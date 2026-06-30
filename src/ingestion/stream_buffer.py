"""stream_buffer.py — zero-copy JSON stream parser with SIMD acceleration.

Accepts raw network binary blocks and locates newline-delimited JSON frames
without allocating intermediate string objects, reducing GC pressure during
high-volume market-volatility spikes.

When ``pysimdjson`` is installed the per-frame decode step is handled by a
thread-local ``simdjson.Parser`` instance, which exploits SIMD vectorization
(SSE4.2 / AVX2 / AVX-512) and avoids repeated C++ allocations by reusing the
same parser object across frames on each OS thread.  If the native extension is
not available the module falls back transparently to the standard ``json``
library so the pipeline remains functional in any environment.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Generator

# ---------------------------------------------------------------------------
# SIMD-accelerated JSON back-end (optional)
# ---------------------------------------------------------------------------
try:
    import simdjson as _simdjson  # type: ignore[import-untyped]

    # Each thread gets its own Parser so concurrent ingestion workers don't
    # race on a shared C++ parser state.
    _local = threading.local()

    def _decode(raw: bytes) -> Any:
        """Decode *raw* bytes via simdjson, reusing the per-thread Parser."""
        parser: _simdjson.Parser = getattr(_local, "parser", None)
        if parser is None:
            parser = _simdjson.Parser()
            _local.parser = parser
        # parse() returns a Mapping-compatible C++ proxy — no full Python dict
        # is materialised unless the caller explicitly iterates all keys.
        return parser.parse(raw)

    SIMDJSON_AVAILABLE: bool = True

except ImportError:  # pragma: no cover — covered by fallback-path tests
    def _decode(raw: bytes) -> Any:  # type: ignore[misc]
        """Fallback: standard library JSON decode."""
        return json.loads(raw)

    SIMDJSON_AVAILABLE = False

# ---------------------------------------------------------------------------

_NEWLINE = ord("\n")
_DEFAULT_BUFFER_SIZE = 64 * 1024

# ---------------------------------------------------------------------------
# Memory-mapped sink constants
# ---------------------------------------------------------------------------

# Default pre-allocated file size: 256 MiB.  The ring buffer wraps around
# when the write cursor reaches the end so no re-allocation is ever needed.
_DEFAULT_MAP_SIZE: int = 256 * 1024 * 1024  # 256 MiB

# Header layout (written at offset 0, never part of the payload region):
#   [0:8]   magic  b"SFMMAP\x00\x01"
#   [8:16]  write_cursor (uint64, little-endian) — next byte to write
#   [16:24] wrap_count   (uint64, little-endian) — times the ring wrapped
_HEADER_SIZE: int = 24
_MAGIC: bytes = b"SFMMAP\x00\x01"
_CURSOR_OFFSET: int = 8
_WRAP_OFFSET: int = 16

# Minimum free space in the usable payload region to trigger a wrap.
# If a frame is larger than this we fall back to a truncated write.
_MIN_WRITE_UNIT: int = 4096


class MmapLogSink:
    """Pre-allocated memory-mapped ring buffer for zero-copy payload logging.

    The file is created (or re-opened) at *path* and pre-allocated to
    *map_size* bytes.  The first ``_HEADER_SIZE`` bytes store a small binary
    header so the cursor position survives a process restart.

    All public methods are thread-safe.
    """

    __slots__ = (
        "_path",
        "_map_size",
        "_payload_size",
        "_fd",
        "_mm",
        "_cursor",
        "_wrap_count",
        "_lock",
        "_closed",
    )

    def __init__(
        self,
        path: str | os.PathLike[str],
        map_size: int = _DEFAULT_MAP_SIZE,
    ) -> None:
        self._path = Path(path)
        self._map_size = map_size
        self._payload_size = map_size - _HEADER_SIZE
        self._lock = threading.Lock()
        self._closed = False

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd, self._mm = self._open_or_create()
        self._cursor, self._wrap_count = self._read_header()

        logger.debug(
            "MmapLogSink initialised — path=%s map_size=%d cursor=%d wraps=%d",
            self._path,
            self._map_size,
            self._cursor,
            self._wrap_count,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_or_create(self) -> tuple[int, mmap.mmap]:
        """Open or create the backing file, ensuring it has the right size."""
        existed = self._path.exists()
        fd = os.open(
            str(self._path),
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        try:
            current_size = os.fstat(fd).st_size
            if current_size < self._map_size:
                # Pre-allocate by extending the file with zero bytes.
                os.ftruncate(fd, self._map_size)

            mm = mmap.mmap(fd, self._map_size, access=mmap.ACCESS_WRITE)
        except Exception:
            os.close(fd)
            raise

        if not existed or current_size < _HEADER_SIZE:
            # Brand-new file — write magic + zero cursor.
            mv = memoryview(mm)
            mv[0:8] = _MAGIC
            mv[_CURSOR_OFFSET : _CURSOR_OFFSET + 8] = struct.pack("<Q", 0)
            mv[_WRAP_OFFSET : _WRAP_OFFSET + 8] = struct.pack("<Q", 0)
            del mv
            mm.flush()

        return fd, mm

    def _read_header(self) -> tuple[int, int]:
        """Read the write-cursor and wrap-count from the file header.

        If the magic bytes are absent the header is considered corrupt and
        the cursor is reset to zero (data from a previous run is left intact
        but will be overwritten from the start of the payload region).
        """
        mv = memoryview(self._mm)
        magic = bytes(mv[0:8])
        if magic != _MAGIC:
            logger.warning(
                "MmapLogSink: header magic mismatch at %s — resetting cursor",
                self._path,
            )
            mv[0:8] = _MAGIC
            mv[_CURSOR_OFFSET : _CURSOR_OFFSET + 8] = struct.pack("<Q", 0)
            mv[_WRAP_OFFSET : _WRAP_OFFSET + 8] = struct.pack("<Q", 0)
            del mv
            return 0, 0

        cursor = struct.unpack("<Q", bytes(mv[_CURSOR_OFFSET : _CURSOR_OFFSET + 8]))[0]
        wraps = struct.unpack("<Q", bytes(mv[_WRAP_OFFSET : _WRAP_OFFSET + 8]))[0]
        del mv
        # Guard against out-of-range cursor from a truncated / partial write.
        if cursor >= self._payload_size:
            cursor = 0
        return cursor, wraps

    def _flush_header(self, mv: memoryview) -> None:
        """Persist cursor + wrap-count into the header region."""
        mv[_CURSOR_OFFSET : _CURSOR_OFFSET + 8] = struct.pack("<Q", self._cursor)
        mv[_WRAP_OFFSET : _WRAP_OFFSET + 8] = struct.pack("<Q", self._wrap_count)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_batch(self, frames: list[bytes]) -> None:
        """Write a batch of raw frame bytes into the ring buffer.

        Each frame is appended verbatim; a newline separator is written
        between frames so the log file remains newline-delimited and can be
        replayed by ``StreamBuffer``.  A single ``mmap.flush()`` call covers
        the entire batch, keeping syscall overhead proportional to batch size
        rather than frame count.
        """
        if self._closed or not frames:
            return

        with self._lock:
            mv = memoryview(self._mm)
            payload_start = _HEADER_SIZE

            for raw in frames:
                # Ensure the frame ends with a newline for replay compatibility.
                entry: bytes = raw if raw.endswith(b"\n") else raw + b"\n"
                entry_len = len(entry)

                if entry_len > self._payload_size:
                    # Pathological frame — truncate rather than refuse.
                    entry = entry[: self._payload_size - 1] + b"\n"
                    entry_len = len(entry)

                write_pos = payload_start + self._cursor

                if self._cursor + entry_len <= self._payload_size:
                    # Fast path: frame fits without wrapping.
                    mv[write_pos : write_pos + entry_len] = entry
                    self._cursor += entry_len
                else:
                    # Ring wrap: write from cursor to end, then continue from
                    # the start of the payload region.
                    tail_space = self._payload_size - self._cursor
                    mv[write_pos : write_pos + tail_space] = entry[:tail_space]
                    remainder = entry[tail_space:]
                    mv[payload_start : payload_start + len(remainder)] = remainder
                    self._cursor = len(remainder)
                    self._wrap_count += 1
                    logger.debug(
                        "MmapLogSink: ring wrapped (count=%d)", self._wrap_count
                    )

            self._flush_header(mv)
            del mv
            # Single msync for the whole batch — the key cost reduction.
            self._mm.flush()

    def write(self, raw: bytes) -> None:
        """Convenience wrapper for writing a single raw frame."""
        self.write_batch([raw])

    @property
    def cursor(self) -> int:
        """Current write cursor position within the payload region."""
        with self._lock:
            return self._cursor

    @property
    def wrap_count(self) -> int:
        """Number of times the ring has wrapped around."""
        with self._lock:
            return self._wrap_count

    def close(self) -> None:
        """Flush and release all resources."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                mv = memoryview(self._mm)
                self._flush_header(mv)
                del mv
                self._mm.flush()
                self._mm.close()
            finally:
                os.close(self._fd)

        logger.debug("MmapLogSink closed — path=%s", self._path)

    def __enter__(self) -> "MmapLogSink":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Module-level default sink (lazily initialised, one per process)
