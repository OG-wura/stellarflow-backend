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


class RPCNodeFailoverSupervisor:
    """Proactive RPC node failover supervisor that monitors node connectivity.

    It maintains a list of endpoints and runs a background thread to check their
    latency and health using lightweight JSON-RPC requests. If the active node
    experiences a latency drop or fails, the supervisor instantly shifts the
    active traffic to the fastest available secondary node.

    Complexity:
    Time: O(1) for active endpoint lookup, O(N) for checking N endpoints.
    Space: O(N) to store latency stats for N endpoints.
    """

    def __init__(
        self,
        endpoints: Optional[List[str]] = None,
        check_interval_sec: float = 2.0,
        latency_threshold_ms: float = 500.0,
        ping_timeout_sec: float = 1.0,
    ) -> None:
        self.check_interval_sec = check_interval_sec
        self.latency_threshold_ms = latency_threshold_ms
        self.ping_timeout_sec = ping_timeout_sec

        if endpoints is None:
            primary = os.environ.get("RPC_URL")
            fallbacks = os.environ.get("FALLBACK_RPC_URLS")
            loaded = []
            if primary:
                loaded.append(primary.strip())
            if fallbacks:
                for f in fallbacks.split(","):
                    if f.strip():
                        loaded.append(f.strip())
            if not loaded:
                loaded = [
                    "https://rpc.testnet.stellar.org",
                    "https://rpc.mainnet.stellar.org",
                ]
            self.endpoints = loaded
        else:
            self.endpoints = list(endpoints)

        self._lock = threading.Lock()
        self._active_endpoint = self.endpoints[0] if self.endpoints else ""
        self._latencies: Dict[str, float] = {ep: 0.0 for ep in self.endpoints}
        self._healthy_endpoints: set = set(self.endpoints)

        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background monitoring thread."""
        with self._lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._run_monitor,
                name="RPCNodeFailoverSupervisor-Monitor",
                daemon=True,
            )
            self._monitor_thread.start()
            logger.info("[RPCNodeFailoverSupervisor] Started proactive background monitoring.")

    def stop(self) -> None:
        """Stop the background monitoring thread."""
        self._stop_event.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None
            logger.info("[RPCNodeFailoverSupervisor] Stopped background monitoring.")

    def get_active_endpoint(self) -> str:
        """Return the currently selected active RPC endpoint."""
        with self._lock:
            return self._active_endpoint

    def _ping_node(self, endpoint: str) -> Optional[float]:
        """Perform a fast, lightweight check on a single node and return its latency in ms."""
        try:
            start = time.time()
            response = requests.post(
                endpoint,
                json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
                timeout=self.ping_timeout_sec,
            )
            latency_ms = (time.time() - start) * 1000.0
            if response.status_code == 200:
                data = response.json()
                if "result" in data or "error" in data:
                    return latency_ms
            return None
        except Exception:
            return None

    def _run_monitor(self) -> None:
        """Main loop for the background monitoring thread."""
        while not self._stop_event.is_set():
            temp_latencies = {}
            temp_healthy = set()

            for ep in self.endpoints:
                latency = self._ping_node(ep)
                if latency is not None:
                    temp_latencies[ep] = latency
                    temp_healthy.add(ep)
                else:
                    temp_latencies[ep] = float("inf")

            with self._lock:
                self._latencies.update(temp_latencies)
                self._healthy_endpoints = temp_healthy

                active_ok = False
                active_latency = self._latencies.get(self._active_endpoint, float("inf"))

                if (
                    self._active_endpoint in self._healthy_endpoints
                    and active_latency <= self.latency_threshold_ms
                ):
                    active_ok = True

                if not active_ok:
                    best_endpoint = self._active_endpoint
                    best_latency = active_latency

                    for ep in self.endpoints:
                        ep_latency = self._latencies.get(ep, float("inf"))
                        if ep in self._healthy_endpoints and ep_latency < best_latency:
                            best_endpoint = ep
                            best_latency = ep_latency

                    if best_endpoint != self._active_endpoint:
                        logger.warning(
                            "[RPCNodeFailoverSupervisor] Shifted traffic from %s (latency: %.1fms) to %s (latency: %.1fms)",
                            self._active_endpoint,
                            active_latency,
                            best_endpoint,
                            best_latency,
                        )
                        self._active_endpoint = best_endpoint

            self._stop_event.wait(self.check_interval_sec)


rpc_supervisor = RPCNodeFailoverSupervisor()


__all__ = [
    "NonceTracker",
    "NonceWindow",
    "nonce_tracker",
    "nonce_window",
    "RPCNodeFailoverSupervisor",
    "rpc_supervisor",
]
