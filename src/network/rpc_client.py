import logging
import requests
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class FailoverRouter:
    """
    Automated RPC Endpoint Switching Routine.
    Automatically switches data transmission paths to backup node endpoints 
    if a target fails to respond within a 3500ms window.
    """
    
    def __init__(self, primary_endpoint: str, backup_endpoints: List[str]):
        self.primary_endpoint = primary_endpoint
        self.backup_endpoints = backup_endpoints
        self.timeout_sec = 3.5  # 3500ms window

    def transmit(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        endpoints = [self.primary_endpoint] + self.backup_endpoints
        
        for url in endpoints:
            target_url = f"{url.rstrip('/')}/{path.lstrip('/')}"
            try:
                response = requests.post(
                    target_url, 
                    json=payload, 
                    timeout=self.timeout_sec
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout:
                logger.warning(f"Node {target_url} timed out after {self.timeout_sec}s. Switching to backup.")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Node {target_url} failed: {e}. Switching to backup.")
                
        raise ConnectionError("All RPC endpoints failed to respond.")
