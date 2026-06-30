"""Compatibility exports for the ingestion parser helpers.

Historically this project exposed parser utilities from ``ingestion.parsers``.
The high-throughput websocket ticker flattener now lives in
``ingestion.parser``; this module re-exports that public surface so existing
imports keep working.

The SIMD-acceleration symbols (``SIMDJSON_AVAILABLE``, ``parse_raw_pack``)
introduced in the pysimdjson integration are also re-exported here so that
callers using either import path gain access to the full API.
"""

from ingestion.parser import (
    DEFAULT_SEGMENT_SIZE,
    SIMDJSON_AVAILABLE,
    TelemetrySegment,
    TelemetrySegmentBatch,
    TelemetryTuple,
    build_telemetry_segments,
    flatten_telemetry_frames,
    iter_flat_ticker_tuples,
    parse_raw_pack,
)

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
