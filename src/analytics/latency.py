from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)
DEFAULT_FLUSH_INTERVAL_SECONDS = 60
LATENCY_TABLE_NAME = "packet_latency_metrics"


@dataclass(frozen=True)
class PacketLatencyRecord:
    packet_id: str
    ingested_at: datetime
    processing_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def total_latency_ms(self) -> Optional[int]:
        if self.confirmed_at is None:
            return None
        return int((self.confirmed_at - self.ingested_at).total_seconds() * 1000)

    def processing_latency_ms(self) -> Optional[int]:
        if self.processing_at is None:
            return None
        return int((self.processing_at - self.ingested_at).total_seconds() * 1000)

    def confirmation_latency_ms(self) -> Optional[int]:
        if self.confirmed_at is None:
            return None
        start = self.processing_at or self.ingested_at
        return int((self.confirmed_at - start).total_seconds() * 1000)

    def is_complete(self) -> bool:
        return self.confirmed_at is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "ingested_at": self.ingested_at.isoformat(),
            "processing_at": self.processing_at.isoformat() if self.processing_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "total_latency_ms": self.total_latency_ms(),
            "processing_latency_ms": self.processing_latency_ms(),
            "confirmation_latency_ms": self.confirmation_latency_ms(),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class LatencyMetrics:
    packet_id: str
    total_latency_ms: int
    processing_latency_ms: Optional[int]
    confirmation_latency_ms: Optional[int]
    ingested_at: datetime
    processing_at: Optional[datetime]
    confirmed_at: datetime
    metadata: Dict[str, Any]
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_db_row(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "ingested_at": self.ingested_at,
            "processing_at": self.processing_at,
            "confirmed_at": self.confirmed_at,
            "total_latency_ms": self.total_latency_ms,
            "processing_latency_ms": self.processing_latency_ms,
            "confirmation_latency_ms": self.confirmation_latency_ms,
            "metadata": self.metadata,
            "recorded_at": self.recorded_at,
        }


