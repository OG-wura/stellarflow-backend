from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from network.polling import poll_price_checks, run_bounded_price_checks


@asynccontextmanager
async def _fake_make_session():
    yield object()


def test_run_bounded_price_checks_returns_empty_for_no_endpoints() -> None:
    async def fetcher(_session: Any, _url: str) -> Optional[Dict[str, Any]]:
        return {"price": 1.0}

    assert asyncio.run(run_bounded_price_checks(None, [], fetcher)) == []


def test_run_bounded_price_checks_collects_all_interval_workers() -> None:
    calls: List[str] = []

    async def fetcher(_session: Any, url: str) -> Optional[Dict[str, Any]]:
        calls.append(url)
        await asyncio.sleep(0.01)
        return {"price": 1.0, "source": url}

    async def exercise() -> None:
        endpoints = ["https://a.example/price", "https://b.example/price"]
        results = await run_bounded_price_checks(None, endpoints, fetcher)
        assert calls == endpoints
        assert results == [(url, {"price": 1.0, "source": url}) for url in endpoints]

    asyncio.run(exercise())


def test_run_bounded_price_checks_joins_workers_before_returning() -> None:
    active = 0
    peak = 0

    async def fetcher(_session: Any, _url: str) -> Optional[Dict[str, Any]]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"price": 1.0}

    async def exercise() -> None:
        endpoints = [f"https://node-{index}.example/price" for index in range(4)]
        await run_bounded_price_checks(None, endpoints, fetcher)
        assert peak == len(endpoints)
        assert active == 0

    asyncio.run(exercise())


def test_poll_price_checks_invokes_handler_for_successful_payloads() -> None:
    observed: List[tuple[str, Dict[str, Any]]] = []
    stop_event = asyncio.Event()

    async def fetcher(_session: Any, url: str) -> Optional[Dict[str, Any]]:
        if "bad.example" in url:
            return None
        return {"price": 2.5, "source": url}

    async def on_price(url: str, payload: Dict[str, Any]) -> None:
        observed.append((url, payload))
        stop_event.set()

    async def exercise() -> None:
        with patch("network.polling.make_session", _fake_make_session):
            await poll_price_checks(
                ["https://good.example/price", "https://bad.example/price"],
                on_price,
                stop_event=stop_event,
                interval_s=0.01,
                fetch_price=fetcher,
            )
        assert observed == [
            (
                "https://good.example/price",
                {"price": 2.5, "source": "https://good.example/price"},
            )
        ]

    asyncio.run(exercise())


def test_poll_price_checks_waits_for_interval_completion_between_cycles() -> None:
    interval_starts: List[int] = []
    active_intervals = 0
    peak_active_intervals = 0
    stop_event = asyncio.Event()
    cycle = 0

    async def fetcher(_session: Any, _url: str) -> Optional[Dict[str, Any]]:
        nonlocal active_intervals, peak_active_intervals, cycle
        active_intervals += 1
        peak_active_intervals = max(peak_active_intervals, active_intervals)
        await asyncio.sleep(0.02)
        active_intervals -= 1
        cycle += 1
        if cycle >= 2:
            stop_event.set()
        return {"price": float(cycle)}

    async def on_price(_url: str, _payload: Dict[str, Any]) -> None:
        interval_starts.append(cycle)

    async def exercise() -> None:
        with patch("network.polling.make_session", _fake_make_session):
            await poll_price_checks(
                ["https://a.example/price"],
                on_price,
                stop_event=stop_event,
                interval_s=0.01,
                fetch_price=fetcher,
            )
        assert peak_active_intervals == 1
        assert interval_starts == [1, 2]

    asyncio.run(exercise())


def test_poll_price_checks_exits_after_current_interval_when_stopped() -> None:
    stop_event = asyncio.Event()
    cycles = 0

    async def fetcher(_session: Any, _url: str) -> Optional[Dict[str, Any]]:
        nonlocal cycles
        cycles += 1
        if cycles == 1:
            stop_event.set()
        return {"price": float(cycles)}

    async def on_price(_url: str, _payload: Dict[str, Any]) -> None:
        pass

    async def exercise() -> None:
        with patch("network.polling.make_session", _fake_make_session):
            await poll_price_checks(
                ["https://a.example/price"],
                on_price,
                stop_event=stop_event,
                interval_s=10.0,
                fetch_price=fetcher,
            )
        assert cycles == 1

    asyncio.run(exercise())
