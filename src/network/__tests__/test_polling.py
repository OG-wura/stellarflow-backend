import pytest
import asyncio
import time
from src.network.polling import RegionalPollingEngine

class MockResponse:
    def __init__(self, status, json_data):
        self.status = status
        self._json_data = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def json(self):
        return self._json_data

@pytest.mark.asyncio
async def test_polling_engine_enforces_2500ms_timeout_cutoff(monkeypatch):
    """
    Asserts that if an endpoint hangs indefinitely, the engine drops it at 2500ms
    and returns a clean failure object without stalling other tasks.
    """
    # 1. Setup a normal fast region alongside an un-responsive lagging region
    endpoints = {
        "FAST-NODE": "https://fast.node/api",
        "LAGGING-NODE": "https://laggy.node/api"
    }
    
    engine = RegionalPollingEngine(endpoints)

    # 2. Mock aiohttp.ClientSession.get to simulate varying endpoint performance
    async def mock_get(self, url, **kwargs):
        if "fast" in url:
            return MockResponse(200, {"metrics": "nominal"})
        elif "laggy" in url:
            # Simulate a 10-second backend hang, well beyond our 2.5s cutoff
            await asyncio.sleep(10.0)
            return MockResponse(200, {"metrics": "delayed"})
        return MockResponse(404, {})

    monkeypatch.setattr("aiohttp.ClientSession.get", mock_get)

    start_time = time.monotonic()
    results = await engine.poll_all_regions_concurrently()
    duration = time.monotonic() - start_time

    # 3. Assertions and Structural Checks
    # The overall run should finish shortly after the 2.5s limit, not waiting 10s
    assert duration < 3.0, f"The execution pool stalled! Total duration took too long: {duration}s"
    
    fast_node_res = next(r for r in results if r["region"] == "FAST-NODE")
    laggy_node_res = next(r for r in results if r["region"] == "LAGGING-NODE")

    # Fast node should return successfully
    assert fast_node_res["status"] == "SUCCESS"
    assert fast_node_res["payload"]["metrics"] == "nominal"

    # Lagging node should be caught and dropped cleanly by the timeout guard
    assert laggy_node_res["status"] == "TIMEOUT"
    assert "2500ms threshold" in laggy_node_res["error"]