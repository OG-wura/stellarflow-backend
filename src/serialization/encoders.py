from __future__ import annotations

"""
src/serialization/encoders.py
==============================
Binary layout encoder for StellarFlow telemetry bundles (Issue #496).

Converts high-frequency structural metrics arrays into dense binary byte
arrays using Python's native ``struct`` library, eliminating the CPU and
bandwidth overhead of JSON serialisation for local microservice communications.

Frame layout (little-endian, tightly-packed — no implicit C-struct padding):
┌─────────────┬────────┬──────────────────────────────────────────────────────┐
│ Field        │ Format │ Description                                          │
├─────────────┼────────┼──────────────────────────────────────────────────────┤
│ asset_id    │  8s    │ 8-byte ASCII asset pair (e.g. b"NGN/XLM\\x00")       │
│ price       │   q    │ int64 scaled price (fixed-point 10⁷)                 │
│ volume      │   Q    │ uint64 24-h rolling volume (scaled 10⁷)              │
│ timestamp   │   Q    │ uint64 Unix epoch milliseconds                       │
│ sequence    │   I    │ uint32 monotonic sequence / nonce counter             │
│ flags       │   H    │ uint16 status-flag bitmask                           │
│ feed_id     │   B    │ uint8  originating data-feed identifier               │
│ _reserved   │   B    │ uint8  reserved byte (always 0x00, for alignment)    │
└─────────────┴────────┴──────────────────────────────────────────────────────┘
Total frame size: 8 + 8 + 8 + 8 + 4 + 2 + 1 + 1 = 40 bytes
"""

import struct
from typing import NamedTuple, Sequence

# Format string & compile-time size
# Using '<' for little-endian standard sizes and no implicit alignment padding.
# The format '<8sqQQIHBB' defines the 40-byte unaligned layout:
# - 8s: 8-byte asset identifier
# - q: 64-bit signed scaled price
# - Q: 64-bit unsigned scaled volume
# - Q: 64-bit unsigned timestamp in ms
# - I: 32-bit unsigned sequence / nonce
# - H: 16-bit unsigned status flags
# - B: 8-bit unsigned data-feed source ID
# - B: 8-bit unsigned reserved byte
_FRAME_STRUCT: struct.Struct = struct.Struct("<8sqQQIHBB")
_FRAME_SIZE: int = _FRAME_STRUCT.size  # 40 bytes

# Status-flag bitmask constants (uint16)
FLAG_LIVE: int = 0x0001       # feed is live / real-time
FLAG_STALE: int = 0x0002      # value has not refreshed within threshold
FLAG_ANOMALY: int = 0x0004    # anomaly-detection alert triggered
FLAG_SYNTHETIC: int = 0x0008  # value is interpolated / synthetic
FLAG_HALTED: int = 0x0010     # asset trading halted


class TelemetryFrame(NamedTuple):
    """Immutable typed container for a single compacted telemetry record.

    All numeric fields use integer fixed-point representations to avoid
    floating-point non-determinism across microservice boundaries.

    Attributes:
        asset_id:  ASCII asset-pair identifier, at most 8 bytes
                   (e.g. ``b"NGN/XLM"``).  Shorter strings are zero-padded
                   during packing and right-stripped during unpacking.
        price:     Signed 64-bit scaled price (multiply by 10⁻⁷ for float).
        volume:    Unsigned 64-bit scaled 24-h rolling volume (×10⁻⁷).
        timestamp: Milliseconds since Unix epoch (uint64).
        sequence:  Monotonically incrementing frame counter (uint32).
        flags:     Status bitmask — combine FLAG_* constants (uint16).
        feed_id:   Originating data-feed identifier byte (uint8, 0–255).
    """

    asset_id: bytes   # at most 8 bytes; padded/stripped automatically
    price: int        # int64 — fixed-point scaled to 10^7
    volume: int       # uint64 — fixed-point scaled to 10^7
    timestamp: int    # uint64 — milliseconds since epoch
    sequence: int     # uint32 — monotonic counter
    flags: int        # uint16 — status bitmask
    feed_id: int      # uint8  — data-feed source identifier


