import ctypes
import ctypes.util
import logging
import platform
import secrets
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["VaultManager", "VaultContext", "vault"]

# ---------------------------------------------------------------------------
# Module-private sentinel — prevents external construction of VaultContext.
# It is not listed in __all__ and cannot be imported via `from … import *`.
# ---------------------------------------------------------------------------
_CONTEXT_SENTINEL = object()


def _load_mlock() -> tuple:
    """Load mlock/munlock (POSIX) or VirtualLock/VirtualUnlock (Windows).

    Returns ``(mlock_fn, munlock_fn)`` or ``(None, None)`` if unavailable.
    Resolved once at module import; subsequent calls reuse the module-level
    ``_MLOCK_FN`` / ``_MUNLOCK_FN`` singletons.
    """
    _os = platform.system()
    if _os == "Windows":
        try:
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            ml = k32.VirtualLock
            mu = k32.VirtualUnlock
            ml.argtypes = mu.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            ml.restype = mu.restype = ctypes.c_bool
            return ml, mu
        except Exception:  # noqa: BLE001
            return None, None

    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        return None, None
    try:
        libc = ctypes.CDLL(libc_name, use_errno=True)
        ml = getattr(libc, "mlock", None)
        mu = getattr(libc, "munlock", None)
        if ml is None or mu is None:
            return None, None
        ml.argtypes = mu.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        ml.restype = mu.restype = ctypes.c_int
        return ml, mu
    except Exception:  # noqa: BLE001
        return None, None


_MLOCK_FN, _MUNLOCK_FN = _load_mlock()
_MLOCK_WARNED: bool = False


def _mlock(buf: bytearray) -> bool:
    """Pin *buf*'s pages to physical RAM.  Returns True on success.

    Must not raise; logs a one-time WARNING when mlock is unavailable.
    """
    global _MLOCK_WARNED  # noqa: PLW0603
    if not buf or _MLOCK_FN is None:
        if not _MLOCK_WARNED:
            logger.warning(
                "[VaultManager] mlock unavailable — vault key pages may be swapped to disk. "
                "Grant CAP_IPC_LOCK or raise RLIMIT_MEMLOCK to harden this deployment."
            )
            _MLOCK_WARNED = True
        return False
    try:
        c_arr = (ctypes.c_char * len(buf)).from_buffer(buf)
        addr = ctypes.addressof(c_arr)
        ret = _MLOCK_FN(addr, ctypes.c_size_t(len(buf)))
        success = bool(ret) if platform.system() == "Windows" else (ret == 0)
        if not success and not _MLOCK_WARNED:
            logger.warning(
                "[VaultManager] mlock syscall failed (errno=%d) — vault key pages may be swapped.",
                ctypes.get_errno(),
            )
            _MLOCK_WARNED = True
        return success
    except Exception:  # noqa: BLE001
        return False


def _munlock(buf: bytearray) -> None:
    """Release the mlock on *buf*'s pages.  Must not raise."""
    if not buf or _MUNLOCK_FN is None:
        return
    try:
        c_arr = (ctypes.c_char * len(buf)).from_buffer(buf)
        _MUNLOCK_FN(ctypes.addressof(c_arr), ctypes.c_size_t(len(buf)))
    except Exception:  # noqa: BLE001
        pass


def _zero_wipe(buf: bytearray) -> None:
    """Overwrite *buf* in-place with zeros to minimise the key's memory footprint.

    Uses ``ctypes.memset`` to write through the underlying C buffer, resisting
    CPython optimisations that could theoretically elide a pure-Python loop.
    A redundant Python-level pass follows as a belt-and-suspenders measure.
    """
    if len(buf) == 0:
        return
    try:
        addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
        ctypes.memset(addr, 0, len(buf))
    finally:
        for i in range(len(buf)):
            buf[i] = 0


class VaultContext:
    """Scoped access token issued exclusively by VaultManager.open_context().

    The _CONTEXT_SENTINEL guard makes it structurally impossible to construct
    a valid VaultContext from outside this module: external code can import
    the class but cannot supply the sentinel, so any forgery attempt raises
    TypeError before any internal state is written.
    """

    __slots__ = ("_VaultContext__scope", "_VaultContext__token")

    def __init__(self, sentinel: object, scope: str, token: bytes) -> None:
        if sentinel is not _CONTEXT_SENTINEL:
            raise TypeError(
                "VaultContext must be created via VaultManager.open_context()."
            )
        self.__scope = scope   # name-mangled → _VaultContext__scope
        self.__token = token   # name-mangled → _VaultContext__token

    @property
    def scope(self) -> str:
        return self.__scope

    def _get_token(self) -> bytes:
        """Module-internal accessor used by VaultManager during validation.

        Single-underscore signals intra-module use only; no public API
        should call this directly.
        """
        return self.__token


