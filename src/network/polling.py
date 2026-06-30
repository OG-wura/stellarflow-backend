from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from network.http_client import FetchTimeoutError, fetch_json, make_session

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS: int = 10_000
DEFAULT_POLL_INTERVAL_S: float = DEFAULT_POLL_INTERVAL_MS / 1000


class RegionalPollingEngine:
    def __init__(self, endpoints: Dict[str, str]):
        """
        Initializes the engine with a directory map of regional exchange endpoints.
        Example: {"US-EAST": "https://us.exchange...", "EU-WEST": "https://eu.exchange..."}
        """
        self.endpoints = endpoints

    async def _fetch_regional_data(self, session, region: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Fetches telemetry metrics from a single regional endpoint using the adaptive timeout from http_client.
        """
        try:
            logger.debug("Dispatching async request to region [%s] -> %s", region, url)
            data = await fetch_json(session, url)
            if isinstance(data, dict):
                return {"region": region, "status": "SUCCESS", "payload": data}
            logger.warning("Region [%s] returned non-dict payload", region)
            return {"region": region, "status": "ERROR", "code": "non-dict payload"}
        except FetchTimeoutError:
            logger.error("Region [%s] timed out", region)
            return {"region": region, "status": "TIMEOUT", "error": "adaptive timeout breached"}
        except Exception as e:
            logger.error("Transport connectivity breakdown for region [%s]: %s", region, str(e))
            return {"region": region, "status": "TRANSPORT_FAILURE", "error": str(e)}

    async def poll_all_regions_concurrently(self) -> List[Dict[str, Any]]:
        """
        Orchestrates parallel non-blocking evaluation of all regional endpoints.
        Slow routes are safely dropped without stalling processing cycles for healthy paths.
        """
        start_time = time.monotonic()
        logger.info("Initializing concurrent poll cycle across %d endpoints...", len(self.endpoints))

        async with make_session() as session:
            tasks = [
                self._fetch_regional_data(session, region, url)
                for region, url in self.endpoints.items()
            ]

            results = await asyncio.gather(*tasks, return_exceptions=False)

            total_duration = (time.monotonic() - start_time) * 1000
            logger.info("Completed concurrent polling cycle in %.2fms total.", total_duration)
            return list(results)


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
