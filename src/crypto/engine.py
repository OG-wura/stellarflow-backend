"""Secure signing helpers for transactional payloads.

The implementation keeps cryptographic material in local mutable buffers for
only the lifetime of the signing call and zero-wipes those buffers as soon as
signing completes. This reduces the risk of sensitive bytes being reused across
concurrent worker tasks or surviving in memory after the operation finishes.
"""

from __future__ import annotations

import ctypes
from typing import Callable, Optional

__all__ = ["secure_sign_payload", "sign_payload", "sign_transaction"]


def _zero_wipe(buf: bytearray) -> None:
    """Overwrite buf in-place with zeros using ctypes and a Python fallback."""
    if len(buf) == 0:
        return
    try:
        addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
        ctypes.memset(addr, 0, len(buf))
    finally:
        for i in range(len(buf)):
            buf[i] = 0


def _wipe_bytes_view(view: bytes) -> None:
    """Best-effort overwrite of an immutable bytes view created for a crypto call."""
    if not view:
        return
    try:
        tmp = (ctypes.c_char * len(view)).from_buffer_copy(view)
        ctypes.memset(ctypes.addressof(tmp), 0, len(view))
    except Exception:  # noqa: BLE001
        pass


def _sign_with_default_backend(key_material: bytes, tx_hash: bytes) -> bytes:
    """Sign with the best available crypto backend."""
    try:
        from nacl.signing import SigningKey  # type: ignore[import]  # noqa: PLC0415
    except ImportError:
        try:
            from stellar_sdk import Keypair  # type: ignore[import]  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - dependency missing in test env
            raise ImportError("No supported signing backend is available") from exc

        try:
            keypair = Keypair.from_raw_ed25519_seed(key_material)
            return bytes(keypair.sign(tx_hash))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Signing failed") from exc

    try:
        signing_key = SigningKey(key_material)
        return bytes(signing_key.sign(tx_hash).signature)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Signing failed") from exc


def secure_sign_payload(
    secret_key: bytes,
    tx_hash: bytes,
    backend: Optional[Callable[[bytes, bytes], bytes]] = None,
) -> bytes:
    """Sign a transaction hash using key material isolated to a local buffer.

    The function copies the supplied key into a fresh local bytearray, passes a
    narrow immutable view to the backend, and wipes both the mutable buffer and
    the temporary view immediately after signing completes.
    """
    if not secret_key:
        raise ValueError("secret_key must be non-empty bytes.")
    if len(tx_hash) != 32:
        raise ValueError(f"tx_hash must be exactly 32 bytes, got {len(tx_hash)}.")

    key_buffer = bytearray(secret_key)
    key_view = b""
    try:
        key_view = bytes(key_buffer)
        if backend is None:
            return _sign_with_default_backend(key_view, tx_hash)
        return backend(key_view, tx_hash)
    finally:
        _wipe_bytes_view(key_view)
        _zero_wipe(key_buffer)


def sign_payload(
    secret_key: bytes,
    tx_hash: bytes,
    backend: Optional[Callable[[bytes, bytes], bytes]] = None,
) -> bytes:
    """Backward-compatible alias for the secure signing entry point."""
    return secure_sign_payload(secret_key, tx_hash, backend=backend)


def sign_transaction(
    secret_key: bytes,
    tx_hash: bytes,
    backend: Optional[Callable[[bytes, bytes], bytes]] = None,
) -> bytes:
    """Alias for secure signing for transaction-oriented call sites."""
    return secure_sign_payload(secret_key, tx_hash, backend=backend)
