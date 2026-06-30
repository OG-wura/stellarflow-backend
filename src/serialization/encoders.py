from __future__ import annotations

"""
src/serialization/encoders.py
==============================
Binary layout encoder for StellarFlow telemetry bundles (Issue #496).

Extended in Issue #613 with :class:`StructPackEncoder` — a strict struct
packing module that converts telemetry payload structures directly into
compacted, unaligned raw binary byte arrays before writing to local IPC
message channels.  All layouts use Python's native ``struct`` library with
no external dependencies.

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
from typing import NamedTuple, Sequence, Union

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



def pack_frame(frame: TelemetryFrame) -> bytes:
    """Serialize a single TelemetryFrame into a FlatBuffers buffer."""
    builder = flatbuffers.Builder(0)
    
    # Create asset_id vector
    asset_bytes = frame.asset_id
    asset_offset = builder.CreateByteVector(asset_bytes)
    
    # Build the TelemetryFrame
    TelemetryFrameStart(builder)
    TelemetryFrameAddAssetId(builder, asset_offset)
    TelemetryFrameAddPrice(builder, frame.price)
    TelemetryFrameAddVolume(builder, frame.volume)
    TelemetryFrameAddTimestamp(builder, frame.timestamp)
    TelemetryFrameAddSequence(builder, frame.sequence)
    TelemetryFrameAddFlags(builder, frame.flags)
    TelemetryFrameAddFeedId(builder, frame.feed_id)
    frame_offset = TelemetryFrameEnd(builder)
    
    builder.Finish(frame_offset)
    return bytes(builder.Output())


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
    """Return the number of frames present in a FlatBuffers bundle buffer."""
    try:
        buf = bytearray(data)
        flat_bundle = FlatTelemetryBundle.GetRootAs(buf, 0)
        return flat_bundle.FramesLength()
    except Exception:
        return 0


def encode_asset_id(symbol: str) -> bytes:
    """Encode a human-readable asset-pair symbol into bytes."""
    return symbol.encode("ascii", errors="replace")


def decode_asset_id(asset_bytes: bytes) -> str:
    """Decode asset_id bytes back into a readable string."""
    return asset_bytes.decode("ascii", errors="replace")



# ---------------------------------------------------------------------------
# Issue #613 — StructPackEncoder: fast struct binary encoders for IPC
# ---------------------------------------------------------------------------
# Additional payload formats written to local message channels.
#
# RingBufferMetric layout (little-endian, no padding):
# ┌──────────────────┬───────┬──────────────────────────────────────────────┐
# │ Field             │ Fmt   │ Description                                  │
# ├──────────────────┼───────┼──────────────────────────────────────────────┤
# │ size             │  Q    │ uint64  current items in buffer               │
# │ capacity         │  Q    │ uint64  total buffer capacity                 │
# │ utilization      │  q    │ int64   utilisation ×10⁷ fixed-point          │
# │ total_enqueued   │  Q    │ uint64  lifetime enqueue count                │
# │ total_dequeued   │  Q    │ uint64  lifetime dequeue count                │
# │ enqueue_failures │  Q    │ uint64  total failed enqueues                 │
# │ dequeue_failures │  Q    │ uint64  total failed dequeues                 │
# │ avg_latency_us   │  q    │ int64   average latency µs ×10⁷              │
# │ peak_latency_us  │  q    │ int64   peak latency µs ×10⁷                 │
# │ batches_processed│  Q    │ uint64  total batches flushed                 │
# └──────────────────┴───────┴──────────────────────────────────────────────┘
# Total: 10 × 8 = 80 bytes
_RBM_FMT: str = "<QQqQQQQqqQ"
_RBM_SIZE: int = struct.calcsize(_RBM_FMT)  # 80 bytes

# BackpressureMetric layout (little-endian, no padding):
# ┌──────────────────────┬───────┬──────────────────────────────────────────┐
# │ Field                 │ Fmt   │ Description                              │
# ├──────────────────────┼───────┼──────────────────────────────────────────┤
# │ queue_length         │  Q    │ uint64  current queue depth               │
# │ max_capacity         │  Q    │ uint64  configured maximum capacity       │
# │ saturation           │  q    │ int64   saturation ratio ×10⁷            │
# │ dropped_packets      │  Q    │ uint64  total dropped packet count        │
# │ slowed_ingestions    │  Q    │ uint64  total throttled ingestion count   │
# │ avg_processing_us    │  q    │ int64   average processing time µs ×10⁷  │
# └──────────────────────┴───────┴──────────────────────────────────────────┘
# Total: 6 × 8 = 48 bytes
_BPM_FMT: str = "<QQqQQq"
_BPM_SIZE: int = struct.calcsize(_BPM_FMT)  # 48 bytes

# IPCHeader layout — prepended to every channel write for framing:
# ┌──────────────────┬───────┬──────────────────────────────────────────────┐
# │ Field             │ Fmt   │ Description                                  │
# ├──────────────────┼───────┼──────────────────────────────────────────────┤
# │ magic            │  H    │ uint16  0xBEEF — sanity marker               │
# │ payload_type     │  B    │ uint8   payload kind (see IPC_TYPE_* consts) │
# │ version          │  B    │ uint8   wire-format version (currently 1)    │
# │ payload_len      │  I    │ uint32  byte length of following payload      │
# │ sequence         │  Q    │ uint64  channel-level monotonic counter      │
# │ timestamp_ms     │  Q    │ uint64  Unix epoch ms at write time          │
# └──────────────────┴───────┴──────────────────────────────────────────────┘
# Total: 2 + 1 + 1 + 4 + 8 + 8 = 24 bytes
_HDR_FMT: str = "<HBBIQQ"
_HDR_SIZE: int = struct.calcsize(_HDR_FMT)  # 24 bytes
_HDR_MAGIC: int = 0xBEEF

# Payload-type identifiers (uint8, stored in header.payload_type)
IPC_TYPE_TELEMETRY_FRAME: int = 0x01    # single TelemetryFrame (40 bytes)
IPC_TYPE_TELEMETRY_BUNDLE: int = 0x02   # concatenated TelemetryFrame bundle
IPC_TYPE_RING_BUFFER_METRIC: int = 0x03 # RingBufferMetric snapshot (80 bytes)
IPC_TYPE_BACKPRESSURE_METRIC: int = 0x04 # BackpressureMetric snapshot (48 bytes)

# Wire-format version
_WIRE_VERSION: int = 1


class RingBufferMetric(NamedTuple):
    """Typed container for a ring-buffer metrics snapshot.

    Numeric floating-point fields are stored as fixed-point int64 (×10⁷)
    so the wire format remains deterministic and free of float rounding.

    Attributes:
        size:               Current number of items in the buffer.
        capacity:           Total buffer capacity.
        utilization:        Utilisation ratio as fixed-point int64 (×10⁷).
        total_enqueued:     Lifetime successful enqueue count.
        total_dequeued:     Lifetime successful dequeue count.
        enqueue_failures:   Lifetime failed enqueue count.
        dequeue_failures:   Lifetime failed dequeue count.
        avg_latency_us:     Average per-item latency in µs as fixed-point int64 (×10⁷).
        peak_latency_us:    Peak per-item latency in µs as fixed-point int64 (×10⁷).
        batches_processed:  Total batch-flush count.
    """

    size: int               # uint64
    capacity: int           # uint64
    utilization: int        # int64, fixed-point ×10⁷
    total_enqueued: int     # uint64
    total_dequeued: int     # uint64
    enqueue_failures: int   # uint64
    dequeue_failures: int   # uint64
    avg_latency_us: int     # int64, fixed-point ×10⁷
    peak_latency_us: int    # int64, fixed-point ×10⁷
    batches_processed: int  # uint64


class BackpressureMetric(NamedTuple):
    """Typed container for a backpressure queue metrics snapshot.

    Attributes:
        queue_length:       Current depth of the bounded queue.
        max_capacity:       Configured maximum capacity.
        saturation:         Saturation ratio as fixed-point int64 (×10⁷).
        dropped_packets:    Total packets dropped due to overflow.
        slowed_ingestions:  Total ingestions throttled by slow-down logic.
        avg_processing_us:  Average processing time in µs as fixed-point int64 (×10⁷).
    """

    queue_length: int       # uint64
    max_capacity: int       # uint64
    saturation: int         # int64, fixed-point ×10⁷
    dropped_packets: int    # uint64
    slowed_ingestions: int  # uint64
    avg_processing_us: int  # int64, fixed-point ×10⁷


# Typed union of all payloads understood by StructPackEncoder
IPCPayload = Union[TelemetryFrame, RingBufferMetric, BackpressureMetric]


class StructPackEncoder:
    """Fast struct binary encoder for internal IPC message-channel writes.

    Translates typed telemetry payload structures into tightly-packed,
    unaligned raw binary byte arrays using Python's native ``struct``
    library — eliminating JSON serialisation overhead on high-frequency
    local message queues.

    Every channel write is prefixed with a fixed 24-byte :data:`_HDR_FMT`
    header that carries a magic marker, payload-type discriminant, payload
    length, a channel-level monotonic sequence counter, and a millisecond
    timestamp.  This makes frames self-describing and allows receivers to
    fast-reject corrupt writes by checking ``_HDR_MAGIC``.

    All numeric float fields (utilization, latencies, saturation) are
    pre-scaled to fixed-point int64 (×10⁷) before packing.

    Usage::

        enc = StructPackEncoder(channel_id=1)

        frame = TelemetryFrame(
            asset_id=b"NGN/XLM",
            price=15_000_000,
            volume=0,
            timestamp=1_700_000_000_000,
            sequence=1,
            flags=FLAG_LIVE,
            feed_id=3,
        )
        ipc_bytes = enc.encode_telemetry_frame(frame)
        channel.write(ipc_bytes)

    Attributes:
        channel_id: Logical channel identifier embedded in log messages.
    """

    _SCALE: int = 10_000_000  # fixed-point 10^7

    def __init__(self, channel_id: int = 0) -> None:
        self.channel_id: int = channel_id
        self._seq: int = 0  # monotonic sequence counter (per-encoder)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        """Return and post-increment the monotonic sequence counter."""
        seq = self._seq
        self._seq += 1
        return seq

    @staticmethod
    def _now_ms() -> int:
        """Return current Unix epoch time in milliseconds (int64)."""
        import time
        return int(time.time() * 1000)

    def _pack_header(self, payload_type: int, payload_len: int) -> bytes:
        """Serialise a 24-byte IPC header into a raw binary buffer.

        Args:
            payload_type: One of the ``IPC_TYPE_*`` constants.
            payload_len:  Byte length of the payload that follows.

        Returns:
            A 24-byte ``bytes`` object.
        """
        return struct.pack(
            _HDR_FMT,
            _HDR_MAGIC,
            payload_type,
            _WIRE_VERSION,
            payload_len,
            self._next_seq(),
            self._now_ms(),
        )

    # ------------------------------------------------------------------
    # Payload encoders
    # ------------------------------------------------------------------

    def encode_telemetry_frame(self, frame: TelemetryFrame) -> bytes:
        """Encode one :class:`TelemetryFrame` with its IPC header.

        Returns a ``FRAME_SIZE + _HDR_SIZE`` (64-byte) buffer: header
        immediately followed by the packed frame payload — no delimiters,
        no length-prefix duplication, ready for direct queue write.

        Args:
            frame: A populated :class:`TelemetryFrame` instance.

        Returns:
            A 64-byte raw binary buffer (24-byte header + 40-byte payload).
        """
        payload = pack_frame(frame)
        return self._pack_header(IPC_TYPE_TELEMETRY_FRAME, len(payload)) + payload

    def encode_telemetry_bundle(self, frames: Sequence[TelemetryFrame]) -> bytes:
        """Encode a batch of :class:`TelemetryFrame` objects with one IPC header.

        The bundle payload is a simple concatenation of fixed-size 40-byte
        frames — identical to :func:`pack_bundle` output — prefixed with a
        single header whose ``payload_len`` covers the entire batch.

        Args:
            frames: An ordered sequence of :class:`TelemetryFrame` instances.

        Returns:
            A ``(24 + len(frames) * 40)``-byte raw binary buffer.
        """
        payload = pack_bundle(frames)
        return self._pack_header(IPC_TYPE_TELEMETRY_BUNDLE, len(payload)) + payload

    def encode_ring_buffer_metric(self, metric: RingBufferMetric) -> bytes:
        """Encode a :class:`RingBufferMetric` snapshot with its IPC header.

        Args:
            metric: A populated :class:`RingBufferMetric` instance.

        Returns:
            A 104-byte raw binary buffer (24-byte header + 80-byte payload).
        """
        payload = struct.pack(
            _RBM_FMT,
            metric.size,
            metric.capacity,
            metric.utilization,
            metric.total_enqueued,
            metric.total_dequeued,
            metric.enqueue_failures,
            metric.dequeue_failures,
            metric.avg_latency_us,
            metric.peak_latency_us,
            metric.batches_processed,
        )
        return self._pack_header(IPC_TYPE_RING_BUFFER_METRIC, len(payload)) + payload

    def encode_backpressure_metric(self, metric: BackpressureMetric) -> bytes:
        """Encode a :class:`BackpressureMetric` snapshot with its IPC header.

        Args:
            metric: A populated :class:`BackpressureMetric` instance.

        Returns:
            A 72-byte raw binary buffer (24-byte header + 48-byte payload).
        """
        payload = struct.pack(
            _BPM_FMT,
            metric.queue_length,
            metric.max_capacity,
            metric.saturation,
            metric.dropped_packets,
            metric.slowed_ingestions,
            metric.avg_processing_us,
        )
        return self._pack_header(IPC_TYPE_BACKPRESSURE_METRIC, len(payload)) + payload

    # ------------------------------------------------------------------
    # Convenience: scale floats to fixed-point before encoding
    # ------------------------------------------------------------------

    @classmethod
    def scale(cls, value: float) -> int:
        """Convert a float to fixed-point int64 (×10⁷).

        Use this before constructing :class:`RingBufferMetric` or
        :class:`BackpressureMetric` fields that carry ratio or latency values.

        Args:
            value: Raw floating-point value.

        Returns:
            Integer representation scaled by 10⁷.
        """
        return round(value * cls._SCALE)

    # ------------------------------------------------------------------
    # Decoder helpers
    # ------------------------------------------------------------------

    @staticmethod
    def decode_header(data: bytes) -> tuple[int, int, int, int, int]:
        """Decode the 24-byte IPC header from the front of a raw buffer.

        Args:
            data: Raw bytes whose first ``_HDR_SIZE`` bytes are the header.

        Returns:
            ``(payload_type, version, payload_len, sequence, timestamp_ms)``

        Raises:
            ValueError:    If the magic marker is absent (corrupt frame).
            struct.error:  If ``data`` is shorter than ``_HDR_SIZE``.
        """
        magic, payload_type, version, payload_len, sequence, timestamp_ms = struct.unpack(
            _HDR_FMT, data[:_HDR_SIZE]
        )
        if magic != _HDR_MAGIC:
            raise ValueError(
                f"Invalid IPC frame magic: expected 0x{_HDR_MAGIC:04X}, "
                f"got 0x{magic:04X}"
            )
        return payload_type, version, payload_len, sequence, timestamp_ms

    @staticmethod
    def decode_ring_buffer_metric(data: bytes) -> RingBufferMetric:
        """Decode a 80-byte payload into a :class:`RingBufferMetric`.

        Args:
            data: The raw payload bytes (without the IPC header).

        Returns:
            A populated :class:`RingBufferMetric` instance.

        Raises:
            struct.error: If ``data`` is shorter than ``_RBM_SIZE``.
        """
        fields = struct.unpack(_RBM_FMT, data[:_RBM_SIZE])
        return RingBufferMetric(*fields)

    @staticmethod
    def decode_backpressure_metric(data: bytes) -> BackpressureMetric:
        """Decode a 48-byte payload into a :class:`BackpressureMetric`.

        Args:
            data: The raw payload bytes (without the IPC header).

        Returns:
            A populated :class:`BackpressureMetric` instance.

        Raises:
            struct.error: If ``data`` is shorter than ``_BPM_SIZE``.
        """
        fields = struct.unpack(_BPM_FMT, data[:_BPM_SIZE])
        return BackpressureMetric(*fields)


# ---------------------------------------------------------------------------
# Public API
__all__ = [
    "TelemetryFrame",
    "RingBufferMetric",
    "BackpressureMetric",
    "IPCPayload",
    # Flag constants
    "FLAG_LIVE",
    "FLAG_STALE",
    "FLAG_ANOMALY",
    "FLAG_SYNTHETIC",
    "FLAG_HALTED",
    # IPC payload-type constants
    "IPC_TYPE_TELEMETRY_FRAME",
    "IPC_TYPE_TELEMETRY_BUNDLE",
    "IPC_TYPE_RING_BUFFER_METRIC",
    "IPC_TYPE_BACKPRESSURE_METRIC",
    # Single-frame codec
    "pack_frame",
    "unpack_frame",
    "pack_bundle",
    "unpack_bundle",
    "bundle_frame_count",
    "encode_asset_id",
    "decode_asset_id",
    # Size constants
    "FRAME_SIZE",
    # Issue #613 — struct-pack IPC encoder
    "StructPackEncoder",
]
