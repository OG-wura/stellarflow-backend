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

import os
import json
import tempfile
import multiprocessing
from typing import Dict, Optional

try:
    import fcntl
except ImportError:
    fcntl = None


class StateRegister:
    """Process‑safe registry for boolean activity flags.

    Attributes:
        _filepath: Filepath to the local operational metadata file layout.
        _lock: Inter-process mutex guarding all modifications and reads of the state file.
    """

    def __init__(self, filepath: str = "state_register.json") -> None:
        self._filepath = filepath
        self._lock_filepath = filepath + ".lock"
        self._lock = multiprocessing.Lock()
        with self._lock:
            dir_name = os.path.dirname(self._filepath) or "."
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
            self._execute_with_file_lock(self._init_file)

    def _init_file(self) -> None:
        if not os.path.exists(self._filepath):
            self._write_state_unlocked({})

    def _execute_with_file_lock(self, func, *args, **kwargs):
        """Execute a function while holding an advisory file lock on Linux."""
        if fcntl is None:
            return func(*args, **kwargs)
        with open(self._lock_filepath, "w") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                return func(*args, **kwargs)
            finally:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                except Exception:
                    pass

    def _load_state(self) -> Dict[str, bool]:
        """Load state map from the file.

        Complexity:
        Time: O(S) where S is the size of the JSON file (de-serialization).
        Space: O(S) memory footprint to hold the parsed state map.
        """
        return self._execute_with_file_lock(self._load_state_unlocked)

    def _load_state_unlocked(self) -> Dict[str, bool]:
        if not os.path.exists(self._filepath):
            return {}
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except Exception:
            return {}

    def _write_state(self, flags: Dict[str, bool]) -> None:
        """Atomically persist state map to the file using a temporary file.

        Complexity:
        Time: O(S) where S is the size of the JSON file (serialization).
        Space: O(S) for temporary buffers.
        """
        self._execute_with_file_lock(self._write_state_unlocked, flags)

    def _write_state_unlocked(self, flags: Dict[str, bool]) -> None:
        dir_name = os.path.dirname(self._filepath) or "."
        fd, temp_path = tempfile.mkstemp(
            dir=dir_name,
            prefix=f".{os.path.basename(self._filepath)}.",
            suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(flags, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self._filepath)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            raise

    def is_active(self, key: str) -> bool:
        """Return ``True`` if the flag for *key* is set, ``False`` otherwise.

        This method acquires the internal lock to guarantee a consistent view.
        """
        with self._lock:
            flags = self._load_state()
            return flags.get(key, False)

    def activate(self, key: str) -> None:
        """Mark the flag for *key* as active (``True``).

        If the key does not yet exist, it is created.
        """
        with self._lock:
            flags = self._load_state()
            flags[key] = True
            self._write_state(flags)

    def try_acquire(self, key: str) -> bool:
        """Atomically check if *key* is inactive and, if so, activate it.

        Returns ``True`` when the caller successfully acquired the flag (i.e. no other
        worker was running for the same ``key``). Returns ``False`` if the flag was
        already ``True``.
        """
        with self._lock:
            flags = self._load_state()
            if flags.get(key, False):
                return False
            flags[key] = True
            self._write_state(flags)
            return True

    def deactivate(self, key: str) -> None:
        """Mark the flag for *key* as inactive (``False``).

        The key is retained in the mapping to allow future ``is_active`` checks
        without raising ``KeyError``.
        """
        with self._lock:
            flags = self._load_state()
            flags[key] = False
            self._write_state(flags)

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
            flags = self._load_state()
            flags.pop(key, None)
            self._write_state(flags)

    def snapshot(self) -> Dict[str, bool]:
        """Return a shallow copy of the current flags mapping.

        The copy is taken under lock to avoid race conditions; callers can safely
        iterate over the result without further synchronization.
        """
        with self._lock:
            return self._load_state()

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
