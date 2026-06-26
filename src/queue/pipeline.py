"""queue/pipeline.py – Semaphore-guarded async ingestion pipeline.

An ``asyncio.Semaphore`` with a ceiling of ``MAX_CONCURRENT_TASKS`` (500)
ensures that at most 500 processing tasks run at the same time.  When the
ceiling is reached, acquiring the semaphore blocks the stream-reading loop
until a slot is freed, applying back-pressure without dropping messages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterable, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Hard ceiling on simultaneously active processing tasks.
MAX_CONCURRENT_TASKS: int = 500

Processor = Callable[[Any], Awaitable[None]]


async def run_pipeline(
    stream: AsyncIterable[Any],
    processor: Processor,
    *,
    max_concurrent: int = MAX_CONCURRENT_TASKS,
) -> None:
    """Drain *stream*, processing each message under a semaphore back-pressure guard.

    Acquiring the semaphore *before* spawning each task means the ``async for``
    loop stalls at the ``await semaphore.acquire()`` call whenever ``max_concurrent``
    tasks are in flight, preventing unbounded task/memory growth during surges.

    Parameters
    ----------
    stream:
        Async iterable of raw market messages.
    processor:
        Async callable invoked once per message.  Exceptions are caught,
        logged, and swallowed so a bad message cannot kill the pipeline.
    max_concurrent:
        Hard ceiling on simultaneous tasks.  Defaults to ``MAX_CONCURRENT_TASKS``.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    tasks: set[asyncio.Task[None]] = set()

    async def _run(message: Any) -> None:
        try:
            await processor(message)
        except Exception as exc:  # noqa: BLE001
            logger.error("[Pipeline] Processor error: %s", exc)
        finally:
            semaphore.release()

    async for message in stream:
        # Block here when max_concurrent slots are taken — back-pressures the loop.
        await semaphore.acquire()
        task = asyncio.create_task(_run(message))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    # Drain remaining in-flight tasks before returning.
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["MAX_CONCURRENT_TASKS", "run_pipeline"]
