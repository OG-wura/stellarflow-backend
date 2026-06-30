import logging
import requests
from typing import List, Dict, Any
from network.nonce_tracker import RPCNodeFailoverSupervisor

logger = logging.getLogger(__name__)


class FailoverRouter:
    """Automated RPC Endpoint Switching Routine.

    Automatically switches data transmission paths to backup node endpoints 
    using a proactive RPC supervisor to avoid connection timeouts.
    """

    def __init__(self, primary_endpoint: str, backup_endpoints: List[str]):
        self.primary_endpoint = primary_endpoint
        self.backup_endpoints = backup_endpoints
        self.timeout_sec = 3.5  # 3500ms window
        self.supervisor = RPCNodeFailoverSupervisor(
            endpoints=[primary_endpoint] + backup_endpoints,
            check_interval_sec=2.0,
            latency_threshold_ms=500.0,
            ping_timeout_sec=1.0,
        )
        self.supervisor.start()

    def transmit(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        active_url = self.supervisor.get_active_endpoint()
        endpoints = [active_url] + [
            ep for ep in self.supervisor.endpoints if ep != active_url
        ]

        for url in endpoints:
            target_url = f"{url.rstrip('/')}/{path.lstrip('/')}"
            try:
                response = requests.post(
                    target_url, json=payload, timeout=self.timeout_sec
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout:
                logger.warning(
                    f"Node {target_url} timed out after {self.timeout_sec}s. Switching to backup."
                )
            except requests.exceptions.RequestException as e:
                logger.warning(
                    f"Node {target_url} failed: {e}. Switching to backup."
                )

        raise ConnectionError("All RPC endpoints failed to respond.")

    def close(self) -> None:
        """Stop the proactive supervisor thread."""
        try:
            self.supervisor.stop()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

