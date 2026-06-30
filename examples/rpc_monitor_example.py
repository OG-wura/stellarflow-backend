"""Example usage of the RPC monitoring framework.

This example demonstrates how to use the RPCMonitor and PredictiveFailoverRouter
to achieve dynamic RPC monitoring with predictive failover.
"""

from src.network.rpc_monitor import (
    RPCMonitor,
    PredictiveFailoverRouter,
    DEFAULT_HEALTH_CHECK_INTERVAL_S,
    DEFAULT_HEALTH_THRESHOLD,
)


def example_basic_monitoring():
    """Basic example of RPC monitoring."""
    # Define your RPC endpoints
    primary = "https://horizon.stellar.org"
    backups = [
        "https://horizon-testnet.stellar.org",
        "https://horizon-fallback.example.com"
    ]
    
    # Create monitor with custom thresholds
    monitor = RPCMonitor(
        primary_endpoint=primary,
        backup_endpoints=backups,
        health_check_interval_s=5.0,  # Check every 5 seconds
        health_threshold=70.0,  # Below 70 is degraded
        critical_threshold=30.0,  # Below 30 is critical
        latency_threshold_ms=2000.0,  # 2 second latency threshold
        success_rate_threshold=0.95,  # 95% success rate required
        on_failover=lambda old, new: print(f"FAILOVER: {old} -> {new}")
    )
    
    # Start background monitoring
    monitor.start_monitoring()
    
    try:
        # Monitor runs in background
        # Your application continues normally
        import time
        time.sleep(30)  # Run for 30 seconds
        
        # Check health status
        health_summary = monitor.get_health_summary()
        print("Health Summary:", health_summary)
        
        # Get metrics for specific node
        node_health = monitor.get_node_health(primary)
        print(f"Primary node health: {node_health.health_score if node_health else 'N/A'}")
        
    finally:
        # Stop monitoring when done
        monitor.stop_monitoring()


def example_predictive_router():
    """Example using the high-level router with automatic failover."""
    primary = "https://horizon.stellar.org"
    backups = [
        "https://horizon-testnet.stellar.org"
    ]
    
    # Create router with monitoring enabled
    router = PredictiveFailoverRouter(
        primary_endpoint=primary,
        backup_endpoints=backups,
        enable_monitoring=True,
        health_check_interval_s=5.0
    )
    
    try:
        # Make requests with automatic failover
        payload = {"data": "example"}
        
        try:
            response = router.transmit(
                path="transactions",
                payload=payload,
                timeout=3.5
            )
            print("Response:", response)
        except ConnectionError as e:
            print("All endpoints failed:", e)
        
        # Check health status
        health = router.get_health_summary()
        print("Current endpoint:", health["current_endpoint"])
        print("Node health:", health["nodes"])
        
    finally:
        router.stop()


def example_manual_failover():
    """Example of manually triggering failover."""
    primary = "https://horizon.stellar.org"
    backups = ["https://horizon-testnet.stellar.org"]
    
    monitor = RPCMonitor(
        primary_endpoint=primary,
        backup_endpoints=backups
    )
    
    monitor.start_monitoring()
    
    try:
        # Manually trigger failover to next best node
        new_endpoint = monitor.manually_trigger_failover()
        print(f"Manually failed over to: {new_endpoint}")
        
    finally:
        monitor.stop_monitoring()


def example_record_request_results():
    """Example of recording actual request results for health tracking."""
    primary = "https://horizon.stellar.org"
    backups = ["https://horizon-testnet.stellar.org"]
    
    monitor = RPCMonitor(
        primary_endpoint=primary,
        backup_endpoints=backups
    )
    
    monitor.start_monitoring()
    
    try:
        # Simulate recording request results
        # This integrates with your actual RPC calls
        import time
        
        # Record a successful request
        monitor.record_request_result(
            url=primary,
            success=True,
            latency_ms=150.0
        )
        
        # Record a failed request
        monitor.record_request_result(
            url=primary,
            success=False,
            latency_ms=3000.0
        )
        
        # Check updated health
        health = monitor.get_node_health(primary)
        print(f"Updated health score: {health.health_score if health else 'N/A'}")
        
    finally:
        monitor.stop_monitoring()


if __name__ == "__main__":
    print("=== Basic Monitoring Example ===")
    example_basic_monitoring()
    
    print("\n=== Predictive Router Example ===")
    example_predictive_router()
    
    print("\n=== Manual Failover Example ===")
    example_manual_failover()
    
    print("\n=== Record Request Results Example ===")
    example_record_request_results()
