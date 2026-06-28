"""Compatibility exports for the ingestion parser helpers.

Historically this project exposed parser utilities from ``ingestion.parsers``.
The high-throughput websocket ticker flattener now lives in
``ingestion.parser``; this module re-exports that public surface so existing
imports keep working.
"""

from ingestion.parser import (
    DEFAULT_SEGMENT_SIZE,
    TelemetrySegment,
    TelemetrySegmentBatch,
    TelemetryTuple,
    build_segments_from_stream,
    build_telemetry_segments,
    flatten_telemetry_frames,
    iter_flat_ticker_tuples,
    iter_price_events_from_stream,
)

__all__ = [
    "DEFAULT_SEGMENT_SIZE",
    "TelemetrySegment",
    "TelemetrySegmentBatch",
    "TelemetryTuple",
    "build_segments_from_stream",
    "build_telemetry_segments",
    "flatten_telemetry_frames",
    "iter_flat_ticker_tuples",
    "iter_price_events_from_stream",
]