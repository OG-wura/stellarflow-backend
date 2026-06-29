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