"""kind → adapter. llm_research registers here in Phase 6."""
from __future__ import annotations

from .adapters.ics_feed import IcsFeedAdapter
from .adapters.jsonld_feed import JsonLdFeedAdapter
from .adapters.nyc_open_data import NycOpenDataAdapter

_ADAPTERS = {
    "nyc_open_data": NycOpenDataAdapter,
    "ics": IcsFeedAdapter,
    "jsonld": JsonLdFeedAdapter,
}


def adapter_for(kind: str):
    try:
        return _ADAPTERS[kind]()
    except KeyError:
        raise ValueError(f"no adapter for source kind '{kind}'") from None


def register(kind: str, factory) -> None:
    _ADAPTERS[kind] = factory