class LatencyTracker:
    """Tracks packet lifecycle latencies and exports aggregate metrics."""

    def __init__(self, database_url: Optional[str] = None, flush_interval_seconds: int = DEFAULT_FLUSH_INTERVAL_SECONDS):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.flush_interval_seconds = flush_interval_seconds
        self._records: Dict[str, PacketLatencyRecord] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False

    def track_ingested(self, packet_id: str, ingested_at: Optional[datetime] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        now = ingested_at or datetime.now(timezone.utc)
        metadata = metadata or {}
        with self._lock:
            self._records[packet_id] = PacketLatencyRecord(
                packet_id=packet_id,
                ingested_at=now,
                metadata=metadata,
            )
            logger.debug("[LatencyTracker] packet ingested: %s", packet_id)

    def track_processing(self, packet_id: str, processing_at: Optional[datetime] = None) -> None:
        now = processing_at or datetime.now(timezone.utc)
        with self._lock:
            existing = self._records.get(packet_id)
            if existing is None:
                logger.warning("[LatencyTracker] processing stage for unknown packet: %s", packet_id)
                return
            self._records[packet_id] = PacketLatencyRecord(
                packet_id=existing.packet_id,
                ingested_at=existing.ingested_at,
                processing_at=now,
                metadata=existing.metadata,
            )
            logger.debug("[LatencyTracker] packet processing recorded: %s", packet_id)

    def track_confirmed(self, packet_id: str, confirmed_at: Optional[datetime] = None) -> None:
        now = confirmed_at or datetime.now(timezone.utc)
        with self._lock:
            existing = self._records.get(packet_id)
            if existing is None:
                logger.warning("[LatencyTracker] confirmation stage for unknown packet: %s", packet_id)
                return
            completed = PacketLatencyRecord(
                packet_id=existing.packet_id,
                ingested_at=existing.ingested_at,
                processing_at=existing.processing_at,
                confirmed_at=now,
                metadata=existing.metadata,
            )
            self._records[packet_id] = completed
            logger.info(
                "[LatencyTracker] packet confirmed: %s total_latency=%sms",
                packet_id,
                completed.total_latency_ms(),
            )

    def collect_completed_metrics(self) -> Dict[str, LatencyMetrics]:
        with self._lock:
            result = {}
            for packet_id, record in list(self._records.items()):
                if record.is_complete():
                    total_latency = record.total_latency_ms()
                    if total_latency is None:
                        continue
                    result[packet_id] = LatencyMetrics(
                        packet_id=record.packet_id,
                        total_latency_ms=total_latency,
                        processing_latency_ms=record.processing_latency_ms(),
                        confirmation_latency_ms=record.confirmation_latency_ms(),
                        ingested_at=record.ingested_at,
                        processing_at=record.processing_at,
                        confirmed_at=record.confirmed_at,
                        metadata=record.metadata,
                    )
            return result

    def _remove_exported_records(self, packet_ids: List[str]) -> None:
        with self._lock:
            for packet_id in packet_ids:
                record = self._records.get(packet_id)
                if record and record.is_complete():
                    self._records.pop(packet_id, None)

    def _connect_db(self):
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not configured for LatencyTracker")
        try:
            import psycopg2
            from psycopg2.extras import Json
        except ImportError as exc:
            raise RuntimeError("psycopg2 is required to export latency metrics") from exc

        conn = psycopg2.connect(self.database_url)
        conn.autocommit = True
        return conn, Json

    def _ensure_latency_table(self, connection) -> None:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {LATENCY_TABLE_NAME} (
            id SERIAL PRIMARY KEY,
            packet_id TEXT NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL,
            processing_at TIMESTAMPTZ NULL,
            confirmed_at TIMESTAMPTZ NOT NULL,
            total_latency_ms INTEGER NOT NULL,
            processing_latency_ms INTEGER NULL,
            confirmation_latency_ms INTEGER NULL,
            metadata JSONB NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
        with connection.cursor() as cursor:
            cursor.execute(create_sql)

    def flush(self) -> None:
        from src.database.writer import DatabaseWriter

        metrics = list(self.collect_completed_metrics().values())
        if not metrics:
            logger.debug("[LatencyTracker] no completed latency records to flush")
            return

        if not self.database_url:
            logger.warning(
                "[LatencyTracker] DATABASE_URL not configured, dropping exported latency metrics"
            )
            return

        try:
            connection, json_adapter = self._connect_db()
            self._ensure_latency_table(connection)

            writer = DatabaseWriter(connection)
            rows = [metric.to_db_row() for metric in metrics]
            for row in rows:
                row["metadata"] = json_adapter(row["metadata"])
            writer.insert_batch(LATENCY_TABLE_NAME, rows)

            packet_ids = [metric.packet_id for metric in metrics]
            self._remove_exported_records(packet_ids)
            logger.info(
                "[LatencyTracker] exported %d latency records to analytics DB",
                len(metrics),
            )
        except Exception as exc:
            logger.error("[LatencyTracker] failed to export latency metrics: %s", exc)
        finally:
            with suppress(Exception):
                connection.close()

    def _flush_loop(self) -> None:
        logger.info("[LatencyTracker] starting latency flush loop every %ss", self.flush_interval_seconds)
        while not self._stop_event.wait(self.flush_interval_seconds):
            try:
                self.flush()
            except Exception as exc:
                logger.error("[LatencyTracker] flush loop error: %s", exc)
        logger.info("[LatencyTracker] latency flush loop stopped")

    def start(self) -> None:
        if self._started:
            logger.debug("[LatencyTracker] already started")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()
        self._started = True

    def stop(self, flush_final: bool = True) -> None:
        if not self._started:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.flush_interval_seconds + 5)
        self._started = False
        if flush_final:
            self.flush()


latency_tracker = LatencyTracker()