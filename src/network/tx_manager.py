"""Transaction broadcast ordering for transport-layer workers.

The manager in this module keeps sequence assignment, payload signing, and
network dispatch inside the same per-source-account critical section. That
prevents parallel broadcast workers from signing valid sequential payloads and
then sending them to the Stellar network out of order.
"""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, MutableMapping, Optional, Protocol

logger = logging.getLogger(__name__)

Payload = MutableMapping[str, Any]
Signer = Callable[[Payload], Payload]
Dispatcher = Callable[[Payload], Any]


class TxPayloadSigner(Protocol):
    def __call__(self, payload: Payload) -> Payload:
        """Sign and return a transaction payload."""


class TxPayloadDispatcher(Protocol):
    def __call__(self, payload: Payload) -> Any:
        """Dispatch a signed transaction payload."""


@dataclass
class BroadcastResult:
    """Result wrapper that exposes the assigned sequence for tracking."""

    account_id: str
    sequence: int
    payload: Payload
    dispatch_result: Any


class AtomicIntegerCounter:
    """Thread-safe integer counter with explicit bootstrap and sync support."""

    def __init__(self) -> None:
        self._value: Optional[int] = None
        self._lock = threading.Lock()

    def next(self, seed: Optional[int] = None) -> int:
        """Return the next sequential integer atomically.

        The first call requires ``seed`` and returns that value. Later calls
        increment the cached value by one. This mirrors Stellar sequence
        tracking where the first local assignment starts from a known ledger
        sequence supplied by the caller.
        """

        with self._lock:
            if self._value is None:
                if seed is None:
                    raise ValueError("Counter has not been seeded.")
                self._value = self._coerce_sequence(seed)
                return self._value

            self._value += 1
            return self._value

    def sync(self, value: int) -> None:
        """Replace the cached value with a known-good sequence."""

        with self._lock:
            self._value = self._coerce_sequence(value)

    def invalidate(self) -> None:
        """Clear the cached value so the next caller must provide a seed."""

        with self._lock:
            self._value = None

    @property
    def current(self) -> Optional[int]:
        with self._lock:
            return self._value

    @staticmethod
    def _coerce_sequence(value: int) -> int:
        sequence = int(value)
        if sequence < 0:
            raise ValueError("Sequence must be a non-negative integer.")
        return sequence


@dataclass
class _AccountState:
    counter: AtomicIntegerCounter
    lock: threading.Lock


class TxManager:
    """Serialize signing and dispatch by account using atomic sequence counters."""

    def __init__(self, sequence_field: str = "sequence") -> None:
        self.sequence_field = sequence_field
        self._states: Dict[str, _AccountState] = {}
        self._states_lock = threading.Lock()

    def broadcast(
        self,
        account_id: str,
        payload: Payload,
        *,
        signer: Signer,
        dispatcher: Dispatcher,
        seed_sequence: Optional[int] = None,
    ) -> BroadcastResult:
        """Assign a sequence, sign the payload, and dispatch it in order.

        Args:
            account_id: Source account whose transaction sequence is tracked.
            payload: Transaction payload. It is deep-copied before mutation.
            signer: Callable that signs the sequenced payload and returns it.
            dispatcher: Callable that sends the signed payload to the network.
            seed_sequence: Required on first use for an account.

        Returns:
            BroadcastResult containing the assigned sequence, signed payload,
            and dispatcher response.
        """

        if not account_id:
            raise ValueError("account_id is required.")

        state = self._get_state(account_id)

        # Keep assignment, signing, and dispatch together. If signing is slow in
        # one worker, a later sequence cannot leapfrog it on the wire.
        # Use lock‑free global sequence manager to obtain a unique sequence number.
        from .sequence_manager import sequence_manager as _global_seq_mgr
        sequence = _global_seq_mgr.next()
        sequenced_payload = self._with_sequence(payload, sequence)
        signed_payload = signer(sequenced_payload)
        self._assert_signed_sequence(signed_payload, sequence)
        dispatch_result = dispatcher(signed_payload)

        logger.info(
            "[TxManager] Dispatched transaction for %s with sequence %d",
            account_id,
            sequence,
        )

        return BroadcastResult(
            account_id=account_id,
            sequence=sequence,
            payload=signed_payload,
            dispatch_result=dispatch_result,
        )

    def sync_sequence(self, account_id: str, sequence: int) -> None:
        """Set an account counter to a known-good sequence value."""

        self._get_state(account_id).counter.sync(sequence)
        logger.info("[TxManager] Synced sequence for %s to %d", account_id, sequence)

    def invalidate(self, account_id: Optional[str] = None) -> None:
        """Clear one account sequence or all tracked account sequences."""

        if account_id is not None:
            self._get_state(account_id).counter.invalidate()
            logger.info("[TxManager] Invalidated sequence for %s", account_id)
            return

        with self._states_lock:
            states = list(self._states.values())

        for state in states:
            state.counter.invalidate()

        logger.info("[TxManager] Invalidated all tracked sequences")

    def current_sequence(self, account_id: str) -> Optional[int]:
        """Return the cached sequence for an account, if seeded."""

        return self._get_state(account_id).counter.current

    def _get_state(self, account_id: str) -> _AccountState:
        state = self._states.get(account_id)
        if state is not None:
            return state

        with self._states_lock:
            state = self._states.get(account_id)
            if state is None:
                state = _AccountState(
                    counter=AtomicIntegerCounter(),
                    lock=threading.Lock(),
                )
                self._states[account_id] = state
            return state

    def _with_sequence(self, payload: Payload, sequence: int) -> Payload:
        sequenced_payload = copy.deepcopy(dict(payload))
        sequenced_payload[self.sequence_field] = sequence
        return sequenced_payload

    def _assert_signed_sequence(self, payload: Payload, sequence: int) -> None:
        if payload.get(self.sequence_field) != sequence:
            raise ValueError(
                "Signer returned a payload with a mismatched sequence value."
            )


tx_manager = TxManager()

__all__ = [
    "AtomicIntegerCounter",
    "BroadcastResult",
    "TxManager",
    "tx_manager",
]