class VaultManager:
    """Isolated in-memory store for decryption keys with strict access barriers.

    Keys are stored as mutable bytearrays so they can be zero-wiped on
    revocation without relying on the garbage collector.  Retrieval requires
    a VaultContext that was issued by this exact vault instance — there is no
    ambient authority pathway.

    Concurrency
    -----------
    A single threading.Lock serialises all mutations and reads of the internal
    key and context maps.  This keeps the critical section minimal while
    guaranteeing consistent state across threads.

    Complexity
    ----------
    Time  : O(1) — register, open_context, retrieve, close_context, revoke.
            O(n) — purge, where n is the number of registered keys.
    Space : O(n) — one bytearray per registered key; one token per open context.
    """

    _instance: Optional["VaultManager"] = None
    _init_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "VaultManager":
        # Double-checked locking: the fast path (instance already exists) never
        # acquires _init_lock, keeping singleton access essentially free.
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    # name-mangled to _VaultManager__keys / __contexts / __lock / __locked
                    instance.__keys: Dict[str, bytearray] = {}
                    instance.__contexts: Dict[bytes, str] = {}  # token → scope
                    instance.__locked: set = set()  # key_ids whose pages are pinned
                    instance.__lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, key_id: str, raw_key: bytes) -> None:
        """Store a decryption key under *key_id*.

        The key is copied into an internal bytearray so the caller's buffer
        is not aliased and future zero-wipes are isolated to the vault.

        Args:
            key_id:  Logical identifier (e.g. ``"aes-256-gcm"``, ``"signing"``).
            raw_key: Raw key bytes — must be non-empty.

        Raises:
            ValueError: If *key_id* or *raw_key* is empty, or if *key_id* is
                        already registered (call revoke() first).
        """
        if not key_id:
            raise ValueError("key_id must be a non-empty string.")
        if not raw_key:
            raise ValueError("raw_key must be non-empty bytes.")

        key_buf = bytearray(raw_key)
        # Pin the buffer's pages to physical RAM before storing so key material
        # never reaches an unencrypted swap partition or hibernate file.
        locked = _mlock(key_buf)

        with self.__lock:
            if key_id in self.__keys:
                # Roll back the mlock before raising to avoid a resource leak.
                if locked:
                    _munlock(key_buf)
                raise ValueError(
                    f"Key '{key_id}' is already registered. Call revoke() first."
                )
            self.__keys[key_id] = key_buf
            if locked:
                self.__locked.add(key_id)

        logger.info("[VaultManager] Key registered: %s (page-locked=%s)", key_id, locked)

    def open_context(self, scope: str) -> VaultContext:
        """Issue a cryptographically-random scoped access token.

        The returned VaultContext must be presented to retrieve() to access
        any stored key.  Contexts can be invalidated via close_context().

        Args:
            scope: A label for the access boundary (e.g. ``"decryption"``).

        Returns:
            A VaultContext bound to this vault instance.

        Raises:
            ValueError: If *scope* is empty.
        """
        if not scope:
            raise ValueError("scope must be a non-empty string.")

        token: bytes = secrets.token_bytes(32)
        with self.__lock:
            self.__contexts[token] = scope

        logger.info("[VaultManager] Context opened for scope: %s", scope)
        return VaultContext(_CONTEXT_SENTINEL, scope, token)

    def retrieve(self, key_id: str, context: VaultContext) -> bytes:
        """Return an immutable copy of the key bytes after validating *context*.

        Returning a ``bytes`` copy (not the live bytearray) ensures that no
        caller can mutate the vault's internal buffer.

        Args:
            key_id:  The registered key identifier.
            context: A VaultContext previously issued by this vault instance.

        Returns:
            A ``bytes`` copy of the raw key — never a reference to internal state.

        Raises:
            TypeError:       If *context* is not a VaultContext.
            PermissionError: If the context token is unrecognised or its scope
                             does not match the stored scope.
            KeyError:        If *key_id* has no registered entry.

        Time: O(1).
        """
        if not isinstance(context, VaultContext):
            raise TypeError("context must be a VaultContext issued by VaultManager.")

        token = context._get_token()

        with self.__lock:
            stored_scope = self.__contexts.get(token)
            if stored_scope is None:
                raise PermissionError("Invalid or unrecognised vault context.")
            if stored_scope != context.scope:
                raise PermissionError("Vault context scope mismatch.")

            key_buf = self.__keys.get(key_id)
            if key_buf is None:
                raise KeyError(f"No key registered for id '{key_id}'.")

            return bytes(key_buf)

    def close_context(self, context: VaultContext) -> None:
        """Invalidate a context token so it can no longer authorise retrieval.

        Time: O(1).
        """
        if not isinstance(context, VaultContext):
            raise TypeError("context must be a VaultContext instance.")

        token = context._get_token()
        with self.__lock:
            self.__contexts.pop(token, None)

        logger.info("[VaultManager] Context closed for scope: %s", context.scope)

    def revoke(self, key_id: str) -> None:
        """Zero-wipe and remove *key_id* from the vault.

        The bytearray is overwritten before deletion, reducing the window
        during which the key bytes are recoverable from process memory.
        Page unlock follows the wipe so the OS cannot evict dirty key material
        to swap between the two operations.

        Time: O(k) where k is the key length in bytes.
        """
        with self.__lock:
            key_buf = self.__keys.pop(key_id, None)
            was_locked = key_id in self.__locked
            self.__locked.discard(key_id)

        if key_buf is not None:
            # Wipe while pages are still locked, then unlock.
            _zero_wipe(key_buf)
            if was_locked:
                _munlock(key_buf)
            logger.info("[VaultManager] Key revoked: %s", key_id)
        else:
            logger.warning("[VaultManager] revoke() called for unknown key: %s", key_id)

    def purge(self) -> None:
        """Zero-wipe all keys and invalidate all open contexts.

        Acquires the lock once to drain both maps, then releases it before
        performing the (potentially slow) wipe-and-unlock loop so other
        threads are not blocked during memory-clearing.

        Ordering: wipe each buffer while its pages are still locked, then
        unlock — same guarantee as revoke() but applied to all keys at once.

        Time: O(n * k) where n = number of keys, k = average key length.
        """
        with self.__lock:
            key_items: List[tuple] = [
                (key_id, buf, key_id in self.__locked)
                for key_id, buf in self.__keys.items()
            ]
            self.__keys.clear()
            self.__contexts.clear()
            self.__locked.clear()

        for _key_id, buf, was_locked in key_items:
            _zero_wipe(buf)
            if was_locked:
                _munlock(buf)

        logger.info("[VaultManager] All keys purged and all contexts invalidated.")


# Module-level singleton — import and use directly.
vault = VaultManager()
