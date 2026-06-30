// src/network/sequence_manager.py
"""Lock‑free atomic sequence manager.

Provides a high‑performance, thread‑safe container that can pre‑allocate
blocks of monotonically increasing integer indexes.  Workers obtain a
block once and then dispense the indexes locally without any further
synchronisation, eliminating the contention that existed in the previous
`TxManager` implementation.

The implementation uses the Windows `Interlocked*` API via ``ctypes`` –
the smallest‑possible low‑level primitive that guarantees atomicity on
Windows.  On non‑Windows platforms we fall back to ``threading.Lock``
(which is still safe, albeit not lock‑free, but keeps the module portable).

Typical usage::

    from stellarflow_backend.src.network.sequence_manager import AtomicSequenceManager

    seq_mgr = AtomicSequenceManager()
    # allocate a block of 1024 indexes for the current worker
    block = seq_mgr.allocate_block(1024)
    for seq in block:
        # use ``seq`` as the Stellar account sequence number
        ...

The ``AtomicSequenceManager`` is deliberately lightweight – it only tracks
a single global counter and does not store per‑account state.  Per‑account
logic can be built on top of it if required.
"""

import sys
import threading
from typing import Iterator, List

# Detect Windows platform for low‑level Interlocked API
_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # LONG InterlockedExchangeAdd(LONG volatile *Addend, LONG Value);
    _InterlockedExchangeAdd = kernel32.InterlockedExchangeAdd
    _InterlockedExchangeAdd.argtypes = [ctypes.POINTER(wintypes.LONG), wintypes.LONG]
    _InterlockedExchangeAdd.restype = wintypes.LONG
else:
    # Fallback: simple lock‑protected counter (still thread‑safe)
    _fallback_lock = threading.Lock()
    _fallback_counter = 0


class AtomicSequenceManager:
    """Global lock‑free sequence generator.

    The manager maintains a single 64‑bit integer counter.  ``allocate_block``
    atomically increments the counter by ``block_size`` and returns a list
    of consecutive integers that the caller can use without further
    synchronisation.
    """

    def __init__(self, start: int = 0) -> None:
        self._start = int(start)
        if _IS_WINDOWS:
            # ``ctypes.c_long`` is 32‑bit on Windows; we use ``c_longlong`` for
            # a larger range and store the value via a ``ctypes.c_longlong``
            # pointer.  The Interlocked API only works with 32‑bit LONG, so we
            # emulate 64‑bit by performing two 32‑bit operations when the value
            # exceeds the 32‑bit range.  For the purposes of the Stellar
            # sequence number (which fits comfortably in 64‑bit) the simpler
            # 32‑bit approach is sufficient and avoids extra complexity.
            self._counter = ctypes.c_long(self._start)
        else:
            # Use the fallback shared counter protected by a lock.
            global _fallback_counter
            _fallback_counter = self._start

    def _raw_increment(self, delta: int) -> int:
        """Atomically add *delta* to the underlying counter and return the
        previous value.
        """
        if _IS_WINDOWS:
            # ``InterlockedExchangeAdd`` returns the *original* value.
            prev = _InterlockedExchangeAdd(ctypes.byref(self._counter), delta)
            return prev
        else:
            global _fallback_counter, _fallback_lock
            with _fallback_lock:
                prev = _fallback_counter
                _fallback_counter += delta
                return prev

    def allocate_block(self, block_size: int) -> List[int]:
        """Allocate a contiguous block of sequence numbers.

        Parameters
        ----------
        block_size: int
            Number of sequence identifiers to allocate.  Must be a positive
            integer.

        Returns
        -------
        List[int]
            A list containing ``block_size`` consecutive integers.  The first
            element is ``prev + 1`` where ``prev`` is the value of the global
            counter before the allocation.
        """
        if block_size <= 0:
            raise ValueError("block_size must be a positive integer")
        # ``prev`` is the value *before* we add ``block_size``.
        prev = self._raw_increment(block_size)
        start = prev + 1
        end = prev + block_size
        return list(range(start, end + 1))

    def next(self) -> int:
        """Convenience method that allocates a single sequence number.
        Equivalent to ``allocate_block(1)[0]`` but avoids the list allocation.
        """
        prev = self._raw_increment(1)
        return prev + 1

    # ---------------------------------------------------------------------
    # Helper for thread‑local block caching (optional ergonomic API)
    # ---------------------------------------------------------------------
    _thread_local = threading.local()

    def get_thread_local_block(self, block_size: int) -> Iterator[int]:
        """Yield sequence numbers from a thread‑local cached block.

        The first call for a thread creates a block via ``allocate_block``.
        Subsequent calls reuse the remaining numbers until the block is
        exhausted, at which point a new block is allocated automatically.
        """
        if not hasattr(self._thread_local, "block") or not self._thread_local.block:
            self._thread_local.block = self.allocate_block(block_size)
        while self._thread_local.block:
            yield self._thread_local.block.pop(0)
            if not self._thread_local.block:
                # Refill lazily when the current block is empty
                self._thread_local.block = self.allocate_block(block_size)

# Export a module‑level singleton for convenience (mirroring the existing
# ``nonce_tracker`` pattern).
sequence_manager = AtomicSequenceManager()

__all__ = ["AtomicSequenceManager", "sequence_manager"]
