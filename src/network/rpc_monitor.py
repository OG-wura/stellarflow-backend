"""network/rpc_monitor.py - Dynamic RPC monitoring framework with predictive failover.

This module provides a comprehensive health monitoring system for Horizon RPC nodes
with predictive failover capabilities. It performs lightweight periodic health checks,
tracks performance metrics, and automatically shifts traffic to healthy nodes before
complete failure occurs.

Key features:
- Health scoring system (0-100) based on latency, success rate, and error patterns
- Predictive degradation detection using moving averages and trend analysis
- Automatic traffic shifting to backup nodes when health drops below thresholds
- Background periodic health checks with configurable intervals
- Thread-safe operations for concurrent access
- Integration with existing horizon_pool and http_client modules
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
import requests
import urllib3

logger = logging.getLogger(__name__)

# Constants
DEFAULT_HEALTH_CHECK_INTERVAL_S: float = 5.0
DEFAULT_HEALTH_THRESHOLD: float = 70.0
DEFAULT_CRITICAL_THRESHOLD: float = 30.0
DEFAULT_LATENCY_THRESHOLD_MS: float = 2000.0
DEFAULT_SUCCESS_RATE_THRESHOLD: float = 0.95
MAX_METRIC_HISTORY: int = 100
HEALTH_CHECK_TIMEOUT_S: float = 2.0


@dataclass
class NodeMetrics:
    """Performance metrics for a single RPC node."""
    
    url: str
    latency_samples: deque = field(default_factory=lambda: deque(maxlen=MAX_METRIC_HISTORY))
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_check_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    health_score: float = 100.0
    is_healthy: bool = True
    error_types: Dict[str, int] = field(default_factory=dict)
    
    @property
    def total_requests(self) -> int:
        return self.success_count + self.failure_count
    
    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.success_count / self.total_requests
    
    @property
    def avg_latency_ms(self) -> float:
        if not self.latency_samples:
            return 0.0
        return sum(self.latency_samples) / len(self.latency_samples)
    
    @property
    def p95_latency_ms(self) -> float:
        if not self.latency_samples:
            return 0.0
        sorted_samples = sorted(self.latency_samples)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[idx]


@dataclass
class HealthCheckResult:
    """Result of a single health check."""
    
    node_url: str
    success: bool
    latency_ms: float
    timestamp: datetime
    error: Optional[str] = None


class RPCMonitor:
    """
    Dynamic RPC monitoring framework with predictive failover.
    
    Monitors node health using lightweight periodic checks and shifts traffic
    to secondary backup nodes instantly if performance drops below thresholds.
    """
    
    def __init__(
        self,
        primary_endpoint: str,
        backup_endpoints: List[str],
        *,
        health_check_interval_s: float = DEFAULT_HEALTH_CHECK_INTERVAL_S,
        health_threshold: float = DEFAULT_HEALTH_THRESHOLD,
        critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
        latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
        success_rate_threshold: float = DEFAULT_SUCCESS_RATE_THRESHOLD,
        on_failover: Optional[Callable[[str, str], None]] = None
    ):
        """
        Initialize the RPC monitor.
        
        Parameters
        ----------
        primary_endpoint:
            The primary RPC endpoint URL.
        backup_endpoints:
            List of backup RPC endpoint URLs.
        health_check_interval_s:
            Interval between health checks in seconds.
        health_threshold:
            Health score threshold below which node is considered degraded.
        critical_threshold:
            Health score threshold below which node is considered critical.
        latency_threshold_ms:
            Latency threshold in milliseconds for health scoring.
        success_rate_threshold:
            Minimum success rate for healthy node.
        on_failover:
            Optional callback function called when failover occurs.
            Receives (old_url, new_url) as arguments.
        """
        self.primary_endpoint = primary_endpoint
        self.backup_endpoints = backup_endpoints
        self.all_endpoints = [primary_endpoint] + backup_endpoints
        
        self.health_check_interval_s = health_check_interval_s
        self.health_threshold = health_threshold
        self.critical_threshold = critical_threshold
        self.latency_threshold_ms = latency_threshold_ms
        self.success_rate_threshold = success_rate_threshold
        self.on_failover = on_failover
        
        self.node_metrics: Dict[str, NodeMetrics] = {
            url: NodeMetrics(url=url) for url in self.all_endpoints
        }
        
        self.current_endpoint = primary_endpoint
        self._monitoring_active = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        
        logger.info(
            "[RPCMonitor] Initialized | primary=%s | backups=%d | interval=%.1fs",
            primary_endpoint,
            len(backup_endpoints),
            health_check_interval_s
        )
    
    def start_monitoring(self) -> None:
        """Start background health monitoring."""
        with self._lock:
            if self._monitoring_active:
                logger.warning("[RPCMonitor] Monitoring already active")
                return
            
            self._monitoring_active = True
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="RPCMonitor"
            )
            self._monitor_thread.start()
            logger.info("[RPCMonitor] Background monitoring started")
    
    def stop_monitoring(self) -> None:
        """Stop background health monitoring."""
        with self._lock:
            if not self._monitoring_active:
                return
            
            self._monitoring_active = False
            if self._monitor_thread:
                self._monitor_thread.join(timeout=5.0)
        
        logger.info("[RPCMonitor] Background monitoring stopped")
    
    def _monitor_loop(self) -> None:
        """Main monitoring loop running in background thread."""
        while self._monitoring_active:
            try:
                self._perform_health_checks()
                self._evaluate_node_health()
                self._check_failover_needed()
            except Exception as exc:
                logger.error("[RPCMonitor] Health check loop error: %s", exc)
            
            time.sleep(self.health_check_interval_s)
    
    def _perform_health_checks(self) -> None:
        """Perform lightweight health checks on all nodes."""
        for url in self.all_endpoints:
            result = self._check_node_health(url)
            self._update_metrics(result)
    
    def _check_node_health(self, url: str) -> HealthCheckResult:
        """
        Perform a lightweight health check on a single node.
        
        Uses a simple GET request to the root endpoint or a lightweight
        status endpoint if available.
        """
        start_time = time.time()
        timestamp = datetime.now(timezone.utc)
        
        try:
            response = requests.get(
                url,
                timeout=HEALTH_CHECK_TIMEOUT_S,
                headers={"Accept": "application/json"}
            )
            latency_ms = (time.time() - start_time) * 1000
            
            if response.status_code < 500:
                return HealthCheckResult(
                    node_url=url,
                    success=True,
                    latency_ms=latency_ms,
                    timestamp=timestamp
                )
            else:
                return HealthCheckResult(
                    node_url=url,
                    success=False,
                    latency_ms=latency_ms,
                    timestamp=timestamp,
                    error=f"HTTP {response.status_code}"
                )
        except requests.exceptions.Timeout:
            return HealthCheckResult(
                node_url=url,
                success=False,
                latency_ms=HEALTH_CHECK_TIMEOUT_S * 1000,
                timestamp=timestamp,
                error="Timeout"
            )
        except Exception as exc:
            return HealthCheckResult(
                node_url=url,
                success=False,
                latency_ms=(time.time() - start_time) * 1000,
                timestamp=timestamp,
                error=str(exc)
            )
    
    def _update_metrics(self, result: HealthCheckResult) -> None:
        """Update node metrics with health check result."""
        with self._lock:
            metrics = self.node_metrics[result.node_url]
            metrics.last_check_time = result.timestamp
            
            if result.success:
                metrics.success_count += 1
                metrics.consecutive_failures = 0
                metrics.last_success_time = result.timestamp
                metrics.latency_samples.append(result.latency_ms)
            else:
                metrics.failure_count += 1
                metrics.consecutive_failures += 1
                error_type = result.error or "unknown"
                metrics.error_types[error_type] = metrics.error_types.get(error_type, 0) + 1
    
    def _evaluate_node_health(self) -> None:
        """Calculate health scores for all nodes."""
        with self._lock:
            for url, metrics in self.node_metrics.items():
                health_score = self._calculate_health_score(metrics)
                metrics.health_score = health_score
                metrics.is_healthy = health_score >= self.health_threshold
                
                logger.debug(
                    "[RPCMonitor] Node health | url=%s | score=%.1f | healthy=%s | avg_latency=%.1fms | success_rate=%.2f",
                    url,
                    health_score,
                    metrics.is_healthy,
                    metrics.avg_latency_ms,
                    metrics.success_rate
                )
    
    def _calculate_health_score(self, metrics: NodeMetrics) -> float:
        """
        Calculate health score (0-100) based on multiple factors.
        
        Scoring algorithm:
        - Latency score: 100 if below threshold, decreases linearly
        - Success rate score: 100 if above threshold, decreases linearly
        - Consecutive failures: Heavy penalty for multiple failures in a row
        - Final score: Weighted average of all factors
        """
        # Latency score (40% weight)
        if metrics.avg_latency_ms == 0:
            latency_score = 100.0
        elif metrics.avg_latency_ms <= self.latency_threshold_ms:
            latency_score = 100.0
        else:
            latency_score = max(0.0, 100.0 - (metrics.avg_latency_ms - self.latency_threshold_ms) / 10)
        
        # Success rate score (40% weight)
        success_rate = metrics.success_rate
        if success_rate >= self.success_rate_threshold:
            success_score = 100.0
        else:
            success_score = max(0.0, (success_rate / self.success_rate_threshold) * 100)
        
        # Consecutive failures penalty (20% weight)
        failure_penalty = min(100.0, metrics.consecutive_failures * 20)
        consecutive_score = max(0.0, 100.0 - failure_penalty)
        
        # Weighted average
        health_score = (
            latency_score * 0.4 +
            success_score * 0.4 +
            consecutive_score * 0.2
        )
        
        return health_score
    
    def _check_failover_needed(self) -> None:
        """Check if failover is needed and execute if required."""
        with self._lock:
            current_metrics = self.node_metrics[self.current_endpoint]
            
            # Failover if current node is critical
            if current_metrics.health_score < self.critical_threshold:
                logger.warning(
                    "[RPCMonitor] Current node critical | url=%s | score=%.1f",
                    self.current_endpoint,
                    current_metrics.health_score
                )
                self._perform_failover()
            
            # Predictive failover if current node is degraded and trending down
            elif current_metrics.health_score < self.health_threshold:
                if self._is_degrading(current_metrics):
                    logger.warning(
                        "[RPCMonitor] Current node degrading | url=%s | score=%.1f",
                        self.current_endpoint,
                        current_metrics.health_score
                    )
                    self._perform_failover()
    
    def _is_degrading(self, metrics: NodeMetrics) -> bool:
        """
        Detect if a node is degrading based on trend analysis.
        
        Checks if:
        - Recent latency samples are increasing
        - Success rate is dropping
        - Consecutive failures are increasing
        """
        if len(metrics.latency_samples) < 5:
            return False
        
        # Check if latency is trending up
        recent_latencies = list(metrics.latency_samples)[-5:]
        if recent_latencies[-1] > recent_latencies[0] * 1.5:
            return True
        
        # Check for consecutive failures
        if metrics.consecutive_failures >= 3:
            return True
        
        return False
    
    def _perform_failover(self) -> None:
        """Perform failover to the healthiest available node."""
        old_url = self.current_endpoint
        
        # Find healthiest node (excluding current)
        healthy_nodes = [
            (url, metrics)
            for url, metrics in self.node_metrics.items()
            if url != old_url and metrics.is_healthy
        ]
        
        if not healthy_nodes:
            # No healthy nodes, pick the one with highest health score
            all_nodes = [
                (url, metrics)
                for url, metrics in self.node_metrics.items()
                if url != old_url
            ]
            if all_nodes:
                healthy_nodes = all_nodes
        
        if healthy_nodes:
            # Sort by health score descending
            healthy_nodes.sort(key=lambda x: x[1].health_score, reverse=True)
            new_url = healthy_nodes[0][0]
            
            self.current_endpoint = new_url
            
            logger.error(
                "[RPCMonitor] FAILOVER | old=%s | new=%s | old_score=%.1f | new_score=%.1f",
                old_url,
                new_url,
                self.node_metrics[old_url].health_score,
                self.node_metrics[new_url].health_score
            )
            
            if self.on_failover:
                self.on_failover(old_url, new_url)
        else:
            logger.error("[RPCMonitor] No available nodes for failover")
    
    def get_current_endpoint(self) -> str:
        """Get the current active endpoint."""
        with self._lock:
            return self.current_endpoint
    
    def get_node_health(self, url: str) -> Optional[NodeMetrics]:
        """Get health metrics for a specific node."""
        with self._lock:
            return self.node_metrics.get(url)
    
    def get_all_node_health(self) -> Dict[str, NodeMetrics]:
        """Get health metrics for all nodes."""
        with self._lock:
            return dict(self.node_metrics)
    
    def get_health_summary(self) -> Dict[str, any]:
        """Get a summary of all node health statuses."""
        with self._lock:
            return {
                "current_endpoint": self.current_endpoint,
                "monitoring_active": self._monitoring_active,
                "nodes": [
                    {
                        "url": url,
                        "health_score": metrics.health_score,
                        "is_healthy": metrics.is_healthy,
                        "avg_latency_ms": metrics.avg_latency_ms,
                        "success_rate": metrics.success_rate,
                        "consecutive_failures": metrics.consecutive_failures,
                        "total_requests": metrics.total_requests
                    }
                    for url, metrics in self.node_metrics.items()
                ]
            }
    
    def record_request_result(self, url: str, success: bool, latency_ms: float) -> None:
        """
        Record the result of an actual RPC request.
        
        This allows the monitor to incorporate real request data
        into health calculations, not just health checks.
        """
        result = HealthCheckResult(
            node_url=url,
            success=success,
            latency_ms=latency_ms,
            timestamp=datetime.now(timezone.utc)
        )
        self._update_metrics(result)
        self._evaluate_node_health()
    
    def manually_trigger_failover(self) -> str:
        """Manually trigger a failover to the next best node."""
        with self._lock:
            old_url = self.current_endpoint
            self._perform_failover()
            return self.current_endpoint if self.current_endpoint != old_url else old_url


class PredictiveFailoverRouter:
    """
    High-level router that combines RPC monitoring with request routing.
    
    This class integrates the RPCMonitor with the actual request routing logic,
    providing automatic failover during request execution.
    """
    
    def __init__(
        self,
        primary_endpoint: str,
        backup_endpoints: List[str],
        *,
        enable_monitoring: bool = True,
        **monitor_kwargs
    ):
        """
        Initialize the predictive failover router.
        
        Parameters
        ----------
        primary_endpoint:
            The primary RPC endpoint URL.
        backup_endpoints:
            List of backup RPC endpoint URLs.
        enable_monitoring:
            Whether to start background monitoring automatically.
        **monitor_kwargs:
            Additional arguments passed to RPCMonitor.
        """
        self.monitor = RPCMonitor(
            primary_endpoint=primary_endpoint,
            backup_endpoints=backup_endpoints,
            **monitor_kwargs
        )
        
        if enable_monitoring:
            self.monitor.start_monitoring()
    
    def transmit(
        self,
        path: str,
        payload: Dict,
        *,
        timeout: float = 3.5,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict:
        """
        Transmit a request with automatic failover.
        
        Parameters
        ----------
        path:
            The API path to request.
        payload:
            The request payload.
        timeout:
            Request timeout in seconds.
        headers:
            Optional request headers.
        
        Returns
        -------
        Dict
            The response JSON.
        
        Raises
        ------
        ConnectionError:
            If all endpoints fail.
        """
        endpoints = [self.monitor.get_current_endpoint()] + self.monitor.backup_endpoints
        
        for url in endpoints:
            target_url = f"{url.rstrip('/')}/{path.lstrip('/')}"
            start_time = time.time()
            
            try:
                response = requests.post(
                    target_url,
                    json=payload,
                    timeout=timeout,
                    headers=headers
                )
                response.raise_for_status()
                
                latency_ms = (time.time() - start_time) * 1000
                self.monitor.record_request_result(url, True, latency_ms)
                
                return response.json()
            
            except requests.exceptions.Timeout:
                latency_ms = timeout * 1000
                self.monitor.record_request_result(url, False, latency_ms)
                logger.warning(
                    "[PredictiveRouter] Node timed out | url=%s | latency=%.1fms",
                    target_url,
                    latency_ms
                )
            
            except requests.exceptions.RequestException as exc:
                latency_ms = (time.time() - start_time) * 1000
                self.monitor.record_request_result(url, False, latency_ms)
                logger.warning(
                    "[PredictiveRouter] Node failed | url=%s | error=%s",
                    target_url,
                    exc
                )
        
        raise ConnectionError("All RPC endpoints failed to respond")
    
    def stop(self) -> None:
        """Stop the monitoring background thread."""
        self.monitor.stop_monitoring()
    
    def get_health_summary(self) -> Dict[str, any]:
        """Get health summary of all nodes."""
        return self.monitor.get_health_summary()


__all__ = [
    "RPCMonitor",
    "NodeMetrics",
    "HealthCheckResult",
    "PredictiveFailoverRouter",
    "DEFAULT_HEALTH_CHECK_INTERVAL_S",
    "DEFAULT_HEALTH_THRESHOLD",
    "DEFAULT_CRITICAL_THRESHOLD",
    "DEFAULT_LATENCY_THRESHOLD_MS",
    "DEFAULT_SUCCESS_RATE_THRESHOLD",
]
