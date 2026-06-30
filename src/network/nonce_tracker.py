import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import aiohttp
import requests

# Default time a nonce may sit "pending" (issued, but neither confirmed nor
# failed) before it is reported as stale. Tunable per call via
# get_stale(address, timeout_seconds=...).
DEFAULT_STALE_TIMEOUT_SECONDS = 30.0


@dataclass
class _PendingNonce:
    """Bookkeeping for a nonce that has been issued but not yet resolved."""

    nonce: int
    issued_at: float = field(default_factory=time.monotonic)


class NonceTracker:
    """Thread-safe per-account nonce tracker with pending-slot recovery.

    This preserves the original strictly-sequential, one-nonce-at-a-time
    contract used by the rest of the transport layer (see tx_manager.py,
    which signs and dispatches under a single per-account lock). What's new
    is visibility into nonces that were handed out but never confirmed or
    failed -- e.g. because the broadcast dropped or the response was lost --
    so callers can detect and recover from those gaps instead of silently
    trusting that every issued nonce eventually landed.

    Each account address owns an independent Lock, so concurrent operations
    across different accounts proceed without contention while a single
    account's nonces remain strictly sequential and duplicate-free.

    Complexity
    ----------
    Time  : O(1) amortised per acquisition, confirmation, failure, or sync.
            O(p) for get_stale, where p is the number of currently pending
            nonces for that account (normally small/bounded by in-flight tx
            count, not a long-term backlog).
    Space : O(n + p) where n is the number of unique account addresses
            tracked and p is the number of currently pending nonces.
    """

    _instance: Optional["NonceTracker"] = None
    _init_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "NonceTracker":
        # Double-checked locking: fast path avoids acquiring _init_lock once
        # the singleton is fully constructed.
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._account_locks: Dict[str, threading.Lock] = {}
                    instance._nonces: Dict[str, int] = {}
                    # address -> {nonce: _PendingNonce}
                    instance._pending: Dict[str, Dict[int, _PendingNonce]] = {}
                    # Protects _account_locks dict during lazy lock creation.
                    instance._map_lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    @classmethod
    def create_standalone(cls) -> "NonceTracker":
        """Build an independent NonceTracker instance, bypassing the singleton.

        NonceTracker() always returns the shared, process-wide singleton --
        that's intentional for production code, where every caller for a
        given account should see the same state. This method exists for
        callers that need an isolated instance instead, e.g. tests that
        construct multiple TxManager objects and expect each to start with
        a clean slate, or any code intentionally tracking a separate,
        unshared set of accounts.
        """

        instance = object.__new__(cls)
        instance._account_locks = {}
        instance._nonces = {}
        instance._pending = {}
        instance._map_lock = threading.Lock()
        return instance

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_lock(self, address: str) -> threading.Lock:
        """Return the per-account lock, creating it lazily on first access.

        Double-checked locking ensures _map_lock is acquired only on the
        initial creation, keeping the common path (lock already exists)
        entirely contention-free.
        """
        lock = self._account_locks.get(address)
        if lock is None:
            with self._map_lock:
                lock = self._account_locks.get(address)
                if lock is None:
                    lock = threading.Lock()
                    self._account_locks[address] = lock
        return lock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_next_nonce(self, address: str, seed: Optional[int] = None) -> int:
        """Return the next unique, monotonically-increasing nonce for *address*.

        On the first call for an account a *seed* (the current on-chain
        sequence number) must be supplied. Subsequent calls increment the
        cached value atomically without further network I/O.

        The returned nonce is recorded as pending until confirm() or fail()
        is called for it, or until it is reported stale via get_stale().

        Args:
            address: Account identifier (e.g. a Stellar public key).
            seed:    Bootstrap nonce when no local cache exists. Required on
                     the first call; ignored once a value is cached.

        Returns:
            An integer nonce guaranteed to be unique and sequential for
            *address* across all concurrent callers.

        Raises:
            ValueError: If no cached nonce exists and no *seed* was supplied.
        """
        lock = self._get_lock(address)
        with lock:
            try:
                cached = self._nonces.get(address)
                if cached is None:
                    if seed is None:
                        raise ValueError(
                            f"No cached nonce for '{address}' and no seed supplied."
                        )
                    self._nonces[address] = seed
                    self._mark_pending(address, seed)
                    logger.info("[NonceTracker] Seeded nonce for %s → %d", address, seed)
                    return seed

                next_nonce = cached + 1
                self._nonces[address] = next_nonce
                self._mark_pending(address, next_nonce)
                return next_nonce
            except Exception:
                # Drop the cache on any error so the next caller is forced to
                # re-sync from the ledger instead of propagating a stale value.
                self._nonces.pop(address, None)
                raise

    def confirm(self, address: str, nonce: int) -> None:
        """Mark *nonce* as confirmed (landed on the ledger) and stop tracking it.

        Call this once the caller learns -- via polling, webhook, or any other
        feedback channel -- that the transaction using this nonce succeeded.

        Time: O(1).
        """
        lock = self._get_lock(address)
        with lock:
            pending = self._pending.get(address)
            if pending is not None:
                pending.pop(nonce, None)
        logger.info("[NonceTracker] Confirmed nonce %d for %s", nonce, address)

    def fail(self, address: str, nonce: int) -> None:
        """Mark *nonce* as failed (rejected or dropped) and stop tracking it.

        This does not by itself roll back the cached counter -- if the chain
        ends up with a gap at this sequence, call sync_nonce() once the
        correct ledger sequence is known. This method only clears the
        pending-slot bookkeeping so the nonce stops being reported as stale.

        Time: O(1).
        """
        lock = self._get_lock(address)
        with lock:
            pending = self._pending.get(address)
            if pending is not None:
                pending.pop(nonce, None)
        logger.info("[NonceTracker] Failed nonce %d for %s", nonce, address)

    def get_stale(
        self, address: str, timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS
    ) -> List[int]:
        """Return pending nonces for *address* older than *timeout_seconds*.

        A nonce counts as stale if it was issued by get_next_nonce() but has
        not since been resolved via confirm() or fail(), and more than
        timeout_seconds have elapsed. Use this to detect transactions that
        likely dropped or whose outcome was never reported back, so they can
        be investigated, retried, or used to trigger a sync_nonce() call.

        Time: O(p), where p is the number of currently pending nonces for
        this account.
        """
        lock = self._get_lock(address)
        with lock:
            pending = self._pending.get(address)
            if not pending:
                return []
            now = time.monotonic()
            stale = [
                nonce
                for nonce, info in pending.items()
                if (now - info.issued_at) > timeout_seconds
            ]
        return sorted(stale)

    def sync_nonce(self, address: str, nonce: int) -> None:
        """Overwrite the cached nonce with a known-good ledger value.
        
        Call this after a tx_bad_seq error to realign the local counter with
        the chain's authoritative sequence number. This also clears all
        pending-slot bookkeeping for the account, since any in-flight nonces
        are now superseded by the authoritative value.

        Time: O(1).
        """
        lock = self._get_lock(address)
        with lock:
            self._nonces[address] = nonce
            self._pending.pop(address, None)
            logger.info("[NonceTracker] Synced nonce for %s → %d", address, nonce)

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
        lock = self._get_lock(address)
        with lock:
            return self._nonces.get(address)

    def invalidate(self, address: Optional[str] = None) -> None:
        """Evict the cached nonce for *address*, or all accounts when omitted.

        The next call to get_next_nonce will require a seed or an external
        sync from the ledger. Also clears any pending-slot bookkeeping for
        the affected account(s).

        Implementation note: for a full clear, a snapshot of existing accounts
        is taken under _map_lock which is then released before acquiring
        individual per-account locks. This prevents a deadlock that would arise
        if _map_lock were held while waiting for per-account locks that other
        threads may already hold.

        Time: O(1) for a single address; O(n) for a full clear.
        """
        if address is not None:
            lock = self._get_lock(address)
            with lock:
                self._nonces.pop(address, None)
                self._pending.pop(address, None)
            logger.info(
                "[NonceTracker] Nonce invalidated for %s. Re-sync required.", address
            )
            return

        # Snapshot account locks without holding _map_lock during the clear.
        with self._map_lock:
            snapshot = list(self._account_locks.items())

        for addr, lock in snapshot:
            with lock:
                self._nonces.pop(addr, None)
                self._pending.pop(addr, None)

        logger.info("[NonceTracker] All cached nonces cleared. Re-sync required.")

    def _mark_pending(self, address: str, nonce: int) -> None:
        """Record *nonce* as freshly issued and unresolved. Caller holds the lock."""
        account_pending = self._pending.setdefault(address, {})
        account_pending[nonce] = _PendingNonce(nonce=nonce)


# Module-level singleton – import and use directly.
nonce_tracker = NonceTracker()
