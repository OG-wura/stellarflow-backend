"""log_parse.py — zero-copy log entry parser for the ingestion pipeline.

Identifies structural markers (level, timestamp, message) directly inside
raw byte arrays using memoryview scans, avoiding intermediate string
allocations that cause CPU spikes during volatile network periods.

Log line format (space-delimited, newline-terminated):
    <ISO-TIMESTAMP> <LEVEL> <MESSAGE...>\n

Example:
    2026-06-28T20:00:00.000Z INFO price update NGN/XLM=1540.25\n
"""
from __future__ import annotations

from typing import Generator, NamedTuple

# Byte-level constants — no str objects created at scan time.
_NEWLINE: int = ord("\n")
_SPACE: int = ord(" ")

_LEVEL_DEBUG = b"DEBUG"
_LEVEL_INFO = b"INFO"
_LEVEL_WARN = b"WARN"
_LEVEL_ERROR = b"ERROR"
_KNOWN_LEVELS = frozenset((_LEVEL_DEBUG, _LEVEL_INFO, _LEVEL_WARN, _LEVEL_ERROR))


class LogEntry(NamedTuple):
    timestamp: bytes  # raw bytes, caller decodes as needed
    level: bytes
    message: bytes


def _find_byte(view: memoryview, target: int, start: int) -> int:
    """Return index of *target* byte in *view* from *start*, or -1."""
    for i in range(start, len(view)):
        if view[i] == target:
            return i
    return -1


def parse_log_bytes(
    data: bytes | bytearray | memoryview,
    *,
    drop_malformed: bool = False,
) -> Generator[LogEntry, None, None]:
    """Yield LogEntry tuples parsed from *data* without intermediate strings.

    Scans *data* as a memoryview to locate newline and space markers directly
    in the byte array.  Each field is sliced as a bytes view; no str conversion
    or text splitting occurs during the scan phase.

    Parameters
    ----------
    data:           Raw log bytes, possibly a partial buffer tail.
    drop_malformed: When True, skip unparseable lines; otherwise raise.

    Yields
    ------
    LogEntry with (timestamp, level, message) as bytes.

    Raises
    ------
    ValueError: On malformed lines when drop_malformed is False.
    """
    view = memoryview(data) if not isinstance(data, memoryview) else data

    start = 0
    length = len(view)

    while start < length:
        nl = _find_byte(view, _NEWLINE, start)
        end = nl if nl != -1 else length
        line = view[start:end]
        start = end + 1

        if not line:
            continue

        # Locate first space → timestamp boundary.
        sp1 = _find_byte(line, _SPACE, 0)
        if sp1 == -1:
            if drop_malformed:
                continue
            raise ValueError(f"Missing timestamp/level separator in: {bytes(line)!r}")

        # Locate second space → level boundary.
        sp2 = _find_byte(line, _SPACE, sp1 + 1)
        if sp2 == -1:
            if drop_malformed:
                continue
            raise ValueError(f"Missing level/message separator in: {bytes(line)!r}")

        timestamp = bytes(line[:sp1])
        level = bytes(line[sp1 + 1 : sp2])
        message = bytes(line[sp2 + 1 :])

        if level not in _KNOWN_LEVELS:
            if drop_malformed:
                continue
            raise ValueError(f"Unknown log level {level!r} in: {bytes(line)!r}")

        yield LogEntry(timestamp=timestamp, level=level, message=message)


__all__ = ["LogEntry", "parse_log_bytes"]