class TelemetryEncoder:
    """
    High-performance, pre-compiled struct packer for telemetry frames.
    Implements a strict struct packing module pattern using Python's native struct.Struct.
    """
    _STRUCT: struct.Struct = _FRAME_STRUCT
    FRAME_SIZE: int = _FRAME_SIZE

    @classmethod
    def pack(cls, frame: TelemetryFrame) -> bytes:
        """
        Pack a TelemetryFrame into a highly compacted, unaligned raw binary byte array.
        """
        asset_bytes = frame.asset_id[:8].ljust(8, b"\x00")
        return cls._STRUCT.pack(
            asset_bytes,
            frame.price,
            frame.volume,
            frame.timestamp,
            frame.sequence,
            frame.flags,
            frame.feed_id,
            0,
        )

    @classmethod
    def unpack(cls, data: bytes) -> TelemetryFrame:
        """
        Unpack raw binary bytes back into a TelemetryFrame.
        """
        unpacked = cls._STRUCT.unpack(data[:cls.FRAME_SIZE])
        return TelemetryFrame(
            asset_id=unpacked[0].rstrip(b"\x00"),
            price=unpacked[1],
            volume=unpacked[2],
            timestamp=unpacked[3],
            sequence=unpacked[4],
            flags=unpacked[5],
            feed_id=unpacked[6],
        )

    @classmethod
    def pack_bundle(cls, frames: Sequence[TelemetryFrame]) -> bytes:
        """
        Pack a sequence of telemetry frames into a single contiguous byte array.
        """
        return b"".join(cls.pack(f) for f in frames)

    @classmethod
    def unpack_bundle(cls, data: bytes) -> list[TelemetryFrame]:
        """
        Unpack a contiguous byte array of packed frames.
        """
        size = cls.FRAME_SIZE
        return [
            cls.unpack(data[offset : offset + size])
            for offset in range(0, len(data), size)
            if len(data) - offset >= size
        ]


def pack_frame(frame: TelemetryFrame) -> bytes:
    """Serialise one :class:`TelemetryFrame` into a compact 40-byte buffer.

    The output is a raw binary byte-string with no delimiters, no length
    prefix, and no JSON overhead — ready for direct socket/queue transmission.

    Args:
        frame: A populated :class:`TelemetryFrame` instance.

    Returns:
        A 40-byte ``bytes`` object representing the packed frame.

    Raises:
        struct.error: If any field value is out of range for its C-type.
    """
    return TelemetryEncoder.pack(frame)


def unpack_frame(data: bytes) -> TelemetryFrame:
    """Deserialise a 40-byte buffer back into a :class:`TelemetryFrame`.

    Only the first ``FRAME_SIZE`` bytes are consumed; trailing bytes are
    silently ignored, which allows safe slicing from a larger buffer.

    Args:
        data: Raw bytes produced by :func:`pack_frame`.  Must be at least
              ``FRAME_SIZE`` (40) bytes long.

    Returns:
        A :class:`TelemetryFrame` with ``asset_id`` right-stripped of
        null-padding bytes.

    Raises:
        struct.error: If ``data`` is shorter than ``FRAME_SIZE``.
    """
    return TelemetryEncoder.unpack(data)


def pack_bundle(frames: Sequence[TelemetryFrame]) -> bytes:
    """Pack a batch of telemetry frames into a single contiguous byte array.

    The bundle has no header or length prefix — it is a simple concatenation
    of fixed-size frame buffers.  Use :func:`unpack_bundle` to reverse.

    Args:
        frames: An ordered sequence of :class:`TelemetryFrame` instances.

    Returns:
        A ``bytes`` object of length ``len(frames) * FRAME_SIZE``.
    """
    return TelemetryEncoder.pack_bundle(frames)


def unpack_bundle(data: bytes) -> list[TelemetryFrame]:
    """Unpack a contiguous byte array produced by :func:`pack_bundle`.

    Trailing bytes that do not constitute a complete frame are silently
    discarded.

    Args:
        data: Raw bytes produced by :func:`pack_bundle`.

    Returns:
        A list of :class:`TelemetryFrame` objects in original order.
    """
    return TelemetryEncoder.unpack_bundle(data)


def bundle_frame_count(data: bytes) -> int:
    """Return the number of complete frames present in a raw bundle buffer.

    Args:
        data: Raw bundle bytes.

    Returns:
        Integer count of decodable frames.
    """
    return len(data) // _FRAME_SIZE


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def encode_asset_id(symbol: str) -> bytes:
    """Encode a human-readable asset-pair symbol into a fixed 8-byte field.

    Args:
        symbol: ASCII string such as ``"NGN/XLM"`` (max 8 chars).

    Returns:
        An 8-byte ``bytes`` object — truncated and zero-padded as needed.
    """
    return symbol.encode("ascii", errors="replace")[:8].ljust(8, b"\x00")


def decode_asset_id(asset_bytes: bytes) -> str:
    """Decode an 8-byte asset-id field back into a readable string.

    Args:
        asset_bytes: Raw bytes from a :class:`TelemetryFrame`.

    Returns:
        ASCII string with null bytes stripped.
    """
    return asset_bytes.rstrip(b"\x00").decode("ascii", errors="replace")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Types
    "TelemetryFrame",
    "TelemetryEncoder",
    # Flag constants
    "FLAG_LIVE",
    "FLAG_STALE",
    "FLAG_ANOMALY",
    "FLAG_SYNTHETIC",
    "FLAG_HALTED",
    # Single-frame codec
    "pack_frame",
    "unpack_frame",
    # Batch codec
    "pack_bundle",
    "unpack_bundle",
    "bundle_frame_count",
    # Helpers
    "encode_asset_id",
    "decode_asset_id",
    # Size constant
    "FRAME_SIZE",
]

#: Exported constant — size in bytes of one packed telemetry frame.
FRAME_SIZE: int = _FRAME_SIZE
