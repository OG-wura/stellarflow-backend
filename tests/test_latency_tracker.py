from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analytics.latency import LatencyTracker, LatencyMetrics


def test_latency_metrics_are_computed_correctly():
    tracker = LatencyTracker(database_url="postgres://user@localhost/db_test")
    ingested_at = datetime.now(timezone.utc)
    tracker.track_ingested("packet-123", ingested_at=ingested_at, metadata={"source": "relayer"})
    tracker.track_processing("packet-123", processing_at=ingested_at + timedelta(seconds=1))
    tracker.track_confirmed("packet-123", confirmed_at=ingested_at + timedelta(seconds=4))

    metrics = tracker.collect_completed_metrics()
    assert "packet-123" in metrics

    latency = metrics["packet-123"]
    assert latency.packet_id == "packet-123"
    assert latency.total_latency_ms == 4000
    assert latency.processing_latency_ms == 1000
    assert latency.confirmation_latency_ms == 3000
    assert latency.metadata == {"source": "relayer"}


def test_latency_tracker_flushes_completed_records_to_db():
    tracker = LatencyTracker(database_url="postgres://user@localhost/db_test")
    ingested_at = datetime.now(timezone.utc)
    tracker.track_ingested("packet-456", ingested_at=ingested_at)
    tracker.track_confirmed("packet-456", confirmed_at=ingested_at + timedelta(milliseconds=1200))

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    fake_json = lambda value: value

    with patch.object(LatencyTracker, "_connect_db", return_value=(mock_conn, fake_json)):
        tracker.flush()

    assert mock_cursor.execute.call_count == 1
    create_sql_call = mock_cursor.execute.call_args_list[0]
    assert "CREATE TABLE IF NOT EXISTS" in create_sql_call.args[0]
    mock_conn.executemany.assert_called_once()
    insert_sql = mock_conn.executemany.call_args[0][0]
    assert "INSERT INTO" in insert_sql


def test_latency_tracker_removes_exported_records_after_flush():
    tracker = LatencyTracker(database_url="postgres://user@localhost/db_test")
    ingested_at = datetime.now(timezone.utc)
    tracker.track_ingested("packet-999", ingested_at=ingested_at)
    tracker.track_confirmed("packet-999", confirmed_at=ingested_at + timedelta(milliseconds=500))

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    fake_json = lambda value: value

    with patch.object(LatencyTracker, "_connect_db", return_value=(mock_conn, fake_json)):
        tracker.flush()

    assert "packet-999" not in tracker._records
    assert mock_cursor.execute.call_count == 1


def test_latency_tracker_no_database_url_logs_warning(caplog):
    tracker = LatencyTracker(database_url=None)
    ingested_at = datetime.now(timezone.utc)
    tracker.track_ingested("packet-789", ingested_at=ingested_at)
    tracker.track_confirmed("packet-789", confirmed_at=ingested_at + timedelta(milliseconds=500))

    with patch.object(LatencyTracker, "_connect_db", side_effect=RuntimeError("DATABASE_URL is not configured")):
        tracker.flush()

    assert any("DATABASE_URL not configured" in record.message for record in caplog.records)