# ---------------------------------------------------------------------------

_DEFAULT_LOG_DIR = Path("logs/ingestion")
_DEFAULT_LOG_FILE = _DEFAULT_LOG_DIR / "stream_payloads.mmap"

_default_sink: MmapLogSink | None = None
_sink_lock = threading.Lock()


def get_default_sink(
    path: str | os.PathLike[str] | None = None,
    map_size: int = _DEFAULT_MAP_SIZE,
) -> MmapLogSink:
    """Return the process-wide default :class:`MmapLogSink`, creating it once.

    Parameters
    ----------
    path:
        Override the backing-file location.  Defaults to
        ``logs/ingestion/stream_payloads.mmap``.
    map_size:
        Pre-allocated file size in bytes.  Only used on first call.
    """
    global _default_sink
    if _default_sink is None:
        with _sink_lock:
            if _default_sink is None:
                effective_path = path if path is not None else _DEFAULT_LOG_FILE
                _default_sink = MmapLogSink(effective_path, map_size)
    return _default_sink


# ---------------------------------------------------------------------------
# Stream parser
# ---------------------------------------------------------------------------


class StreamBuffer:
    """Accumulate binary chunks, yield parsed JSON objects, and log raw frames.

    __slots__ = ("_buf", "_start", "_size", "_capacity")

    def __init__(self, buffer_size: int = _DEFAULT_BUFFER_SIZE) -> None:
        if buffer_size <= 0:
            raise ValueError("buffer size must be positive")
        self._buf = bytearray(buffer_size)
        self._start = 0
        self._size = 0
        self._capacity = buffer_size

    def _compact(self) -> None:
        """Move any retained bytes back to the front of the backing buffer."""
        if self._size == 0 or self._start == 0:
            return
        view = memoryview(self._buf)[self._start : self._start + self._size]
        self._buf[: self._size] = view
        self._start = 0

    def feed(self, data: bytes | bytearray | memoryview) -> Generator[Any, None, None]:
        """Append *data* and yield every complete newline-delimited JSON frame.

        A memoryview over the internal bytearray is used during the scan phase
        to slice frame boundaries without intermediate string copies.  The view
        is released before the buffer is trimmed so the bytearray can resize.

        Each complete frame is decoded by the SIMD-accelerated back-end when
        ``pysimdjson`` is available, or by ``json.loads`` otherwise.
        The parser uses a pre-allocated backing buffer that is reused across feeds
        so stream workers avoid repeated dynamic allocations for incoming blocks.
        """
        if not data:
            return

        payload = memoryview(data)
        self._compact()

        if len(payload) > self._capacity - self._size:
            raise ValueError("stream chunk exceeds pre-allocated buffer capacity")

        end = self._start + self._size
        self._buf[end : end + len(payload)] = payload
        self._size += len(payload)

        raw_frames: list[bytes] = []
        start = 0

        view = memoryview(self._buf)[self._start : self._start + self._size]
        for i in range(len(view)):
            if view[i] == _NEWLINE:
                if i > start:
                    raw_frames.append(bytes(view[start:i]))
                start = i + 1
        consumed = start
        view.release()

        if consumed:
            self._start += consumed
            self._size -= consumed
            if self._size == 0:
                self._start = 0

        for frame in frames:
            # _decode() handles both the SIMD and stdlib fallback paths.
            # Input is already bytes — no str conversion needed, which is a
            # free performance win over the previous json.loads(str) pattern.
            yield _decode(frame)

    def reset(self) -> None:
        """Discard all buffered data while keeping the backing storage reusable."""
        self._start = 0
        self._size = 0


__all__ = ["SIMDJSON_AVAILABLE", "StreamBuffer"]
