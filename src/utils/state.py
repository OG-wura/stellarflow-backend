'''state.py
"""Utility module providing a process‑safe state register for internal worker flags.

The register maintains a mapping from arbitrary string identifiers (e.g. ``asset_pair``
or ``worker_name``) to boolean flags that indicate whether a particular worker is
currently active.  All operations are protected by a :class:`multiprocessing.Lock`
ensuring safe concurrent access from multiple ingestion processes.

Typical usage::

    from src.utils.state import StateRegister

    # Obtain a singleton instance (module‑level) or instantiate directly
    state = StateRegister()

    if not state.is_active('BTC/USD'):
        state.activate('BTC/USD')
        start_worker('BTC/USD')

    # Later, when the worker finishes
    state.deactivate('BTC/USD')

The implementation is deliberately lightweight and does not depend on any external
libraries so it can be used from both Python and TypeScript runtimes (via
inter‑process communication) without side effects.
"""

import multiprocessing
from typing import Dict


class StateRegister:
    """Process‑safe registry for boolean activity flags.

    Attributes
    ----------
    _flags: Dict[str, bool]
        Internal mapping from a key to its active/inactive state.
    _lock: multiprocessing.Lock
        Inter-process mutex guarding all modifications and reads of ``_flags``.
    """

    def __init__(self) -> None:
        self._flags: Dict[str, bool] = {}
        self._lock = multiprocessing.Lock()

    def is_active(self, key: str) -> bool:
        """Return ``True`` if the flag for *key* is set, ``False`` otherwise.

        This method acquires the internal lock to guarantee a consistent view.
        """
        with self._lock:
            return self._flags.get(key, False)

    def activate(self, key: str) -> None:
        """Mark the flag for *key* as active (``True``).

        If the key does not yet exist, it is created.
        """
        with self._lock:
            self._flags[key] = True

    def try_acquire(self, key: str) -> bool:
        """Atomically check if *key* is inactive and, if so, activate it.

        Returns ``True`` when the caller successfully acquired the flag (i.e. no other
        worker was running for the same ``key``). Returns ``False`` if the flag was
        already ``True``.
        """
        with self._lock:
            if self._flags.get(key, False):
                return False
            self._flags[key] = True
            return True
    def deactivate(self, key: str) -> None:
        """Mark the flag for *key* as inactive (``False``).

        The key is retained in the mapping to allow future ``is_active`` checks
        without raising ``KeyError``.
        """
        with self._lock:
            self._flags[key] = False

    # Alias for clarity when releasing a worker lock
    def release(self, key: str) -> None:
        """Convenient wrapper that forwards to :meth:`deactivate`.

        This can be used by ingestion code to explicitly free the allocation flag.
        """
        self.deactivate(key)
    def clear(self, key: str) -> None:
        """Remove *key* from the registry entirely.

        After removal, ``is_active`` will return ``False`` for the key.
        """
        with self._lock:
            self._flags.pop(key, None)

    def snapshot(self) -> Dict[str, bool]:
        """Return a shallow copy of the current flags mapping.

        The copy is taken under lock to avoid race conditions; callers can safely
        iterate over the result without further synchronization.
        """
        with self._lock:
            return dict(self._flags)

    # Optional convenience context manager for safe activation/deactivation
    def guard(self, key: str):
        """Context manager that activates *key* on entry and deactivates on exit.

        Example::

            with state.guard('worker-1'):
                run_expensive_task()
        """
        return _StateGuard(self, key)


class _StateGuard:
    def __init__(self, register: StateRegister, key: str) -> None:
        self._register = register
        self._key = key

    def __enter__(self):
        self._register.activate(self._key)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._register.deactivate(self._key)
        # Propagate any exception
        return False

# Create a module‑level singleton for convenient import
state_register = StateRegister()
'''
