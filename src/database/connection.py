#!/usr/bin/env python3
"""
Database Connection Keep-Alive
==============================
Maintains long-lived relational connections during quiet, low-volume market
windows.
 
Serverless / autoscaled Postgres sinks (and intermediary poolers such as
PgBouncer) frequently drop idle TCP connections after a short timeout. When the
next price record arrives, the first write then stalls waiting for a fresh TCP
handshake and re-authentication. This module runs a low-overhead background
heartbeat (``SELECT 1;``) on a fixed interval to keep the channel warm so the
write path never pays the reconnect cost.
 
The keep-alive is connection-agnostic: it accepts any DB-API 2.0 connection
exposing ``cursor()``. It is meaningful for networked backends (e.g. PostgreSQL
via ``psycopg2``); for a local ``sqlite3`` connection there is no socket to keep
open, so the ping is harmless but inert.

Two flavours are provided:

* :class:`ConnectionKeepAlive` – thread-based, for blocking DB-API 2.0
  connections (``psycopg2``, ``sqlite3``, ...).
* :class:`AsyncConnectionKeepAlive` – asyncio-based, for non-blocking async
  drivers (``asyncpg``, ``databases``, ``aiosqlite``, ...) whose ``execute`` is
  awaitable. This is the preferred form for event-loop based services so the
  heartbeat never blocks the loop.

Usage (sync):
    conn = psycopg2.connect(DATABASE_URL)
    keepalive = ConnectionKeepAlive(conn, interval=30.0)
    keepalive.start()
    ...
    keepalive.stop()   # stops the background thread

Usage (async):
    conn = await asyncpg.connect(DATABASE_URL)
    keepalive = AsyncConnectionKeepAlive(conn, interval=30.0)
    keepalive.start()
    ...
    await keepalive.stop()   # cancels the background task
"""
 
import asyncio
import logging
import threading
from typing import Any, Optional
 
logger = logging.getLogger(__name__)
 
# Default heartbeat cadence in seconds. Idle-connection timeouts on serverless
# Postgres / PgBouncer are commonly 60-300s, so a 30s ping keeps the channel
# warm with comfortable margin.
DEFAULT_PING_INTERVAL: float = 30.0
HEARTBEAT_QUERY: str = "SELECT 1;"
 
 
class ConnectionKeepAlive:
    """Background heartbeat that keeps a relational connection channel alive.
 
    A daemon thread wakes every ``interval`` seconds and issues a lightweight
    ``SELECT 1;`` against the supplied connection. The thread is interruptible:
    ``stop()`` signals it via an :class:`threading.Event`, so shutdown does not
    wait out the full interval.
    """
 
    def __init__(
        self,
        connection: Any,
        interval: float = DEFAULT_PING_INTERVAL,
        query: str = HEARTBEAT_QUERY,
    ) -> None:
        if connection is None:
            raise ValueError("connection must not be None")
        if interval <= 0:
            raise ValueError("interval must be a positive number of seconds")
 
        self._conn = connection
        self._interval = interval
        self._query = query
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
 
    @property
    def is_running(self) -> bool:
        """True while the background heartbeat thread is alive."""
        return self._thread is not None and self._thread.is_alive()
 
    def start(self) -> None:
        """Start the background heartbeat thread.
 
        Calling ``start`` on an already-running keep-alive is a no-op.
        """
        if self.is_running:
            logger.debug("ConnectionKeepAlive already running; start() ignored")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="ConnectionKeepAlive",
        )
        self._thread.start()
        logger.info(
            "ConnectionKeepAlive started; pinging every %.1f seconds", self._interval
        )
 
    def ping(self) -> bool:
        """Issue a single heartbeat query.
 
        Returns ``True`` if the ping succeeded, ``False`` if it raised. Failures
        are logged and swallowed so a transient drop never takes down the
        background loop; the next tick simply tries again.
        """
        try:
            with self._lock:
                cursor = self._conn.cursor()
                try:
                    cursor.execute(self._query)
                    cursor.fetchone()
                finally:
                    close = getattr(cursor, "close", None)
                    if callable(close):
                        close()
            logger.debug("Heartbeat ping succeeded")
            return True
        except Exception:
            logger.warning("Heartbeat ping failed; will retry next interval", exc_info=True)
            return False
 
    def _run(self) -> None:
        """Background worker loop.
 
        ``Event.wait`` returns ``True`` when ``stop()`` has been signalled and
        ``False`` on timeout, so the loop ticks once per interval and exits
        promptly on shutdown.
        """
        while not self._stop_event.wait(self._interval):
            self.ping()
 
    def stop(self, timeout: Optional[float] = 5.0) -> None:
        """Signal the background thread to stop and wait for it to exit."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("ConnectionKeepAlive stopped")


class AsyncConnectionKeepAlive:
    """Asyncio background heartbeat for non-blocking relational connections.

    The async counterpart of :class:`ConnectionKeepAlive`. A background
    :class:`asyncio.Task` wakes every ``interval`` seconds and awaits a
    lightweight ``SELECT 1;`` against the supplied connection, keeping the
    channel warm without blocking the event loop.

    The connection only needs an awaitable ``execute(query)`` (as provided by
    ``asyncpg``, ``databases``, ``aiosqlite`` and similar async drivers). The
    sleep between pings is implemented with an :class:`asyncio.Event`, so
    ``stop()`` cancels the wait immediately rather than blocking for the full
    interval.
    """

    def __init__(
        self,
        connection: Any,
        interval: float = DEFAULT_PING_INTERVAL,
        query: str = HEARTBEAT_QUERY,
    ) -> None:
        if connection is None:
            raise ValueError("connection must not be None")
        if interval <= 0:
            raise ValueError("interval must be a positive number of seconds")

        self._conn = connection
        self._interval = interval
        self._query = query
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        """True while the background heartbeat task is active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Schedule the background heartbeat task on the running event loop.

        Calling ``start`` on an already-running keep-alive is a no-op. Must be
        called from within a running event loop.
        """
        if self.is_running:
            logger.debug("AsyncConnectionKeepAlive already running; start() ignored")
            return
        self._stop_event.clear()
        self._task = asyncio.ensure_future(self._run())
        logger.info(
            "AsyncConnectionKeepAlive started; pinging every %.1f seconds",
            self._interval,
        )

    async def ping(self) -> bool:
        """Issue a single heartbeat query.

        Returns ``True`` if the ping succeeded, ``False`` if it raised. Failures
        are logged and swallowed so a transient drop never takes down the
        background loop; the next tick simply tries again. ``CancelledError`` is
        re-raised so shutdown stays responsive.
        """
        try:
            await self._conn.execute(self._query)
            logger.debug("Heartbeat ping succeeded")
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Heartbeat ping failed; will retry next interval", exc_info=True
            )
            return False

    async def _run(self) -> None:
        """Background worker coroutine.

        Waits on the stop event with a per-interval timeout: a timeout means it
        is time to ping, while a set event means ``stop()`` was called and the
        loop should exit promptly.
        """
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._interval
                    )
                except asyncio.TimeoutError:
                    await self.ping()
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Signal the background task to stop and await its completion."""
        self._stop_event.set()
        task = self._task
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("AsyncConnectionKeepAlive stopped")
 