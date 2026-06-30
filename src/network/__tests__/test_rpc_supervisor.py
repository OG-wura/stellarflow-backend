import pytest
import asyncio
from src.network.nonce_tracker import PredictiveRPCSupervisor, HorizonNodeProfile

class MockResponseContext:
    def __init__(self, status):
        self.status = status
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc_val, exc_tb): pass

@pytest.mark.asyncio
async def test_rpc_supervisor_preemptive_failover_logic(monkeypatch):
    """
    Asserts that the supervisor instantly shifts traffic to secondary backup nodes
    if the primary node's health drops below acceptable bounds.
    """
    primary = [{"name": "primary-node-01", "url": "https://primary.stellar.org"}]
    fallback = [{"name": "backup-node-01", "url": "https://backup.stellar.org"}]
    
    supervisor = PredictiveRPCSupervisor(primary, fallback)
    assert supervisor.get_active_endpoint_url() == "https://primary.stellar.org"

    # Mock client session requests to simulate primary node failure and backup success
    async def mock_get(self, url, **kwargs):
        if "primary" in url:
            # Simulate network timeout block
            await asyncio.sleep(2.0)
            return MockResponseContext(504)
        return MockResponseContext(200)

    monkeypatch.setattr("aiohttp.ClientSession.get", mock_get)

    # Trigger predictive health evaluation cycles
    await supervisor.run_predictive_ping_cycle()

    # The supervisor should notice the primary node timeout and shift to the backup node
    assert supervisor.active_node.is_healthy is False
    assert supervisor.get_active_endpoint_url() == "https://backup.stellar.org"