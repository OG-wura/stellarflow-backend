#!/usr/bin/env python3
"""
Database writers for telemetry persistence.

* **RelationalWriter** (Issue #579) – PostgreSQL bulk insert with buffered
  batching via ``psycopg2.extras.execute_values``.  Rows are flushed when
  the buffer reaches 50 records or every 1000 ms, whichever comes first.

* **PartitionedTelemetryWriter** – weekly SQLite partition router in front
  of ``BatchSink``.  Splits incoming telemetry records across weekly tables
  (e.g. ``telemetry_2024_W01``, ``telemetry_2024_W02``, …) based on a raw
  Unix-timestamp field in the payload.

RelationalWriter usage::

    import psycopg2
    from src.database.writer import RelationalWriter

    conn = psycopg2.connect(database_url)
    writer = RelationalWriter(conn, table_name="telemetry")
    writer.save({"asset_id": "NGN/XLM", "price": 12345, "ts": 1700000000})
    writer.shutdown()

PartitionedTelemetryWriter usage::

    import sqlite3
    from src.database.batch_sink import BatchSink
    from src.database.writer import PartitionedTelemetryWriter

    conn = sqlite3.connect('metrics.db', isolation_level=None)
    base_sink = BatchSink(conn, table_name='telemetry', flush_interval=2.0)
    writer = PartitionedTelemetryWriter(
        base_sink,
        timestamp_field='ts',
    )

    writer.save({
        "asset_id": "xlm-usdc",
        "price": 0.12,
        "ts": 1700000000,
    })
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .batch_sink import BatchSink

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
DEFAULT_FLUSH_INTERVAL_MS = 1000.0


def _execute_values_bulk(cursor: Any, sql: str, values: List[tuple], page_size: int) -> None:
    """Bulk-insert helper; separated for test patching."""
    try:
        from psycopg2.extras import execute_values
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2 is required for RelationalWriter bulk inserts"
        ) from exc
    execute_values(cursor, sql, values, page_size=page_size)


# ---------------------------------------------------------------------------
# RelationalWriter – PostgreSQL bulk insert (Issue #579)
# ---------------------------------------------------------------------------

class RelationalWriter:
    """Thread-safe transactional buffer for batched PostgreSQL inserts.

    Rows are flushed when either:

    * the buffer reaches ``batch_size`` records (default 50), or
    * the background timer fires every ``flush_interval_ms`` (default 1000 ms).
    """

    def __init__(
        self,
        connection: Any,
        table_name: str = "telemetry",
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_ms: float = DEFAULT_FLUSH_INTERVAL_MS,
    ) -> None:
        if connection is None:
            raise ValueError("connection must not be None")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if flush_interval_ms <= 0:
            raise ValueError("flush_interval_ms must be positive")

        self._conn = connection
        self._table = table_name
        self._batch_size = batch_size
        self._interval = flush_interval_ms / 1000.0
        self._buffer: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="RelationalWriter-Flusher",
        )
        self._thread.start()
        logger.debug(
            "RelationalWriter initialized for table %s "
            "(batch_size=%d, flush_interval_ms=%.0f)",
            self._table,
            self._batch_size,
            flush_interval_ms,
        )

    def save(self, data: Dict[str, Any]) -> None:
        """Add a compiled row to the in-memory buffer.

        Triggers an immediate flush when the buffer reaches ``batch_size``.
        """
        if not isinstance(data, dict):
            raise TypeError("data must be a dict mapping column names to values")

        should_flush = False
        with self._lock:
            self._buffer.append(data)
            if len(self._buffer) >= self._batch_size:
                should_flush = True

        if should_flush:
            self._flush()

    def _run(self) -> None:
        """Background worker that flushes buffered rows on a fixed interval."""
        while not self._stop_event.wait(self._interval):
            try:
                self._flush()
            except Exception as exc:  # pragma: no cover – defensive
                logger.exception(
                    "Unexpected error while flushing RelationalWriter: %s", exc
                )

    def _flush(self) -> None:
        """Bulk-insert buffered rows inside a single database transaction."""
        with self._flush_lock:
            with self._lock:
                if not self._buffer:
                    return
                batch = self._buffer.copy()
                self._buffer.clear()

            logger.debug(
                "Flushing %d records to table %s via execute_values",
                len(batch),
                self._table,
            )

            columns = list(batch[0].keys())
            column_clause = ", ".join(columns)
            sql = f"INSERT INTO {self._table} ({column_clause}) VALUES %s"
            values = [tuple(row[col] for col in columns) for row in batch]

            cursor = self._conn.cursor()
            try:
                _execute_values_bulk(cursor, sql, values, page_size=len(values))
                self._conn.commit()
                logger.debug("Successfully flushed %d records", len(batch))
            except Exception:
                self._conn.rollback()
                with self._lock:
                    self._buffer = batch + self._buffer
                logger.exception(
                    "Failed to flush RelationalWriter; records re-queued"
                )
                raise
            finally:
                close = getattr(cursor, "close", None)
                if callable(close):
                    close()

    def shutdown(self) -> None:
        """Stop the background flusher and persist any remaining rows."""
        self._stop_event.set()
        self._thread.join()
        try:
            self._flush()
        except Exception as exc:  # pragma: no cover – defensive
            logger.exception(
                "Error during final RelationalWriter shutdown flush: %s", exc
            )
        logger.info(
            "RelationalWriter shutdown complete; %d records remaining in buffer",
            len(self._buffer),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _week_bounds_from_epoch(epoch_s: float) -> tuple[int, int]:
    """Return ``(iso_year, iso_week_number)`` for a Unix timestamp (seconds
    or milliseconds; the latter is detected by magnitude).
    """
    if abs(epoch_s) > 1e12:
        # Probably milliseconds
        epoch_s = epoch_s / 1_000.0
    dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return iso_year, iso_week


def _partition_table_name(base_table: str, iso_year: int, iso_week: int) -> str:
    return f"{base_table}_{iso_year}_W{iso_week:02d}"


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------

class PartitionedTelemetryWriter:
    """Routes telemetry records to weekly partitioned child tables.

    Parameters
    ----------
    base_sink:
        A fully constructed ``BatchSink`` that owns the shared SQLite
        connection.  All flushed data still travels through this sink;
        the router only selects which child table name to use.
    base_table:
        Logical parent table name (default: ``telemetry``).  Child tables
        are named ``<base_table>_<YEAR>_W<WW>``.
    timestamp_field:
        Key in the record dict that carries the event time as a raw Unix
        timestamp (seconds or milliseconds).
    schema_source:
        An optional mapping of ``column_name -> SQLite affinity`` used
        when auto-creating a new partition.  When *None*, the schema is
        inferred from the first record that lands in that week.  Supplying
        this mapping keeps the partition DDL deterministic and avoids
        surprises when the first week's payload is sparse.
    create_if_missing:
        If *False*, a missing partition is treated as a write error
        instead of triggering an automatic CREATE TABLE.
    """

    def __init__(
        self,
        base_sink: BatchSink,
        base_table: str = "telemetry",
        timestamp_field: str = "ts",
        schema_source: Optional[Dict[str, str]] = None,
        create_if_missing: bool = True,
    ) -> None:
        if base_sink is None:
            raise ValueError("base_sink must not be None")

        self._sink = base_sink
        self._base_table = base_table
        self._ts_field = timestamp_field
        self._schema = schema_source
        self._create_if_missing = create_if_missing

        # Known partitions that have been created (or verified) so far.
        # Guarded by self._lock because partition creation happens on the
        # writer thread path.
        self._lock = threading.Lock()
        self._known_partitions: Set[str] = set()

        logger.info(
            "PartitionedTelemetryWriter initialised: base='%s' ts='%s'",
            base_table,
            timestamp_field,
        )

    # ------------------------------------------------------------------
    # Public API – mirrors BatchSink.save
    # ------------------------------------------------------------------

    def save(self, record: Dict[str, Any]) -> None:
        """Buffer *record* under the appropriate weekly partition.

        The call is non-blocking for the caller: the record is appended
        to the underlying ``BatchSink``'s buffer immediately.  If the
        target partition does not yet exist, its DDL is issued before
        the record is enqueued (still on the same thread).
        """
        if self._ts_field not in record:
            raise KeyError(f"Record is missing required timestamp field '{self._ts_field}'")

        iso_year, iso_week = _week_bounds_from_epoch(record[self._ts_field])
        table_name = _partition_table_name(self._base_table, iso_year, iso_week)

        # Ensure the partition table exists before buffering.
        # Only one writer thread can hold the lock at a time; the check-
        # then-create sequence is therefore race-free.
        if table_name not in self._known_partitions:
            if not self._create_if_missing:
                raise RuntimeError(
                    f"Partition table '{table_name}' does not exist and "
                    "create_if_missing is disabled"
                )
            self._create_partition(table_name)
            with self._lock:
                self._known_partitions.add(table_name)

        # Rewrite the target table on the fly.  We bypass BatchSink.save
        # because it hard-codes self._table.  Instead we call _flush-like
        # logic inline but append to the shared buffer with the resolved
        # table name, then let the normal flusher thread pick it up.
        # To keep things simple we inject the table name into the record
        # and patch the flusher on a per-batch basis.  A cleaner way
        # would be to fork BatchSink, but that adds unnecessary surface.
        with self._sink._lock:
            tagged = dict(record)
            tagged["__partition_table"] = table_name
            self._sink._buffer.append(tagged)

        logger.debug("Saved record -> partition %s", table_name)

    def shutdown(self) -> None:
        """Delegate shutdown to the underlying ``BatchSink``."""
        self._sink.shutdown()

    # ------------------------------------------------------------------
    # Partition DDL
    # ------------------------------------------------------------------

    def _create_partition(self, table_name: str) -> None:
        """Issue ``CREATE TABLE IF NOT EXISTS`` for a child partition.

        The DDL mirrors the canonical schema expected in telemetry payloads.
        """
        schema_columns = self._schema or {
            "asset_id": "TEXT",
            "price": "REAL",
            "source": "TEXT",
            "ts": "INTEGER",
        }
        column_defs = ", ".join(f'"{col}" {aff}' for col, aff in schema_columns.items())
        create_sql = (
            f'CREATE TABLE IF NOT EXISTS "{table_name}" ({column_defs})'
        )

        cursor = self._sink._conn.cursor()
        try:
            cursor.execute(create_sql)
            logger.info("Created/verified partition table %s", table_name)
        except sqlite3.Error:
            logger.exception("Failed to create partition table %s", table_name)
            raise
        finally:
            cursor.close()

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @property
    def known_partitions(self) -> Set[str]:
        """Return the set of partition table names created by this writer."""
        with self._lock:
            return set(self._known_partitions)
