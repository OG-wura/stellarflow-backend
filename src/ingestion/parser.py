"""High-throughput websocket ticker flattening for analytics ingestion.

Websocket providers often wrap ticker frames in several layers of schema
metadata (for example: ``{"data": {"ticker": {...}}}`` or batched
``{"frames": [...]}``). This module collapses those nested payloads into
uniform, flat tuple segments so the analytics engine can consume a stable
shape with minimal Python object churn.

SIMD Acceleration
-----------------
When ``pysimdjson`` is installed (``pip install pysimdjson``), the raw JSON
decode step is handled by a thread-local ``simdjson.Parser`` that exploits
SIMD vectorization (SSE4.2 / AVX2 / AVX-512 depending on host CPU).  The
parser is reused across calls on the same OS thread to avoid repeated C++
heap allocations.

If the native extension is not available the module falls back silently to
``json.loads``, keeping the pipeline functional in any environment.  The
:data:`SIMDJSON_AVAILABLE` flag is exported so callers can introspect which
path is active at runtime.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Iterator, Mapping
from typing import TypeAlias, cast

# ---------------------------------------------------------------------------
# SIMD-accelerated JSON back-end (optional)
# ---------------------------------------------------------------------------
try:
    import simdjson as _simdjson  # type: ignore[import-untyped]

    # Thread-local storage: each worker thread gets its own Parser instance so
    # concurrent ingestion goroutines cannot race on shared C++ parser state.
    _tl = threading.local()

    def _simd_decode(raw: bytes) -> object:
        """Decode *raw* bytes via simdjson, reusing the per-thread Parser.

        Returns a ``Mapping``-compatible proxy object backed by the C++
        simdjson document.  The proxy supports ``__getitem__``, ``.get()``,
        and the ``collections.abc.Mapping`` protocol, so the rest of this
        module's traversal logic requires no changes.

        Thread safety: each OS thread owns a dedicated ``simdjson.Parser``
        instance stored in ``threading.local()``.
        """
        parser: _simdjson.Parser = getattr(_tl, "parser", None)
        if parser is None:
            parser = _simdjson.Parser()
            _tl.parser = parser
        return parser.parse(raw)

    SIMDJSON_AVAILABLE: bool = True

except ImportError:  # pragma: no cover — exercised by test_parser_fallback_path
    def _simd_decode(raw: bytes) -> object:  # type: ignore[misc]
        """Fallback: standard-library JSON decode."""
        return json.loads(raw)

    SIMDJSON_AVAILABLE = False

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

TelemetryTuple: TypeAlias = tuple[str, float, int, int, int]
TelemetrySegment: TypeAlias = tuple[TelemetryTuple, ...]
TelemetrySegmentBatch: TypeAlias = tuple[TelemetrySegment, ...]
SequencePayload: TypeAlias = list[object] | tuple[object, ...]

DEFAULT_SEGMENT_SIZE = 256

# ---------------------------------------------------------------------------
# Field-name look-up tables (ordered by likelihood of appearance)
# ---------------------------------------------------------------------------

_ASSET_KEYS = ("asset_id", "asset", "symbol", "pair", "instrument")
_PRICE_KEYS = ("price", "last_price", "last", "mark_price", "value")
_TIMESTAMP_KEYS = ("timestamp", "ts", "time", "event_time")
_SEQUENCE_KEYS = ("sequence", "seq", "nonce")
_FLAGS_KEYS = ("flags", "status_flags", "flag_bits")

_BATCH_KEYS = ("frames", "ticks", "tickers", "telemetry", "updates", "results")
_CONTAINER_KEYS = ("data", "payload", "result")
_FRAME_KEYS = ("frame", "tick", "ticker", "telemetry")

_NON_STRING_SEQUENCES = (list, tuple)
_MISSING = object()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _first_present(
    mapping: Mapping[str, object],
    keys: tuple[str, ...],
    default: object = _MISSING,
) -> object:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _has_any(mapping: Mapping[str, object], keys: tuple[str, ...]) -> bool:
    return _first_present(mapping, keys) is not _MISSING


def _coerce_asset(value: object) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii")
    elif not isinstance(value, str):
        raise TypeError(
            f"asset identifier must be str or bytes, got {type(value).__name__}"
        )

    asset = value.strip().upper()
    if not asset:
        raise ValueError("asset identifier must not be empty")
    return asset


def _coerce_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} must not be empty")
        return float(text)
    raise TypeError(f"{field_name} must be numeric, got {type(value).__name__}")


def _coerce_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field_name} must be an integer value")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} must not be empty")
        return int(text, 10)
    raise TypeError(f"{field_name} must be an integer, got {type(value).__name__}")


def _looks_like_frame(mapping: Mapping[str, object]) -> bool:
    return (
        _has_any(mapping, _ASSET_KEYS)
        and _has_any(mapping, _PRICE_KEYS)
        and _has_any(mapping, _TIMESTAMP_KEYS)
    )


def _flatten_frame(mapping: Mapping[str, object]) -> TelemetryTuple:
    asset_value = _first_present(mapping, _ASSET_KEYS)
    price_value = _first_present(mapping, _PRICE_KEYS)
    timestamp_value = _first_present(mapping, _TIMESTAMP_KEYS)
    sequence_value = _first_present(mapping, _SEQUENCE_KEYS, 0)
    flags_value = _first_present(mapping, _FLAGS_KEYS, 0)

    if asset_value is _MISSING:
        raise ValueError("ticker frame is missing asset identifier")
    if price_value is _MISSING:
        raise ValueError("ticker frame is missing price")
    if timestamp_value is _MISSING:
        raise ValueError("ticker frame is missing timestamp")

    return (
        _coerce_asset(asset_value),
        _coerce_float(price_value, "price"),
        _coerce_int(timestamp_value, "timestamp"),
        _coerce_int(sequence_value, "sequence"),
        _coerce_int(flags_value, "flags"),
    )


def _as_sequence(value: object) -> SequencePayload | None:
    if isinstance(value, _NON_STRING_SEQUENCES):
        return cast(SequencePayload, value)
    return None


# ---------------------------------------------------------------------------
# Public API — ingestion entry point
# ---------------------------------------------------------------------------


def parse_raw_pack(
    raw: bytes,
    *,
    drop_invalid: bool = False,
) -> TelemetrySegment:
    """Decode a raw JSON B2C pack and flatten all contained ticker frames.

    This is the primary ingestion entry point for incoming binary payloads
    received over websocket or REST transport.  The raw bytes are decoded
    using the SIMD-accelerated back-end when ``pysimdjson`` is available,
    falling back to ``json.loads`` otherwise.

    Parameters
    ----------
    raw:
        Raw JSON-encoded bytes.  Must be a single JSON value (object or
        array) — newline delimiting is handled upstream by
        :class:`~ingestion.stream_buffer.StreamBuffer`.
    drop_invalid:
        When ``True`` frames that fail field extraction are silently skipped.
        When ``False`` (the default) the first malformed frame raises.

    Returns
    -------
    TelemetrySegment
        Immutable tuple of :data:`TelemetryTuple` records in the order they
        were encountered inside *raw*.

    Raises
    ------
    ValueError
        If *raw* is empty.
    json.JSONDecodeError / simdjson.InvalidJSON
        If *raw* is not valid JSON.
    """
    if not raw:
        raise ValueError("raw pack must not be empty")

    decoded = _simd_decode(raw)

    # A top-level array is unpacked as individual payloads; any other value
    # (object, scalar) is wrapped in a list so the flattening loop sees a
    # uniform iterable.
    payloads: list[object] = list(decoded) if isinstance(decoded, (list, tuple)) else [decoded]
    return flatten_telemetry_frames(payloads, drop_invalid=drop_invalid)


# ---------------------------------------------------------------------------
# Public API — streaming / iterator helpers
# ---------------------------------------------------------------------------


def iter_flat_ticker_tuples(
    payloads: Iterable[object],
    *,
    drop_invalid: bool = False,
) -> Iterator[TelemetryTuple]:
    """Yield flat ticker tuples from nested websocket payloads.

    Each yielded tuple has the shape ``(asset_id, price, timestamp, sequence,
    flags)``. Optional ``sequence`` and ``flags`` values default to ``0`` when
    they are not present in the incoming frame.
    """
    for payload in payloads:
        stack: list[object] = [payload]

        while stack:
            current = stack.pop()

            if current is None:
                continue

            if isinstance(current, Mapping):
                mapping = cast(Mapping[str, object], current)

                if _looks_like_frame(mapping):
                    try:
                        yield _flatten_frame(mapping)
                    except (TypeError, ValueError):
                        if not drop_invalid:
                            raise
                    continue

                expanded = False

                for key in _BATCH_KEYS:
                    sequence = _as_sequence(mapping.get(key))
                    if sequence is not None:
                        stack.extend(reversed(sequence))
                        expanded = True

                for key in _CONTAINER_KEYS:
                    value = mapping.get(key)
                    if isinstance(value, Mapping):
                        stack.append(cast(Mapping[str, object], value))
                        expanded = True
                        continue

                    sequence = _as_sequence(value)
                    if sequence is not None:
                        stack.extend(reversed(sequence))
                        expanded = True

                for key in _FRAME_KEYS:
                    value = mapping.get(key)
                    if isinstance(value, Mapping):
                        stack.append(cast(Mapping[str, object], value))
                        expanded = True

                if expanded or drop_invalid:
                    continue

                raise ValueError(f"Unsupported ticker payload shape: {mapping!r}")

            else:
                sequence = _as_sequence(current)
                if sequence is not None:
                    stack.extend(reversed(sequence))
                elif not drop_invalid:
                    raise TypeError(
                        f"Unsupported ticker payload type: {type(current).__name__}"
                    )


def flatten_telemetry_frames(
    payloads: Iterable[object],
    *,
    drop_invalid: bool = False,
) -> TelemetrySegment:
    """Return all parsed websocket ticker frames as a flat immutable tuple."""
    return tuple(iter_flat_ticker_tuples(payloads, drop_invalid=drop_invalid))


def build_telemetry_segments(
    payloads: Iterable[object],
    *,
    segment_size: int = DEFAULT_SEGMENT_SIZE,
    drop_invalid: bool = False,
) -> TelemetrySegmentBatch:
    """Group flat ticker tuples into fixed-size immutable segments.

    Segmenting batches keeps the downstream analytics handoff uniform while also
    avoiding a single oversized container during heavy websocket bursts.
    """
    if segment_size <= 0:
        raise ValueError("segment_size must be greater than zero")

    segments: list[TelemetrySegment] = []
    current: list[TelemetryTuple] = []

    for frame in iter_flat_ticker_tuples(payloads, drop_invalid=drop_invalid):
        current.append(frame)
        if len(current) == segment_size:
            segments.append(tuple(current))
            current = []

    if current:
        segments.append(tuple(current))

    return tuple(segments)


__all__ = [
    "DEFAULT_SEGMENT_SIZE",
    "SIMDJSON_AVAILABLE",
    "TelemetrySegment",
    "TelemetrySegmentBatch",
    "TelemetryTuple",
    "build_telemetry_segments",
    "flatten_telemetry_frames",
    "iter_flat_ticker_tuples",
    "parse_raw_pack",
]
