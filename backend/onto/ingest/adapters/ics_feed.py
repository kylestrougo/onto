"""Tier 2: any venue that publishes an iCalendar feed.

Hand-rolled parsing on purpose: the subset .ics feeds actually use (VEVENT,
DTSTART, SUMMARY, LOCATION, URL, DESCRIPTION) is small, and one more
dependency on the Pi buys little. Folded lines and the common DTSTART
shapes are handled; exotica is skipped, not crashed on.

config_json shape:
  {"url": "https://venue.example/events.ics",
   "borough": "Manhattan",              # feeds rarely say; the admin does
   "category_hint": "culture/live-music",
   "venue_name": "The Example Hall"}    # fallback when LOCATION is absent
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from .. import base
from ..base import RawEvent, strip_html


def _unfold(text: str) -> list[str]:
    """RFC 5545 line unfolding: a line starting with space/tab continues the
    previous one."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _parse_dt(prop_params: str, value: str) -> str:
    """DTSTART value → 'YYYY-MM-DD HH:MM:SS' (UTC when the feed says UTC,
    else the feed's local wall time). Returns '' on shapes we don't know."""
    value = value.strip()
    try:
        if re.fullmatch(r"\d{8}", value):  # VALUE=DATE — all-day
            return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d 00:00:00")
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d %H:%M:%S")
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        m = re.search(r"TZID=([^;:]+)", prop_params)
        if m:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(m.group(1))).astimezone(timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:  # unknown TZID — treat as wall time
                pass
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").strip()
    )


class IcsFeedAdapter:
    def fetch(self, config: dict) -> Iterable[RawEvent]:
        text = base.http_get(config["url"]).text
        in_event = False
        props: dict[str, tuple[str, str]] = {}
        for line in _unfold(text):
            if line == "BEGIN:VEVENT":
                in_event, props = True, {}
                continue
            if line == "END:VEVENT":
                in_event = False
                ev = self._to_event(props, config)
                if ev:
                    yield ev
                continue
            if in_event and ":" in line:
                head, value = line.split(":", 1)
                name, _, params = head.partition(";")
                props[name.upper()] = (params, value)

    def _to_event(self, props, config) -> RawEvent | None:
        summary = _unescape(props.get("SUMMARY", ("", ""))[1])
        dtstart = props.get("DTSTART", ("", ""))
        starts = _parse_dt(*dtstart) if dtstart[1] else ""
        if not summary:
            return None
        # Missing/unparseable start still yields, so the pipeline can count
        # it invalid instead of it vanishing silently.
        dtend = props.get("DTEND", ("", ""))
        uid = _unescape(props.get("UID", ("", ""))[1]) or f"{summary}|{starts[:10]}"
        url = _unescape(props.get("URL", ("", ""))[1]) or config["url"]
        location = _unescape(props.get("LOCATION", ("", ""))[1])
        return RawEvent(
            external_id=uid,
            title=summary,
            url=url,
            starts_at=starts,
            ends_at=_parse_dt(*dtend) or None if dtend[1] else None,
            venue_name=location or config.get("venue_name", ""),
            borough=config.get("borough", ""),
            description=strip_html(_unescape(props.get("DESCRIPTION", ("", ""))[1]))[:2000],
            category_hint=config.get("category_hint", ""),
            raw={"ics_uid": uid},
        )
