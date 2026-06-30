from __future__ import annotations

import threading
import logging
import sqlite3
from typing import Dict, List, Any, Optional

from src.database.writer import DatabaseWriter

logger = logging.getLogger(__name__)


class BatchSink:
    """Thread‑safe micro‑batch aggregator for telemetry data.

    Uses a pre‑compiled ``DatabaseWriter`` so the SQL INSERT is parsed only
    once per (table, column‑set) pair instead of on every flush cycle.

    Usage:
        conn = sqlite3.connect('telemetry.db', isolation_level=None)
        sink = BatchSink(conn, table_name='telemetry', flush_interval=2.0)
        sink.save({"asset_id": "abc", "price": 123.45, "ts": 1700000000})
        ...
        sink.shutdown()
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        table_name: str = "telemetry",
        flush_interval: float = 2.0,
    ):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be an instance of sqlite3.Connection")
        self._conn = connection
        self._table = table_name
        self._interval = flush_interval
        self._buffer: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._writer = DatabaseWriter(connection)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="BatchSink-Flusher"
        )
        self._thread.start()
        logger.debug(
            "BatchSink initialized for table %s with %s‑second interval",
            self._table,
            self._interval,
        )

    def save(self, data: Dict[str, Any]) -> None:
        """Add a telemetry record to the in‑memory buffer."""
        if not isinstance(data, dict):
            raise TypeError("data must be a dict mapping column names to values")
        with self._lock:
            self._buffer.append(data)
        logger.debug("Saved record to buffer; current size=%d", len(self._buffer))

    def _run(self) -> None:
        """Background worker that periodically flushes the buffer."""
        while not self._stop_event.wait(self._interval):
            try:
                self._flush()
            except Exception as exc:
                logger.exception(
                    "Unexpected error while flushing BatchSink: %s", exc
                )

    def _flush(self) -> None:
        """Flush buffered rows via the pre‑compiled DatabaseWriter."""
        with self._lock:
            if not self._buffer:
                return
            batch = self._buffer.copy()
            self._buffer.clear()

        logger.debug("Flushing %d records to table %s", len(batch), self._table)

        try:
            self._writer.insert_batch(self._table, batch, commit=False)
            self._conn.execute("COMMIT")
            logger.debug("Successfully flushed %d records", len(batch))
        except Exception:
            self._conn.execute("ROLLBACK")
            with self._lock:
                self._buffer = batch + self._buffer
            logger.exception("Failed to flush BatchSink; records re‑queued")
            raise

    def shutdown(self) -> None:
        """Stop the background thread and flush any remaining data."""
        self._stop_event.set()
        self._thread.join()
        try:
            self._flush()
        except Exception as exc:
            logger.exception(
                "Error during final BatchSink shutdown flush: %s", exc
            )
        logger.info(
            "BatchSink shutdown complete; %d records remaining in buffer",
            len(self._buffer),
        )
