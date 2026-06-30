"""
src/serialization/fast_pack.py
================================
Custom C-Based Byte Serialization Builders for Fast Hashing (Issue #476).

Converts telemetry parameters directly into flat binary blocks using
``struct.pack`` — bypassing slow reflection-based encoders to maximize
cryptographic throughput.

All layouts are little-endian, tightly-packed (no implicit C-struct padding),
and deterministic so the same input always produces the same byte sequence for
hashing pipelines.

Supported layouts
-----------------
- :func:`pack_price_tick`      — 32-byte price-update hash block
- :func:`pack_oracle_payload`  — 48-byte oracle submission hash block
- :func:`pack_feed_event`      — 24-byte lightweight feed-event hash block
- :func:`pack_batch_hash_input` — variable-length concatenated block
"""

from __future__ import annotations

import struct
from typing import Sequence

# ---------------------------------------------------------------------------
# Format strings (little-endian, no padding)
# ---------------------------------------------------------------------------

# Price tick: asset(8s) | price_scaled(q) | timestamp_ms(Q) | sequence(I) | feed_id(B) | flags(H) | _pad(B)
# 8 + 8 + 8 + 4 + 1 + 2 + 1 = 32 bytes
_PRICE_TICK_FMT: str = "<8sqQIBHB"
_PRICE_TICK_SIZE: int = struct.calcsize(_PRICE_TICK_FMT)  # 32

# Oracle payload: asset(8s) | price(q) | bid(q) | ask(q) | timestamp_ms(Q) | sequence(I) | provider_id(H) | flags(H)
# 8 + 8 + 8 + 8 + 8 + 4 + 2 + 2 = 48 bytes
_ORACLE_PAYLOAD_FMT: str = "<8sqqqQIHH"
_ORACLE_PAYLOAD_SIZE: int = struct.calcsize(_ORACLE_PAYLOAD_FMT)  # 48

# Feed event: timestamp_ms(Q) | event_type(B) | feed_id(B) | value_scaled(q) | sequence(I)
# 8 + 1 + 1 + 8 + 4 = 22 bytes → pad to 24 with 2 reserved bytes for alignment
_FEED_EVENT_FMT: str = "<QBBqIH"
_FEED_EVENT_SIZE: int = struct.calcsize(_FEED_EVENT_FMT)  # 24

# ---------------------------------------------------------------------------
# Scale factor (fixed-point 10^7 — consistent with encoders.py)
# ---------------------------------------------------------------------------
_SCALE: int = 10_000_000


def _scale(value: float) -> int:
    """Convert a float to fixed-point int64 (×10⁷), avoiding float non-determinism."""
    return round(value * _SCALE)


def _asset_bytes(symbol: str) -> bytes:
    """Encode asset symbol to a fixed 8-byte field, zero-padded."""
    return symbol.encode("ascii", errors="replace")[:8].ljust(8, b"\x00")


# ---------------------------------------------------------------------------
# pack_price_tick — 32-byte price hash block
# ---------------------------------------------------------------------------
def pack_price_tick(
    asset: str,
    price: float,
    timestamp_ms: int,
    sequence: int,
    feed_id: int,
    flags: int = 0,
) -> bytes:
    """Pack a price-update tick into a 32-byte flat binary block.

    Designed as the minimal hash input for a single price event. The layout is
    deterministic and contains no JSON overhead.

    Args:
        asset:        Asset-pair symbol, e.g. ``"NGN/XLM"`` (max 8 chars).
        price:        Mid-price as a Python float; scaled to int64 internally.
        timestamp_ms: Unix timestamp in milliseconds (uint64).
        sequence:     Monotonic sequence counter (uint32).
        feed_id:      Data-feed source byte identifier (uint8, 0–255).
        flags:        Status bitmask (uint16); use FLAG_* constants as needed.

    Returns:
        A 32-byte ``bytes`` object ready to pass directly to ``hashlib``.
    """
    return struct.pack(
        _PRICE_TICK_FMT,
        _asset_bytes(asset),
        _scale(price),
        timestamp_ms,
        sequence,
        feed_id,
        flags,
        0x00,  # reserved pad byte
    )


