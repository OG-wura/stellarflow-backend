from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from network.polling import POLL_TIMEOUT_S, poll_endpoints


pytestmark = pytest.mark.asyncio(loop_scope="function")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fast_fetcher(endpoint: str) -> str:
    return f"ok:{endpoint}"


async def _slow_fetcher(endpoint: str) -> str:
    await asyncio.sleep(POLL_TIMEOUT_S + 1)
    return "never"


async def _error_fetcher(endpoint: str) -> str:
    raise RuntimeError("upstream down")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_successful_endpoints_return_results():
    results = await poll_endpoints(_fast_fetcher, ["http://a", "http://b"])
    assert results["http://a"] == "ok:http://a"
    assert results["http://b"] == "ok:http://b"


async def test_timed_out_endpoint_returns_none():
    results = await poll_endpoints(_slow_fetcher, ["http://slow"])
    assert results["http://slow"] is None


async def test_timeout_does_not_exceed_bound():
    start = time.monotonic()
    await poll_endpoints(_slow_fetcher, ["http://slow"])
    elapsed = time.monotonic() - start
    # Should finish shortly after POLL_TIMEOUT_S, not hang
    assert elapsed < POLL_TIMEOUT_S + 1.0


async def test_failing_endpoint_returns_none():
    results = await poll_endpoints(_error_fetcher, ["http://bad"])
    assert results["http://bad"] is None


async def test_mixed_endpoints_healthy_unaffected_by_slow():
    """Slow endpoint must not delay result from fast endpoint."""

    async def mixed_fetcher(endpoint: str) -> str:
        if "slow" in endpoint:
            await asyncio.sleep(POLL_TIMEOUT_S + 1)
        return f"ok:{endpoint}"

    results = await poll_endpoints(mixed_fetcher, ["http://fast", "http://slow"])
    assert results["http://fast"] == "ok:http://fast"
    assert results["http://slow"] is None


async def test_empty_endpoints_returns_empty_dict():
    results = await poll_endpoints(_fast_fetcher, [])
    assert results == {}


async def test_all_keys_present_in_result():
    endpoints = ["http://a", "http://b", "http://c"]
    results = await poll_endpoints(_fast_fetcher, endpoints)
    assert set(results.keys()) == set(endpoints)
