import asyncio
import logging
import time
from typing import Dict, List, Optional, Any
import aiohttp

logger = logging.getLogger("Network.RPCSup")

# Threshold Parameters
LIGHTWEIGHT_PING_TIMEOUT = 0.8  # Max acceptable time window (800ms) before degradation warning
MOVING_AVG_WINDOW_SIZE = 4      # Number of historic latency checks to weigh mathematically

class HorizonNodeProfile:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.latency_history: List[float] = []
        self.is_healthy = True

    @property
    def moving_average_latency(self) -> float:
        """Calculates historical moving average execution latency parameters."""
        if not self.latency_history:
            return 0.0
        return sum(self.latency_history) / len(self.latency_history)

    def record_metric(self, latency_ms: float):
        """Appends latency sample to bounded historic window tracking loops."""
        self.latency_history.append(latency_ms)
        if len(self.latency_history) > MOVING_AVG_WINDOW_SIZE:
            self.latency_history.pop(0)


class PredictiveRPCSupervisor:
    def __init__(self, primary_endpoints: List[Dict[str, str]], fallback_endpoints: List[Dict[str, str]]):
        """
        Orchestrates network health scoring topologies across core and backup infrastructure arrays.
        Input format example: [{"name": "horizon-main", "url": "https://horizon.stellar.org"}]
        """
        self.primary_pool = [HorizonNodeProfile(node["name"], node["url"]) for node in primary_endpoints]
        self.fallback_pool = [HorizonNodeProfile(node["name"], node["url"]) for node in fallback_endpoints]
        self.active_node: HorizonNodeProfile = self.primary_pool[0]

    async def run_predictive_ping_cycle(self) -> None:
        """
        Executes parallel, lightweight validation pings across the cluster.
        Updates health statuses without introducing blocking execution lags to outer worker frameworks.
        """
        async with aiohttp.ClientSession() as session:
            tasks = []
            all_nodes = self.primary_pool + self.fallback_pool
            
            for node in all_nodes:
                tasks.append(self._probe_node_health(session, node))
            
            await asyncio.gather(*tasks)
        
        self._evaluate_routing_topology()

    async def _probe_node_health(self, session: aiohttp.ClientSession, node: HorizonNodeProfile) -> None:
        """
        Dispatches lightweight low-overhead endpoint probes to track real-time communication shifts.
        """
        # Horizon base path used for lightweight connection checks
        probe_url = f"{node.url.rstrip('/')}/"
        start_time = time.monotonic()
        
        try:
            async with asyncio.timeout(LIGHTWEIGHT_PING_TIMEOUT):
                async with session.get(probe_url) as response:
                    if response.status == 200:
                        latency_ms = (time.monotonic() - start_time) * 1000
                        node.record_metric(latency_ms)
                        
                        # Mark degraded if moving average indicates systematic latency decline
                        if node.moving_average_latency > (LIGHTWEIGHT_PING_TIMEOUT * 1000):
                            if node.is_healthy:
                                logger.warning(f"Predictive Warning: Performance degradation detected on {node.name}. Latency: {node.moving_average_latency:.1f}ms")
                            node.is_healthy = False
                        else:
                            node.is_healthy = True
                        return

                    node.is_healthy = False
                    logger.debug(f"Node {node.name} returned non-200 footprint status: {response.status}")
                    
        except (asyncio.TimeoutError, aiohttp.ClientError):
            node.is_healthy = False
            node.record_metric(LIGHTWEIGHT_PING_TIMEOUT * 1000 * 2) # Penalize metric tracking log
            logger.warn(f"Predictive Supervisor flagged node [{node.name}] as UNHEALTHY (Timeout/Network breakdown)")

    def _evaluate_routing_topology(self) -> None:
        """
        Dynamically shifts layout traffic pointers to healthier candidate environments.
        """
        # If active node is healthy and performing nominal processing, preserve active route
        if self.active_node.is_healthy:
            return

        logger.warn(f"Active Horizon Endpoint [{self.active_node.name}] degraded. Initializing preemptive failover routine...")
        
        # 1. Scan primary pool for an alternate healthy node
        for primary in self.primary_pool:
            if primary.is_healthy:
                self.active_node = primary
                logger.info(f"Traffic routing safely shifted to alternate primary node: [{self.active_node.name}]")
                return

        # 2. Fallback to secondary isolated backup arrays if full primary tier crashes
        for fallback in self.fallback_pool:
            if fallback.is_healthy:
                self.active_node = fallback
                logger.critical(f"EMERGENCY: Primary Horizon node array completely degraded! Failover routed to backup: [{self.active_node.name}]")
                return

        logger.error("CRITICAL FAILURE: Comprehensive Horizon node matrix completely unreachable. No healthy nodes found.")

    def get_active_endpoint_url(self) -> str:
        """Returns the currently active, validated node URL for ledger submissions."""
        return self.active_node.url