# ---------------------------------------------------------------------------
# pack_oracle_payload — 48-byte oracle submission hash block
# ---------------------------------------------------------------------------
def pack_oracle_payload(
    asset: str,
    price: float,
    bid: float,
    ask: float,
    timestamp_ms: int,
    sequence: int,
    provider_id: int,
    flags: int = 0,
) -> bytes:
    """Pack a full oracle submission into a 48-byte flat binary block.

    Captures the complete price spread context needed for on-chain verification
    hashing without any reflective encoding overhead.

    Args:
        asset:        Asset-pair symbol (max 8 chars).
        price:        Mid-price float; scaled ×10⁷ to int64.
        bid:          Bid price float; scaled ×10⁷ to int64.
        ask:          Ask price float; scaled ×10⁷ to int64.
        timestamp_ms: Unix timestamp in milliseconds (uint64).
        sequence:     Monotonic sequence counter (uint32).
        provider_id:  Data-provider identifier (uint16, 0–65535).
        flags:        Status bitmask (uint16).

    Returns:
        A 48-byte ``bytes`` object.
    """
    return struct.pack(
        _ORACLE_PAYLOAD_FMT,
        _asset_bytes(asset),
        _scale(price),
        _scale(bid),
        _scale(ask),
        timestamp_ms,
        sequence,
        provider_id,
        flags,
    )


# ---------------------------------------------------------------------------
# pack_feed_event — 24-byte lightweight event hash block
# ---------------------------------------------------------------------------
def pack_feed_event(
    timestamp_ms: int,
    event_type: int,
    feed_id: int,
    value: float,
    sequence: int,
) -> bytes:
    """Pack a lightweight feed event into a 24-byte flat binary block.

    Optimized for high-frequency ingestion paths where only the event value
    and metadata are required for hashing (e.g. anomaly triggers, staleness).

    Args:
        timestamp_ms: Unix timestamp in milliseconds (uint64).
        event_type:   Event-type byte identifier (uint8).
        feed_id:      Data-feed source byte (uint8).
        value:        Associated numeric value; scaled ×10⁷ to int64.
        sequence:     Monotonic sequence counter (uint32).

    Returns:
        A 24-byte ``bytes`` object.
    """
    return struct.pack(
        _FEED_EVENT_FMT,
        timestamp_ms,
        event_type,
        feed_id,
        _scale(value),
        sequence,
        0x0000,  # reserved 2-byte pad for word alignment
    )


# ---------------------------------------------------------------------------
# pack_batch_hash_input — variable-length concatenated block
# ---------------------------------------------------------------------------
def pack_batch_hash_input(raw_blocks: Sequence[bytes]) -> bytes:
    """Concatenate pre-packed binary blocks into a single contiguous buffer.

    Use this when hashing an entire ingestion batch in one pass. Each element
    in ``raw_blocks`` must be produced by one of the ``pack_*`` functions above.

    Args:
        raw_blocks: Sequence of fixed-size byte blocks (32, 48, or 24 bytes).

    Returns:
        A ``bytes`` object that is the concatenation of all input blocks.
    """
    return b"".join(raw_blocks)


# ---------------------------------------------------------------------------
# Size constants (exported for consumers)
# ---------------------------------------------------------------------------
PRICE_TICK_SIZE: int = _PRICE_TICK_SIZE      # 32 bytes
ORACLE_PAYLOAD_SIZE: int = _ORACLE_PAYLOAD_SIZE  # 48 bytes
FEED_EVENT_SIZE: int = _FEED_EVENT_SIZE      # 24 bytes

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "pack_price_tick",
    "pack_oracle_payload",
    "pack_feed_event",
    "pack_batch_hash_input",
    "PRICE_TICK_SIZE",
    "ORACLE_PAYLOAD_SIZE",
    "FEED_EVENT_SIZE",
]
