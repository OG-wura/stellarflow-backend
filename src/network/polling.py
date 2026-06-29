import asyncio
import logging
import time
from typing import Dict, List, Any, Optional
import aiohttp

logger = logging.getLogger("Network.Polling")

# Strict operational threshold limits
REGIONAL_TIMEOUT_SECONDS = 2.5  # 2500ms threshold bounds

class RegionalPollingEngine:
    def __init__(self, endpoints: Dict[str, str]):
        """
        Initializes the engine with a directory map of regional exchange endpoints.
        Example: {"US-EAST": "https://us.exchange...", "EU-WEST": "https://eu.exchange..."}
        """
        self.endpoints = endpoints

    async def _fetch_regional_data(self, session: aiohttp.ClientSession, region: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Fetches telemetry metrics from a single regional endpoint protected by a 2500ms non-blocking gate.
        """
        start_time = time.monotonic()
        try:
            logger.debug(f"Dispatching async request to region [{region}] -> {url}")
            
            # Enforce strict 2500ms timeout bounds on the network call coroutine
            async with async_timeout(REGIONAL_TIMEOUT_SECONDS):
                async with session.get(url, allow_redirects=True) as response:
                    if response.status == 200:
                        data = await response.json()
                        latency = (time.monotonic() - start_time) * 1000
                        logger.info(f"Successful fetch from region [{region}] in {latency:.2f}ms")
                        return {"region": region, "status": "SUCCESS", "payload": data}
                    
                    logger.warning(f"Region [{region}] returned unsafe response status: {response.status}")
                    return {"region": region, "status": "ERROR", "code": response.status}

        except asyncio.TimeoutError:
            duration = (time.monotonic() - start_time) * 1000
            logger.error(f"Execution boundary breached! Region [{region}] timed out after {duration:.2f}ms (Limit: 2500ms)")
            return {"region": region, "status": "TIMEOUT", "error": "2500ms threshold bound breached"}
        
        except aiohttp.ClientError as e:
            logger.error(f"Transport connectivity breakdown for region [{region}]: {str(e)}")
            return {"region": region, "status": "TRANSPORT_FAILURE", "error": str(e)}
            
        except Exception as e:
            logger.error(f"Uncaught intercept failure inside coroutine pool for region [{region}]: {str(e)}")
            return {"region": region, "status": "INTERNAL_EXCEPTION", "error": str(e)}

    async def poll_all_regions_concurrently(self) -> List[Dict[str, Any]]:
        """
        Orchestrates parallel non-blocking evaluation of all regional endpoints.
        Slow routes are safely dropped without stalling processing cycles for healthy paths.
        """
        start_time = time.monotonic()
        logger.info(f"Initializing concurrent poll cycle across {len(self.endpoints)} endpoints...")

        # Configure connection limits to optimize socket pool usage
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            # Build the task array list mapping out regional targets
            tasks = [
                self._fetch_regional_data(session, region, url)
                for region, url in self.endpoints.items()
            ]

            # Trigger a non-blocking gather execution, harvesting results as a block
            results = await asyncio.gather(*tasks, return_exceptions=False)
            
            total_duration = (time.monotonic() - start_time) * 1000
            logger.info(f"Completed concurrent polling cycle in {total_duration:.2f}ms total.")
            return list(results)

def async_timeout(seconds: float):
    """Utility abstraction tracking unified async timeout parameters across Python runtimes."""
    return asyncio.timeout(seconds) if hasattr(asyncio, 'timeout') else asyncio.wait_for
"""network/polling.py – Bounded asynchronous task collections for periodic price checks.

Background polling loops must not spawn unchecked, floating ``asyncio.create_task``
coroutines.  Each tracking interval is modelled as a structured
:class:`asyncio.TaskGroup` so every auxiliary worker is explicitly awaited (or
cancelled) before the next cycle begins.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from network.http_client import FetchTimeoutError, fetch_json, make_session

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS: int = 10_000
DEFAULT_POLL_INTERVAL_S: float = DEFAULT_POLL_INTERVAL_MS / 1000

PriceCheckHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
PriceFetcher = Callable[[Any, str], Awaitable[Optional[Dict[str, Any]]]]


async def _default_fetch_price(session: Any, url: str) -> Optional[Dict[str, Any]]:
    """Fetch JSON price payload from *url* using the shared HTTP client."""
    try:
        payload = await fetch_json(session, url)
        if isinstance(payload, dict):
            return payload
        logger.warning("Price check returned non-dict payload from %s", url)
    except FetchTimeoutError:
        logger.warning("Price check timed out for %s", url)
    except Exception as exc:
        logger.error("Price check failed for %s: %s", url, exc)
    return None


async def run_bounded_price_checks(
    session: Any,
    endpoints: List[str],
    fetch_price: PriceFetcher,
) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    """Execute one polling interval with a bounded :class:`asyncio.TaskGroup`.

    All workers spawned for this interval are joined before this coroutine
    returns — no task escapes the interval boundary.

    Returns
    -------
    list[tuple[str, dict | None]]
        ``(endpoint_url, payload)`` pairs in endpoint order.
    """
    if not endpoints:
        return []

    task_by_url: Dict[str, asyncio.Task[Optional[Dict[str, Any]]]] = {}

    async with asyncio.TaskGroup() as group:
        for url in endpoints:
            task_by_url[url] = group.create_task(fetch_price(session, url))

    return [(url, task_by_url[url].result()) for url in endpoints]


async def poll_price_checks(
    endpoints: List[str],
    on_price: PriceCheckHandler,
    *,
    stop_event: Optional[asyncio.Event] = None,
    interval_s: float = DEFAULT_POLL_INTERVAL_S,
    fetch_price: Optional[PriceFetcher] = None,
) -> None:
    """Poll exchange endpoints on a fixed interval with bounded task groups.

    Parameters
    ----------
    endpoints:
        REST URLs to query each interval.
    on_price:
        Async callback invoked with ``(url, payload)`` for every successful
        fetch in the completed interval.
    stop_event:
        When set, the loop exits after the current interval finishes.
    interval_s:
        Seconds between the *start* of consecutive intervals.
    fetch_price:
        Optional override for the per-endpoint fetch coroutine (used in tests).
    """
    if stop_event is None:
        stop_event = asyncio.Event()

    fetcher = fetch_price or _default_fetch_price

    async with make_session() as session:
        while not stop_event.is_set():
            results = await run_bounded_price_checks(session, endpoints, fetcher)

            for url, payload in results:
                if payload is not None:
                    await on_price(url, payload)

            if stop_event.is_set():
                break

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass


__all__ = [
    "DEFAULT_POLL_INTERVAL_MS",
    "DEFAULT_POLL_INTERVAL_S",
    "run_bounded_price_checks",
    "poll_price_checks",
]
