"""network/polling.py – Non-blocking coroutine pool for regional fallback endpoints.

Each endpoint is polled concurrently via ``asyncio.gather``.  Tasks that exceed
``POLL_TIMEOUT_S`` (2 500 ms) are cancelled and their connection contexts are
released before the gather returns, so a slow regional feed never stalls
healthy channels.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Hard per-task timeout in seconds (2 500 ms as required).
POLL_TIMEOUT_S: float = 2.5

Fetcher = Callable[[str], Awaitable[Any]]


class PollTimeoutError(Exception):
    """Raised when a single endpoint poll exceeds ``POLL_TIMEOUT_S``."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        super().__init__(f"Poll timed out after {POLL_TIMEOUT_S * 1000:.0f}ms: {endpoint!r}")


async def _poll_one(fetcher: Fetcher, endpoint: str) -> Optional[Any]:
    """Poll a single *endpoint* via *fetcher*, cancelling after ``POLL_TIMEOUT_S``.

    Returns the fetcher result on success, or ``None`` on timeout/error so that
    ``poll_endpoints`` can keep collecting results from healthy peers.
    """
    try:
        return await asyncio.wait_for(fetcher(endpoint), timeout=POLL_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning(
            "[Polling] Endpoint timed out after %.0fms, dropping context: %s",
            POLL_TIMEOUT_S * 1000,
            endpoint,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Polling] Endpoint error (%s): %s", endpoint, exc)
        return None


async def poll_endpoints(
    fetcher: Fetcher,
    endpoints: List[str],
) -> Dict[str, Any]:
    """Poll all *endpoints* concurrently and return a mapping of endpoint → result.

    Endpoints that time out or raise are mapped to ``None``; they never block
    results from healthy endpoints.  All tasks run inside a single
    ``asyncio.gather`` call so the overall wall-clock time is bounded by the
    slowest *successful* response, capped at ``POLL_TIMEOUT_S``.

    Parameters
    ----------
    fetcher:
        Async callable ``(endpoint: str) -> Any`` that fetches data for one URL.
    endpoints:
        List of regional endpoint URLs to poll in parallel.

    Returns
    -------
    dict
        ``{endpoint: result_or_None}`` for every entry in *endpoints*.
    """
    if not endpoints:
        return {}

    results: List[Optional[Any]] = await asyncio.gather(
        *(_poll_one(fetcher, ep) for ep in endpoints),
        return_exceptions=False,  # exceptions are already handled inside _poll_one
    )
    return dict(zip(endpoints, results))
