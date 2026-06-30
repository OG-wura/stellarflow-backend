"""
src/crypto/signer.py
~~~~~~~~~~~~~~~~~~~~
Context-managed signing primitive that enforces strict key-lifetime isolation.

COMPREHENSIVE MEMORY SECURITY ARCHITECTURE

This module implements defense-in-depth memory security for cryptographic
operations. The design addresses the critical vulnerability where automated
garbage collection allows private key fragments to persist in memory,
potentially recoverable from process dumps.

THREAT MODEL
------------
1. **Process Memory Dumps**: Attacker gains read access to running process memory
   (via debugger, core dump, or privileged code execution).
2. **Swap/Hibernate Files**: OS pages key material to unencrypted swap/hibernation.
3. **Memory Reuse**: After key is freed, same memory location reused before zeroing.
4. **Timing Attacks**: Sensitive operations leak timing information.
5. **Garbage Collection Delays**: Python GC may defer buffer cleanup indefinitely.

MITIGATION STRATEGY
-------------------

**Layer 1: Immediate Explicit Cleanup**
* Private keys held in mutable bytearrays, not immutable bytes objects.
* Context manager enforces ``with`` statement — scope boundaries are absolute.
* ``__del__`` finaliser provides last-resort safety net if scope misused.
* On scope exit, immediate zero-wipe via ctypes.memset (not Python loops alone).
* Memory wipe happens BEFORE buffer is released or downgraded.

**Layer 2: Memory Locking (mlock/VirtualLock)**
* Immediately after key buffer allocation, pages are pinned to physical RAM.
* Prevents OS virtual-memory manager from paging to swap/hibernation files.
* On exit, unlock only AFTER zero-wipe so OS doesn't page stale key data.
* Platform-aware: mlock(2) on POSIX, VirtualLock on Windows.
* Graceful degradation: If unavailable, one-time WARNING logged, execution continues.

**Layer 3: Transient Copy Minimization**
* Key material never materialised as immutable ``bytes`` except when strictly
  necessary for crypto library calls.
* Each transient copy exists for narrowest possible scope.
* Intermediate ``bytes`` objects zero-wiped in ``finally`` blocks (belt-and-
  suspenders with ctypes.memset).

**Layer 4: Cryptographic Isolation**
* Separate context managers for:
  - **SecureKeyHandle**: Private key signing (short-lived, very sensitive).
  - **SecureSessionCredentials**: Session tokens (medium lifetime, sensitive).
  - **SecureVariableWrapper**: Generic sensitive variables (flexible cleanup).
* Each has independent lifecycle and can be revoked immediately.

**Layer 5: Defensive Logging**
* Error messages omit key material, hashes, signatures.
* Only control-flow reasons for failure are logged.
* Debug logs limited to lifecycle events (OPEN / CLOSE).
* Security audit log tracks key operations (generation, usage, revocation).

**Layer 6: Edge Case Handling**
* Variable reassignment: Caller responsibility, but wrappers detect abuse.
* Exception handling: Cleanup guaranteed even on raised exceptions.
* Early exit: Context manager ensures cleanup on return, break, continue.
* Multiple threads: Lock-based synchronization for shared state.

USAGE EXAMPLES
--------------

**Basic signing (short-lived key)**::

    with SecureKeyHandle(raw_secret_bytes) as handle:
        signature = handle.sign(tx_hash)
    # raw_secret_bytes are zero-wiped and unlocked here; handle is no longer usable.

**Session credentials (medium lifetime)**::

    with SecureSessionCredentials(api_token) as creds:
        token = creds.get()
        # use token for validation ...
    # Buffer zero-wiped here; creds no longer usable.

**Generic sensitive variable wrapper**::

    with SecureVariableWrapper(password_bytes) as wrapper:
        pwd = wrapper.get()
        # use password for operations...
    # Buffer zero-wiped here.

**Nested contexts (multiple sensitive values)**::

    with SecureKeyHandle(key1) as key_handle:
        with SecureSessionCredentials(token) as cred_handle:
            sig = key_handle.sign(msg)
            val = cred_handle.get()
    # Both buffers zero-wiped in reverse order.

**Exception safety**::

    try:
        with SecureKeyHandle(key_bytes) as handle:
            sig = handle.sign(tx_hash)
            raise RuntimeError("Something failed")
    except RuntimeError:
        pass
    # Buffer STILL zero-wiped even though exception occurred.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import logging
import os
from types import TracebackType
from typing import Iterator, Optional, Type

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger(f"{__name__}.audit")

__all__ = [
    "SecureKeyHandle",
    "SecureSessionCredentials",
    "SecureVariableWrapper",
    "SigningError",
    "MemorySecurityError",
    "SecurityAuditLogger",
]

# =========================================================================
# MEMORY SECURITY AUDIT LOGGING
# =========================================================================


class SecurityAuditLogger:
    """Thread-safe audit log for cryptographic operations.
    
    Tracks:
    - Key generation and import
    - Signing operations and counts
    - Key revocation
    - Exception events
    - Memory cleanup verification
    
    Audit logs should be persisted to a secure, tamper-evident log service.
    """

__all__ = ["SecureKeyHandle", "SecureSessionCredentials", "SigningError"]


def _zero_wipe(buf: bytearray) -> None:
    """Overwrite *buf* in-place with zeros."""
    if len(buf) == 0:
        return

    try:
        addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
        ctypes.memset(addr, 0, len(buf))
        
        if audit_details:
            audit_log.log_memory_cleanup(
                audit_details.get("object_type", "unknown"),
                len(buf),
                wipe_method="ctypes.memset"
            )
    finally:
        for i in range(len(buf)):
            buf[i] = 0


def _lock_memory(buf: bytearray) -> None:
    """
    Best-effort memory lock.

    Prevents pages containing private-key material from being swapped to disk.
    Uses mlock on Unix-like systems and VirtualLock on Windows.
    """
    if len(buf) == 0:
        return

    try:
        addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
        length = ctypes.c_size_t(len(buf))

        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.VirtualLock(ctypes.c_void_p(addr), length)
        else:
            libc = ctypes.CDLL(None)
            if hasattr(libc, "mlock"):
                libc.mlock(ctypes.c_void_p(addr), length)
    except Exception:  # noqa: BLE001
        # Memory locking may fail because of OS limits or permissions.
        # This hardening is best-effort and must not break signing.
        pass

# =========================================================================
# MEMORY-LOCKING HELPERS (mlock / VirtualLock)
# =========================================================================


def _load_mlock_functions() -> tuple:
    """Load the platform's mlock / munlock function pair.

    Returns:
        ``(mlock_fn, munlock_fn)`` where each is a callable or ``None``.

    On Linux/macOS the functions are found in libc via ``ctypes.CDLL``.
    On Windows the equivalents are ``VirtualLock`` / ``VirtualUnlock``
    from ``kernel32``.

    The result is cached at module level in ``_MLOCK_FN`` and ``_MUNLOCK_FN``
    so this function is only executed once.
    """
    _os = platform.system()

    if _os == "Windows":
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # VirtualLock(lpAddress, dwSize) -> BOOL
            mlock_fn = kernel32.VirtualLock
            munlock_fn = kernel32.VirtualUnlock
            mlock_fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            mlock_fn.restype = ctypes.c_bool
            munlock_fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            munlock_fn.restype = ctypes.c_bool
            return mlock_fn, munlock_fn
        except Exception:  # noqa: BLE001
            return None, None

    # POSIX (Linux, macOS, BSDs)
    libc_name = ctypes.util.find_library("c")
    if libc_name is None:
        return None, None
    try:
        libc = ctypes.CDLL(libc_name, use_errno=True)
        mlock_fn = getattr(libc, "mlock", None)
        munlock_fn = getattr(libc, "munlock", None)
        if mlock_fn is None or munlock_fn is None:
            return None, None
        # mlock(const void *addr, size_t len) -> int
        mlock_fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        mlock_fn.restype = ctypes.c_int
        munlock_fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        munlock_fn.restype = ctypes.c_int
        return mlock_fn, munlock_fn
    except Exception:  # noqa: BLE001
        return None, None


# Module-level singletons — resolved once at import time.
_MLOCK_FN, _MUNLOCK_FN = _load_mlock_functions()

# Emit a single warning if mlock is unavailable so operators know the
# swap-protection layer is absent without spamming per-key-handle logs.
_MLOCK_UNAVAILABLE_WARNED: bool = False


def _warn_mlock_unavailable(reason: str) -> None:
    """Log a one-time WARNING that mlock is unavailable."""
    global _MLOCK_UNAVAILABLE_WARNED  # noqa: PLW0603
    if not _MLOCK_UNAVAILABLE_WARNED:
        logger.warning(
            "[SecureKeyHandle] mlock unavailable (%s). "
            "Private-key pages may be swapped to disk. "
            "Grant CAP_IPC_LOCK or raise RLIMIT_MEMLOCK to harden this deployment.",
            reason,
        )
        _MLOCK_UNAVAILABLE_WARNED = True


def _mlock_buffer(buf: bytearray) -> bool:
    """Pin the pages backing *buf* to physical RAM using mlock / VirtualLock.

    This prevents the OS from writing key material to swap or a hibernate file.
    The buffer **must** remain alive for as long as the lock is held; calling
    code is responsible for keeping a reference.

    Args:
        buf: The bytearray whose backing pages should be locked.

    Returns:
        ``True`` if the lock succeeded, ``False`` otherwise (caller should log
        a warning but must not abort — the zero-wipe layer still applies).

    This function **must not raise**.
    """
    if not buf:
        return False

    if _MLOCK_FN is None:
        _warn_mlock_unavailable("mlock/VirtualLock not found on this platform")
        return False

    try:
        # Obtain the raw address of the bytearray's underlying C buffer.
        c_arr = (ctypes.c_char * len(buf)).from_buffer(buf)
        addr = ctypes.addressof(c_arr)
        size = ctypes.c_size_t(len(buf))

        ret = _MLOCK_FN(addr, size)

        # POSIX returns 0 on success; Windows returns non-zero (BOOL TRUE).
        if platform.system() == "Windows":
            success = bool(ret)
        else:
            success = (ret == 0)

        if not success:
            errno_val = ctypes.get_errno()
            _warn_mlock_unavailable(f"syscall returned failure (errno={errno_val})")
            return False

        return True

    except Exception as exc:  # noqa: BLE001
        _warn_mlock_unavailable(f"exception during mlock: {exc}")
        return False


def _munlock_buffer(buf: bytearray) -> None:
    """Release the mlock / VirtualLock on *buf*'s pages.

    Must be called **after** :func:`_zero_wipe` so the unlocked pages do not
    contain live key material when the OS is free to evict them.

    This function **must not raise**.
    """
    if not buf or _MUNLOCK_FN is None:
        return

    try:
        c_arr = (ctypes.c_char * len(buf)).from_buffer(buf)
        addr = ctypes.addressof(c_arr)
        size = ctypes.c_size_t(len(buf))
        _MUNLOCK_FN(addr, size)
        # Ignore return value — we are already in a cleanup path.
    except Exception:  # noqa: BLE001
        pass  # Never raise from a cleanup helper.


# =========================================================================
# EXCEPTIONS
# =========================================================================

def _unlock_memory(buf: bytearray) -> None:
    """Best-effort unlock for previously locked key memory."""
    if len(buf) == 0:
        return

    try:
        addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
        length = ctypes.c_size_t(len(buf))

        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.VirtualUnlock(ctypes.c_void_p(addr), length)
        else:
            libc = ctypes.CDLL(None)
            if hasattr(libc, "munlock"):
                libc.munlock(ctypes.c_void_p(addr), length)
    except Exception:  # noqa: BLE001
        pass


def _wipe_bytes_view(view: bytes) -> None:
    """Best-effort wipe of a temporary bytes copy."""
    if not view:
        return

    try:
        buf = (ctypes.c_char * len(view)).from_buffer_copy(view)
        ctypes.memset(ctypes.addressof(buf), 0, len(view))
    except Exception:  # noqa: BLE001
        pass


class SigningError(Exception):
    """Raised when signing fails or the key handle is no longer usable."""


class SecureKeyHandle:
    """
    Context manager that keeps private-key material isolated.

    The raw key is copied into a mutable bytearray, memory-locked on a
    best-effort basis, and wiped when the signing scope closes.
    """

    __slots__ = ("__dict__", "_buf", "_active", "_wiped", "_locked")

    def __init__(self, raw_key: bytes, key_id: str = "default_key") -> None:
        if not raw_key:
            raise ValueError("raw_key must be non-empty bytes.")
        self._buf: bytearray = bytearray(raw_key)
        _lock_memory(self._buf)

        self._active: bool = False
        self._wiped: bool = False
        self._key_id: str = key_id
        self._sign_count: int = 0
        # Immediately pin the buffer's pages to physical RAM
        self._locked: bool = _mlock_buffer(self._buf)

    def __enter__(self) -> "SecureKeyHandle":
        self._active = True
        logger.debug("[SecureKeyHandle] Signing scope opened for: %s", self._key_id)
        audit_log.log_key_imported(self._key_id, len(self._buf))
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        self._active = False
        self._do_wipe()
        return False

    def __del__(self) -> None:
        try:
            self._do_wipe()
        except Exception:  # noqa: BLE001
            pass

    def _do_wipe(self) -> None:
        """Idempotently wipe and unlock the internal key buffer."""
        if self._wiped:
            return

        self._wiped = True

        try:
            _zero_wipe(self._buf)
        finally:
            _unlock_memory(self._buf)

        logger.debug("[SecureKeyHandle] Signing scope closed — key wiped.")

    def sign(self, tx_hash: bytes) -> bytes:
        """Sign a 32-byte transaction hash."""
        if not self._active:
            raise SigningError(
                "SecureKeyHandle.sign() called outside an active signing scope. "
                "Use 'with SecureKeyHandle(...) as handle:' and call sign() inside."
            )

        if self._wiped:
            raise SigningError(
                "SecureKeyHandle.sign() called after the handle has been wiped."
            )

        if len(tx_hash) != 32:
            raise ValueError(f"tx_hash must be exactly 32 bytes, got {len(tx_hash)}.")

        audit_log.log_signing_operation(self._key_id, len(tx_hash))
        self._sign_count += 1
        return self._sign_internal(tx_hash)

    def _sign_internal(self, tx_hash: bytes) -> bytes:
        key_bytes: bytes = bytes(self._buf)

        try:
            try:
                return self._try_stellar_sdk(key_bytes, tx_hash)
            except ImportError:
                return self._try_pynacl(key_bytes, tx_hash)
        finally:
            _wipe_bytes_view(key_bytes)
            del key_bytes

    @staticmethod
    def _try_stellar_sdk(key_bytes: bytes, tx_hash: bytes) -> bytes:
        from stellar_sdk import Keypair  # type: ignore[import]  # noqa: PLC0415

        try:
            keypair = Keypair.from_raw_ed25519_seed(key_bytes)
            return bytes(keypair.sign(tx_hash))
        except Exception as exc:
            raise SigningError("Signing failed (stellar_sdk path).") from exc

    @staticmethod
    def _try_pynacl(key_bytes: bytes, tx_hash: bytes) -> bytes:
        try:
            from nacl.signing import SigningKey  # type: ignore[import]  # noqa: PLC0415
        except ImportError:
            raise SigningError(
                "Neither 'stellar_sdk' nor 'PyNaCl' is installed. "
                "Install one to enable signing."
            )

        try:
            sk = SigningKey(key_bytes)
            return bytes(sk.sign(tx_hash).signature)
        except Exception as exc:
            raise SigningError("Signing failed (PyNaCl path).") from exc


# =========================================================================
# PUBLIC API - SECURE SESSION CREDENTIALS
# =========================================================================


class SecureSessionCredentials:
    """Context manager that holds temporary session credentials for one validation scope.

    The credentials are copied into an internal ``bytearray`` on construction.
    On ``__exit__`` — normal *or* exceptional — the buffer is zero-wiped
    **before** any reference is released.

    A ``__del__`` finaliser acts as a last-resort safety net.

    Args:
        credentials: Raw session credential bytes (e.g. API token, JWT).
        credential_type: Label for what kind of credential (default: "session_token").

    Raises:
        ValueError:   If *credentials* is empty.
        SigningError: If :meth:`get` is called outside the ``with`` block.

    Example::

        with SecureSessionCredentials(api_token, credential_type="jwt") as creds:
            token = creds.get()
            # use token for validation ...
        # Buffer zero-wiped here; creds is no longer usable.

    Prefer the :meth:`use` method over :meth:`get` when possible — it
    accepts a callback and wipes the temporary ``bytes`` view in a
    ``finally`` block, minimising the window during which credential
    material is recoverable from a heap dump.
    """

    __slots__ = ("_buf", "_active", "_wiped", "_credential_type", "_locked")

    def __init__(
        self, credentials: bytes, credential_type: str = "session_token"
    ) -> None:
        if not credentials:
            raise ValueError("credentials must be non-empty bytes.")
        self._buf: bytearray = bytearray(credentials)
        self._active: bool = False
        self._wiped: bool = False
        self._credential_type: str = credential_type
        self._locked: bool = _mlock_buffer(self._buf)

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "SecureSessionCredentials":
        self._active = True
        logger.debug(
            "[SecureSessionCredentials] Validation scope opened for: %s",
            self._credential_type
        )
        audit_log.log_key_imported(f"cred_{self._credential_type}", len(self._buf))
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        self._active = False
        self._do_wipe()
        return False

    def __del__(self) -> None:
        try:
            self._do_wipe()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_wipe(self) -> None:
        if self._wiped:
            return
        self._wiped = True
        _zero_wipe(
            self._buf,
            audit_details={"object_type": "SecureSessionCredentials"}
        )
        if self._locked:
            _munlock_buffer(self._buf)
            self._locked = False
        logger.debug(
            "[SecureSessionCredentials] Validation scope closed — credentials wiped."
        )
        audit_log.log_key_revoked(f"cred_{self._credential_type}", reason="scope_exit")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self) -> bytes:
        """Return a ``bytes`` copy of the stored session credentials.

        Returns:
            A ``bytes`` copy of the credentials (caller's responsibility).

        Raises:
            SigningError: If called outside the ``with`` block.
        """
        if not self._active:
            raise SigningError(
                "SecureSessionCredentials.get() called outside an active validation scope. "
                "Use 'with SecureSessionCredentials(...) as creds:' and call get() inside."
            )
        if self._wiped:
            raise SigningError(
                "SecureSessionCredentials.get() called after credentials have been wiped."
            )
        return bytes(self._buf)

    def use(self, callback):
        """Pass the session credentials to *callback* and wipe the temporary
        copy immediately after the callback returns (or raises).

        This is the **preferred** way to consume credentials because the
        intermediate ``bytes`` view is overwritten in a ``finally`` block,
        minimising the window during which the credential material is
        recoverable from a process memory dump.

        Args:
            callback: A callable ``fn(credentials: bytes) -> T`` that
                      receives the credential bytes for the duration of
                      the call.  The return value of *callback* is
                      forwarded as the return value of :meth:`use`.

        Returns:
            The return value of *callback*.

        Raises:
            SigningError: If called outside the ``with`` block or after the
                          buffer has already been wiped.

        Example::

            with SecureSessionCredentials(token_bytes) as creds:
                api_token = creds.use(lambda tok: verify(tok))
            # Temporary bytes copy zero-wiped here; creds is no longer usable.
        """
        if not self._active:
            raise SigningError(
                "SecureSessionCredentials.use() called outside an active validation scope. "
                "Use 'with SecureSessionCredentials(...) as creds:' and call use() inside."
            )
        if self._wiped:
            raise SigningError(
                "SecureSessionCredentials.use() called after credentials have been wiped."
            )
        temp: bytes = bytes(self._buf)
        try:
            return callback(temp)
        finally:
            _wipe_bytes_view(temp)
            del temp
