from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_MINIMUM_24H_VOLUME: float = 1_000_000.0
DEFAULT_VOLUME_KEYS: Sequence[str] = (
    "volume_24h",
    "24h_volume",
    "volume24h",
    "volume",
)


def _parse_volume(value: Any) -> Optional[float]:
    """Convert a raw volume value into a usable float, or return None."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)

    try:
        text = str(value).strip()
        if not text:
            return None
        parsed = float(text)
        if math.isnan(parsed):
            return None
        return parsed
    except (ValueError, TypeError):
        return None


def is_volume_sufficient(volume: Any, min_volume: float = DEFAULT_MINIMUM_24H_VOLUME) -> bool:
    """Return True if the supplied 24h volume meets or exceeds the safety threshold."""
    if min_volume < 0:
        raise ValueError("Minimum volume threshold must be non-negative")

    parsed_volume = _parse_volume(volume)
    return parsed_volume is not None and parsed_volume >= min_volume


def filter_record(
    record: Dict[str, Any],
    *,
    min_volume: float = DEFAULT_MINIMUM_24H_VOLUME,
    volume_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the record only if its reported 24h volume meets the minimum threshold."""
    if not isinstance(record, dict):
        return None

    if volume_key is None:
        for candidate in DEFAULT_VOLUME_KEYS:
            if candidate in record:
                volume_key = candidate
                break

    if volume_key is None:
        return None

    if is_volume_sufficient(record.get(volume_key), min_volume=min_volume):
        return record
    return None


def filter_records(
    records: Iterable[Dict[str, Any]],
    *,
    min_volume: float = DEFAULT_MINIMUM_24H_VOLUME,
    volume_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter a batch of records, rejecting those with insufficient 24h volume."""
    return [
        rec
        for rec in (filter_record(record, min_volume=min_volume, volume_key=volume_key) for record in records)
        if rec is not None
    ]


__all__ = [
    "DEFAULT_MINIMUM_24H_VOLUME",
    "DEFAULT_VOLUME_KEYS",
    "is_volume_sufficient",
    "filter_record",
    "filter_records",
]
