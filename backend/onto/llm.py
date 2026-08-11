"""OpenRouter access — the one interface every LLM call goes through
(spec 12.4), so a model swap never touches application code.

Ported from curio: an admin-editable ordered fallback chain in app_config,
a whole-chain time budget so a run of timing-out models can't pin a thread,
tolerant JSON parsing for what free models actually emit, and a stats row
per attempt so /admin can see what's behaving.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests
from flask import current_app

from .db import execute, query

log = logging.getLogger(__name__)

CONFIG_KEY_CHAIN = "llm_chain"
CONFIG_KEY_OVERRIDES = "llm_overrides"
PARSE_ATTEMPTS_PER_MODEL = 2

# Temperature is a variance dial, not a truth dial (a model can hallucinate
# at 0), so this works alongside the validation layers, not instead of them.
_TEMPERATURES = {
    "research_extract": 0.2,   # facts from a fetched page — no creativity wanted
    "digest": 0.7,             # warm prose around server-verified facts
    # Deliberately mirrors "digest": the benchmark must measure what
    # production runs, or refresh-chain ranks models on the wrong task.
    "bench": 0.7,
    "generic": 0.5,
}


class LLMError(RuntimeError):
    """Every model in the chain failed."""


# ── configuration ───────────────────────────────────────────────────────


def _config_json(key: str, default):
    row = query("SELECT value FROM app_config WHERE key = ?", (key,), one=True)
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return default


def set_config_json(key: str, value) -> None:
    execute(
        "INSERT INTO app_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def get_chain() -> list[str]:
    """The active ordered fallback chain, admin-set, env default until then."""
    chain = _config_json(CONFIG_KEY_CHAIN, None)
    if isinstance(chain, list) and chain:
        return [str(m) for m in chain]
    return list(current_app.config["DEFAULT_MODEL_CHAIN"])


def get_overrides() -> dict:
    """Optional per-intent model override, e.g. a careful model for research."""
    ov = _config_json(CONFIG_KEY_OVERRIDES, {})
    return ov if isinstance(ov, dict) else {}


def chain_for(intent: str) -> list[str]:
    override = get_overrides().get(intent)
    chain = get_chain()
    if override:
        # The override leads; the rest of the chain still backs it up.
        return [override] + [m for m in chain if m != override]
    return chain


def _temperature_for(intent: str) -> float:
    return _TEMPERATURES.get(intent, _TEMPERATURES["generic"])


# ── tolerant parsing ────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)


def parse_json_loose(text: str):
    """Extract a JSON object from whatever the model actually emitted.

    Handles: clean JSON, fenced JSON, JSON with a chatty preamble/suffix, and
    the common free-model habit of emitting smart quotes or a trailing comma.
    Returns a dict, or raises ValueError.
    """
    if not text:
        raise ValueError("empty response")

    cleaned = _FENCE_RE.sub("", text).strip()

    # Prefer the outermost balanced object — models like to append commentary.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found")
    candidate = cleaned[start : end + 1]

    for attempt in (candidate, _repair(candidate)):
        try:
            parsed = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("unparseable JSON object")


def _repair(s: str) -> str:
    """Best-effort cleanup of the mistakes small models actually make."""
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("‘", "'").replace("’", "'")
    # Trailing commas before a close.
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # Literal newlines inside string values break json.loads; escape them.
    out, in_string, escaped = [], False, False
    for ch in s:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
        if in_string and ch == "\n":
            out.append("\\n")
            continue
        out.append(ch)
    return "".join(out)


# ── stats ───────────────────────────────────────────────────────────────


def _record(model: str, intent: str, ok: bool, latency_ms: int | None, error: str | None) -> None:
    try:
        execute(
            "INSERT INTO model_stats (model, intent, ok, latency_ms, error) VALUES (?, ?, ?, ?, ?)",
            (model, intent, 1 if ok else 0, latency_ms, (error or "")[:300] or None),
        )
    except Exception:  # stats must never break a user-facing call
        log.exception("failed to record model stats")


# ── the call ────────────────────────────────────────────────────────────


def _post(
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    json_mode: bool,
    temperature: float | None = None,
    timeout: float | None = None,
) -> str:
    cfg = current_app.config
    key = cfg["OPENROUTER_API_KEY"]
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set")

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if temperature is not None:
        body["temperature"] = temperature
    if json_mode:
        # Sent when we think the model supports it — but never relied upon.
        body["response_format"] = {"type": "json_object"}

    res = requests.post(
        f"{cfg['OPENROUTER_BASE']}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": cfg["PUBLIC_URL"],
            "X-Title": "Onto",
        },
        json=body,
        timeout=timeout if timeout is not None else cfg["OPENROUTER_TIMEOUT"],
    )
    if res.status_code != 200:
        raise LLMError(f"HTTP {res.status_code}: {res.text[:200]}")

    data = res.json()
    if "error" in data and not data.get("choices"):
        raise LLMError(str(data["error"])[:200])
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"unexpected response shape: {str(data)[:200]}")


def generate(
    system: str,
    user: str,
    intent: str = "generic",
    max_tokens: int = 1000,
    models: list[str] | None = None,
) -> dict:
    """Run the chain until one model returns parseable JSON.

    Each model gets two attempts (the second without response_format), a
    transport failure falls straight through to the next model, and one
    deadline covers the whole chain.
    """
    chain = models if models is not None else chain_for(intent)
    if not chain:
        raise LLMError("no models configured")

    temperature = _temperature_for(intent)
    deadline = time.monotonic() + current_app.config["GENERATION_BUDGET"]
    last_error = "no models attempted"
    for model in chain:
        for attempt in range(PARSE_ATTEMPTS_PER_MODEL):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMError(f"generation budget exhausted; last: {last_error}")
            started = time.monotonic()
            try:
                raw = _post(
                    model, system, user, max_tokens,
                    json_mode=(attempt == 0), temperature=temperature,
                    timeout=min(current_app.config["OPENROUTER_TIMEOUT"], remaining),
                )
            except (LLMError, requests.RequestException) as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                _record(model, intent, False, elapsed, last_error)
                log.warning("model %s failed (%s) — falling through", model, last_error)
                break  # transport/quota problem: don't retry this model, move on

            elapsed = int((time.monotonic() - started) * 1000)
            try:
                parsed = parse_json_loose(raw)
            except ValueError as exc:
                last_error = f"unparseable: {exc}"
                _record(model, intent, False, elapsed, last_error)
                continue  # second chance for this model, without json_mode

            _record(model, intent, True, elapsed, None)
            return parsed

    raise LLMError(last_error)


def generate_raw(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1000,
    intent: str = "admin_test",
) -> dict:
    """One model, one attempt, no chain, no raise — the probe behind the
    admin test button and the refresh-chain benchmark.

    Returns {ok, raw, parsed, latency_ms, error} and records the attempt to
    model_stats whatever happens, so probes show up in the same health view
    as production calls.
    """
    started = time.monotonic()
    try:
        raw = _post(
            model, system, user, max_tokens,
            json_mode=True, temperature=_temperature_for(intent),
        )
    except (LLMError, requests.RequestException) as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        error = f"{type(exc).__name__}: {exc}"[:300]
        _record(model, intent, False, elapsed, error)
        return {"ok": False, "raw": None, "parsed": None, "latency_ms": elapsed, "error": error}

    elapsed = int((time.monotonic() - started) * 1000)
    try:
        parsed = parse_json_loose(raw)
    except ValueError as exc:
        error = f"unparseable: {exc}"
        _record(model, intent, False, elapsed, error)
        return {"ok": False, "raw": raw, "parsed": None, "latency_ms": elapsed, "error": error}

    _record(model, intent, True, elapsed, None)
    return {"ok": True, "raw": raw, "parsed": parsed, "latency_ms": elapsed, "error": None}


def list_free_models() -> list[dict]:
    """OpenRouter catalogue, filtered to the free variants.

    Unauthenticated on purpose — /models is public — and the ':free' id
    suffix is the entire definition of "free" here.
    """
    cfg = current_app.config
    res = requests.get(f"{cfg['OPENROUTER_BASE']}/models", timeout=20)
    res.raise_for_status()
    models = []
    for m in res.json().get("data", []):
        mid = m.get("id", "")
        if not mid.endswith(":free"):
            continue
        models.append(
            {
                "id": mid,
                "name": m.get("name", mid),
                "context_length": m.get("context_length"),
                "description": (m.get("description") or "")[:300],
            }
        )
    models.sort(key=lambda m: m["id"])
    return models


def stats_rollup(days: int = 7) -> list[dict]:
    """Per-model success rate and latency percentiles over a recent window.

    Latencies come from successful calls only; last_error is the most recent
    failure. Sorted best-behaved first.
    """
    rows = query(
        "SELECT model, ok, latency_ms, error FROM model_stats"
        " WHERE created_at >= datetime('now', ?) ORDER BY created_at DESC",
        (f"-{int(days)} days",),
    )
    by_model: dict[str, dict] = {}
    for r in rows:
        s = by_model.setdefault(
            r["model"],
            {"model": r["model"], "calls": 0, "ok": 0, "latencies": [], "last_error": None},
        )
        s["calls"] += 1
        if r["ok"]:
            s["ok"] += 1
            if r["latency_ms"] is not None:
                s["latencies"].append(r["latency_ms"])
        elif s["last_error"] is None:
            s["last_error"] = r["error"]  # rows are newest-first

    out = []
    for s in by_model.values():
        lat = sorted(s["latencies"])
        out.append(
            {
                "model": s["model"],
                "calls": s["calls"],
                "ok_rate": round(s["ok"] / s["calls"], 3) if s["calls"] else 0.0,
                "p50_ms": lat[len(lat) // 2] if lat else None,
                "p95_ms": lat[max(0, int(len(lat) * 0.95) - 1)] if lat else None,
                "last_error": s["last_error"],
            }
        )
    out.sort(key=lambda s: (-s["ok_rate"], s["model"]))
    return out
