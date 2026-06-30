from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from database.connection import (  # noqa: E402
    ConnectionPoolHealthMonitor,
    HEARTBEAT_QUERY,
    _default_broken_exceptions,
)


class StaleSocketError(Exception):
    """Stand-in for a driver's stale-socket error, injected via broken_exceptions."""


def _make_pool() -> MagicMock:
    """A fake connection pool whose connections hand out recording cursors."""
    pool = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    pool.getconn.return_value = conn
    return pool


def _monitor(pool, factory=None, **kwargs):
    """Build a monitor with StaleSocketError classified as a broken socket."""
    kwargs.setdefault("broken_exceptions", (StaleSocketError,))
    return ConnectionPoolHealthMonitor(
        pool,
        factory or (lambda: _make_pool()),
        **kwargs,
    )


def test_healthy_probe_validates_and_returns_connection():
    pool = _make_pool()
    monitor = _monitor(pool)

    assert monitor.check_health() is True
    conn = pool.getconn.return_value
    conn.cursor.return_value.execute.assert_called_once_with(HEARTBEAT_QUERY)
    # Healthy connection is returned to the pool, not discarded.
    pool.putconn.assert_called_once_with(conn)
    assert monitor.consecutive_failures == 0


def test_stale_probe_discards_connection_with_close():
    pool = _make_pool()
    pool.getconn.return_value.cursor.return_value.execute.side_effect = StaleSocketError(
        "server closed the connection unexpectedly"
    )
    # threshold=2 so a single failure discards but does not yet rebuild.
    monitor = _monitor(pool, failure_threshold=2)

    assert monitor.check_health() is False
    pool.putconn.assert_called_once_with(pool.getconn.return_value, close=True)
    assert monitor.consecutive_failures == 1


def test_consecutive_failures_trigger_full_rebuild_and_swap():
    bad_pool = _make_pool()
    bad_pool.getconn.return_value.cursor.return_value.execute.side_effect = StaleSocketError(
        "connection already closed"
    )
    new_pool = _make_pool()
    factory = MagicMock(return_value=new_pool)

    monitor = _monitor(bad_pool, factory=factory, failure_threshold=2)

    assert monitor.check_health() is False  # 1st failure: discard only
    assert monitor.check_health() is True   # 2nd failure: rebuild succeeds

    factory.assert_called_once()
    bad_pool.closeall.assert_called_once()
    # The pool reference is swapped to the freshly built one.
    assert monitor.pool is new_pool
    assert monitor.consecutive_failures == 0


def test_healthy_probe_resets_failure_streak():
    pool = _make_pool()
    cursor = pool.getconn.return_value.cursor.return_value
    cursor.execute.side_effect = [StaleSocketError("blip"), None]
    monitor = _monitor(pool, failure_threshold=5)

    assert monitor.check_health() is False
    assert monitor.consecutive_failures == 1
    assert monitor.check_health() is True
    assert monitor.consecutive_failures == 0


def test_recover_keeps_old_pool_if_factory_raises():
    pool = _make_pool()
    factory = MagicMock(side_effect=RuntimeError("cannot reach DB"))
    monitor = _monitor(pool, factory=factory)

    assert monitor.recover() is False
    # Old pool retained for the next retry; not closed.
    assert monitor.pool is pool
    pool.closeall.assert_not_called()


def test_default_broken_exceptions_includes_oserror():
    # OSError (base of socket.error) is always classified as a stale socket.
    assert OSError in _default_broken_exceptions()


def test_non_stale_probe_error_still_counts_as_failure():
    pool = _make_pool()
    pool.getconn.return_value.cursor.return_value.execute.side_effect = ValueError("weird")
    monitor = _monitor(pool, failure_threshold=2)

    # Not a classified stale socket, but a probe that errors is not healthy.
    assert monitor.check_health() is False
    assert monitor.consecutive_failures == 1
    # Any errored probe leaves the connection in an unknown state, so it is
    # discarded (close=True) rather than returned to the pool.
    pool.putconn.assert_called_once_with(pool.getconn.return_value, close=True)


def test_putconn_without_close_kwarg_falls_back():
    pool = _make_pool()
    pool.getconn.return_value.cursor.return_value.execute.side_effect = StaleSocketError("x")

    def putconn(conn, **kwargs):
        if kwargs:
            raise TypeError("putconn() got an unexpected keyword argument")

    pool.putconn.side_effect = putconn
    monitor = _monitor(pool, failure_threshold=5)

    # Should not raise even though the pool's putconn rejects close=True.
    assert monitor.check_health() is False


def test_start_launches_thread_and_stop_joins_it():
    monitor = _monitor(_make_pool(), interval=30.0)
    assert monitor.is_running is False
    monitor.start()
    assert monitor.is_running is True
    monitor.stop()
    assert monitor.is_running is False


def test_background_loop_probes_on_interval():
    pool = _make_pool()
    probed = threading.Event()
    pool.getconn.return_value.cursor.return_value.execute.side_effect = (
        lambda *_a, **_k: probed.set()
    )
    monitor = _monitor(pool, interval=0.05)
    monitor.start()
    try:
        assert probed.wait(timeout=2.0), "expected at least one probe within 2s"
    finally:
        monitor.stop()


def test_stop_is_prompt_and_does_not_wait_full_interval():
    monitor = _monitor(_make_pool(), interval=60.0)
    monitor.start()

    start = time.monotonic()
    monitor.stop()
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert monitor.is_running is False


def test_double_start_is_noop():
    monitor = _monitor(_make_pool(), interval=60.0)
    monitor.start()
    first_thread = monitor._thread
    monitor.start()
    assert monitor._thread is first_thread
    monitor.stop()


def test_invalid_arguments_rejected():
    pool = _make_pool()
    with pytest.raises(ValueError):
        ConnectionPoolHealthMonitor(None, lambda: pool)
    with pytest.raises(ValueError):
        ConnectionPoolHealthMonitor(pool, None)
    with pytest.raises(ValueError):
        ConnectionPoolHealthMonitor(pool, lambda: pool, interval=0)
    with pytest.raises(ValueError):
        ConnectionPoolHealthMonitor(pool, lambda: pool, failure_threshold=0)
