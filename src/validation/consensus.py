from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Set

MINIMUM_INDEPENDENT_NODE_SOURCES = 3
ENGINEERING_WARNING_PREFIX = "[ENGINEERING]"

logger = logging.getLogger(__name__)


def _extract_independent_sources(
    records: Iterable[Dict[str, Any]],
    source_key: str = "source",
) -> Set[str]:
    """Return a set of unique source identifiers from a list of records."""
    sources: Set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue

        source_value = record.get(source_key)
        if source_value is None:
            continue

        sources.add(str(source_value))

    return sources


def count_independent_sources(
    records: Iterable[Dict[str, Any]],
    source_key: str = "source",
) -> int:
    """Count unique independent sources reported in the price update records."""
    return len(_extract_independent_sources(records, source_key=source_key))


def enforce_minimum_independent_sources(
    records: Iterable[Dict[str, Any]],
    asset_pair: str,
    source_key: str = "source",
    min_sources: int = MINIMUM_INDEPENDENT_NODE_SOURCES,
) -> None:
    """Reject price updates when there are fewer than the required independent node sources.

    Logs an engineering warning when the source count is below the minimum threshold.
    """
    if records is None:
        raise ValueError("Price update records must be provided")

    sources = _extract_independent_sources(records, source_key=source_key)
    source_count = len(sources)

    if source_count < min_sources:
        logger.warning(
            "%s Insufficient independent node sources for %s: %d provided, minimum required %d",
            ENGINEERING_WARNING_PREFIX,
            asset_pair,
            source_count,
            min_sources,
            extra={
                "asset_pair": asset_pair,
                "source_count": source_count,
                "sources": sorted(sources),
            },
        )
        raise ValueError(
            f"Insufficient independent node sources for {asset_pair}: "
            f"{source_count} provided, minimum {min_sources} required."
        )


def has_minimum_independent_sources(
    records: Iterable[Dict[str, Any]],
    source_key: str = "source",
    min_sources: int = MINIMUM_INDEPENDENT_NODE_SOURCES,
) -> bool:
    """Return True when the price update contains enough independent sources."""
    return count_independent_sources(records, source_key=source_key) >= min_sources


__all__ = [
    "MINIMUM_INDEPENDENT_NODE_SOURCES",
    "count_independent_sources",
    "enforce_minimum_independent_sources",
    "has_minimum_independent_sources",
]
