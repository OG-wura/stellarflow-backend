import logging
import threading
from contextlib import contextmanager
from typing import List, Optional

try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
except ImportError:
    psycopg2 = None

logger = logging.getLogger(__name__)

class ConnectionHub:
    """
    Dual-pool connection management hub.
    Separates transaction paths by routing data writes to a primary instance
    while directing analytical queries to read-only replicas.
    """

    def __init__(
        self,
        primary_url: str,
        replica_urls: Optional[List[str]] = None,
        min_conn: int = 1,
        max_conn: int = 10,
    ):
        if psycopg2 is None:
            raise ImportError("psycopg2 is required for ConnectionHub")
            
        self.primary_url = primary_url
        self.replica_urls = replica_urls or []
        self.min_conn = min_conn
        self.max_conn = max_conn

        # Primary pool for writes
        self._primary_pool = ThreadedConnectionPool(
            self.min_conn, self.max_conn, dsn=self.primary_url
        )
        logger.info("Initialized primary connection pool")

        # Replica pools for read-only analytical queries
        self._replica_pools = []
        for url in self.replica_urls:
            pool = ThreadedConnectionPool(self.min_conn, self.max_conn, dsn=url)
            self._replica_pools.append(pool)
            
        if self._replica_pools:
            logger.info(f"Initialized {len(self._replica_pools)} replica connection pool(s)")
        else:
            logger.warning("No replica URLs provided; analytical queries will fall back to primary")

        self._rr_counter = 0
        self._lock = threading.Lock()

    def close_all(self):
        """Close all connection pools."""
        self._primary_pool.closeall()
        for pool in self._replica_pools:
            pool.closeall()
        logger.info("Closed all connection pools")

    @contextmanager
    def primary_connection(self):
        """Context manager for obtaining a primary connection for data writes."""
        conn = self._primary_pool.getconn()
        try:
            yield conn
        finally:
            self._primary_pool.putconn(conn)

    @contextmanager
    def replica_connection(self):
        """Context manager for obtaining a read-only replica connection for analytical queries."""
        if not self._replica_pools:
            # Fall back to primary if no replicas are configured
            with self.primary_connection() as conn:
                yield conn
            return

        with self._lock:
            pool_index = self._rr_counter % len(self._replica_pools)
            self._rr_counter += 1
            
        pool = self._replica_pools[pool_index]
        conn = pool.getconn()
        try:
            yield conn
        finally:
            pool.putconn(conn)
