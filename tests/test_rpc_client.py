import os
import sys
import pytest
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from network.rpc_client import FailoverRouter
import requests


def test_failover_router_uses_supervisor_active_endpoint(monkeypatch) -> None:
    primary = "https://rpc-primary.stellar.org"
    backup = "https://rpc-backup.stellar.org"

    class MockResponse:
        status_code = 200

        def json(self):
            return {"result": {"status": "healthy"}}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        requests,
        "post",
        lambda url, json=None, timeout=None: MockResponse(),
    )

    router = FailoverRouter(primary_endpoint=primary, backup_endpoints=[backup])
    time.sleep(0.1)

    transmit_calls = []

    def mock_transmit_post(url, json=None, timeout=None):
        transmit_calls.append(url)
        return MockResponse()

    monkeypatch.setattr(requests, "post", mock_transmit_post)

    router.transmit("/submit", {"tx": "xyz"})

    assert len(transmit_calls) == 1
    assert "submit" in transmit_calls[0]

    router.supervisor.stop()
