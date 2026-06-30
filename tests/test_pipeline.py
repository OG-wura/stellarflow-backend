from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncIterator, List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import importlib.util, pathlib

_spec = importlib.util.spec_from_file_location(
    "queue_pipeline",
    pathlib.Path(__file__).parent.parent / "src" / "queue" / "pipeline.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

MAX_CONCURRENT_TASKS = _mod.MAX_CONCURRENT_TASKS
run_pipeline = _mod.run_pipeline

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def _as_stream(items: list) -> AsyncIterator:
    for item in items:
        yield item


async def test_all_messages_processed():
    received: List[int] = []

    async def processor(msg: int) -> None:
        received.append(msg)

    await run_pipeline(_as_stream(list(range(10))), processor)
    assert sorted(received) == list(range(10))


async def test_processor_exception_does_not_kill_pipeline():
    received: List[int] = []

    async def processor(msg: int) -> None:
        if msg == 3:
            raise ValueError("bad message")
        received.append(msg)

    await run_pipeline(_as_stream(list(range(6))), processor)
    # All messages except the failing one should have been processed.
    assert 3 not in received
    assert len(received) == 5


async def test_concurrency_cap_enforced():
    """Never more than max_concurrent tasks running simultaneously."""
    cap = 5
    active = 0
    peak = 0
    barrier = asyncio.Event()

    async def processor(msg: int) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)  # yield to allow other tasks to start
        active -= 1

    await run_pipeline(_as_stream(list(range(20))), processor, max_concurrent=cap)
    assert peak <= cap


async def test_empty_stream_completes_immediately():
    await run_pipeline(_as_stream([]), lambda _: asyncio.sleep(0))


async def test_default_cap_is_500():
    assert MAX_CONCURRENT_TASKS == 500
