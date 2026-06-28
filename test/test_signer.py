"""
test/test_signer.py
~~~~~~~~~~~~~~~~~~~
Unit tests for src/crypto/signer.py and src/crypto/vault_manager.py.

Covers:
- SecureKeyHandle lifecycle: enter/exit, sign gating, wipe idempotency
- SecureKeyHandle mlock: pages are pinned on __init__ and unlocked after wipe
- SecureSessionCredentials lifecycle: enter/exit, get gating, wipe idempotency
- SecureSessionCredentials mlock: pages are pinned on __init__ and unlocked after wipe
- VaultManager: register, open_context, retrieve, close_context, revoke, purge
- VaultManager mlock: register pins pages; revoke/purge unlock after zero-wipe
- _zero_wipe: buffer is fully zeroed
- SigningError propagation
"""

from __future__ import annotations

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# Ensure the repo root is on sys.path so we can import src.crypto.*
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.crypto.signer import (
    SecureKeyHandle,
    SecureSessionCredentials,
    SigningError,
    _zero_wipe,
    _mlock_buffer,
    _munlock_buffer,
)
from src.crypto.vault_manager import VaultManager, VaultContext, vault


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_KEY = b"\x01" * 32
_DUMMY_CREDS = b"session-token-bytes"
_DUMMY_HASH = b"\xab" * 32


def _make_mock_stellar(signature: bytes = b"\xff" * 64):
    """Return a minimal fake stellar_sdk module."""
    kp = MagicMock()
    kp.sign.return_value = signature
    Keypair = MagicMock()
    Keypair.from_raw_ed25519_seed.return_value = kp
    mod = types.ModuleType("stellar_sdk")
    mod.Keypair = Keypair
    return mod


# ---------------------------------------------------------------------------
# _zero_wipe
# ---------------------------------------------------------------------------


class TestZeroWipe(unittest.TestCase):
    def test_zeroes_buffer(self):
        buf = bytearray(b"\xff" * 16)
        _zero_wipe(buf)
        self.assertEqual(buf, bytearray(16))

    def test_empty_buffer_is_noop(self):
        buf = bytearray()
        _zero_wipe(buf)  # must not raise


# ---------------------------------------------------------------------------
# SecureKeyHandle — lifecycle
# ---------------------------------------------------------------------------


class TestSecureKeyHandleLifecycle(unittest.TestCase):
    def test_sign_requires_with_block(self):
        handle = SecureKeyHandle(_DUMMY_KEY)
        with self.assertRaises(SigningError):
            handle.sign(_DUMMY_HASH)

    def test_sign_after_exit_raises(self):
        with patch.dict("sys.modules", {"stellar_sdk": _make_mock_stellar()}):
            with SecureKeyHandle(_DUMMY_KEY) as h:
                pass  # exits here → wipe
            with self.assertRaises(SigningError):
                h.sign(_DUMMY_HASH)

    def test_wipe_idempotent(self):
        h = SecureKeyHandle(_DUMMY_KEY)
        h._do_wipe()
        h._do_wipe()  # must not raise or wipe twice

    def test_empty_key_raises(self):
        with self.assertRaises(ValueError):
            SecureKeyHandle(b"")

    def test_sign_inside_with_block(self):
        sig = b"\xff" * 64
        with patch.dict("sys.modules", {"stellar_sdk": _make_mock_stellar(sig)}):
            with SecureKeyHandle(_DUMMY_KEY) as h:
                result = h.sign(_DUMMY_HASH)
        self.assertEqual(result, sig)

    def test_sign_bad_hash_length(self):
        with SecureKeyHandle(_DUMMY_KEY) as h:
            with self.assertRaises(ValueError):
                h.sign(b"\x00" * 16)

    def test_buffer_zeroed_after_exit(self):
        with patch.dict("sys.modules", {"stellar_sdk": _make_mock_stellar()}):
            with SecureKeyHandle(_DUMMY_KEY) as h:
                buf = h._buf
        self.assertEqual(buf, bytearray(32))

    def test_pynacl_fallback(self):
        """Falls back to PyNaCl when stellar_sdk is absent."""
        sig = b"\xee" * 64

        fake_sig = MagicMock()
        fake_sig.signature = sig
        sk_instance = MagicMock()
        sk_instance.sign.return_value = fake_sig
        SigningKey = MagicMock(return_value=sk_instance)
        nacl_mod = types.ModuleType("nacl")
        nacl_signing = types.ModuleType("nacl.signing")
        nacl_signing.SigningKey = SigningKey
        nacl_mod.signing = nacl_signing

        with patch.dict(
            "sys.modules",
            {"stellar_sdk": None, "nacl": nacl_mod, "nacl.signing": nacl_signing},
        ):
            with SecureKeyHandle(_DUMMY_KEY) as h:
                result = h.sign(_DUMMY_HASH)
        self.assertEqual(result, sig)


# ---------------------------------------------------------------------------
# SecureKeyHandle — mlock
# ---------------------------------------------------------------------------


class TestSecureKeyHandleMlock(unittest.TestCase):
    def test_mlock_called_on_init(self):
        with patch("src.crypto.signer._mlock_buffer", return_value=True) as mock_ml:
            h = SecureKeyHandle(_DUMMY_KEY)
        mock_ml.assert_called_once()
        self.assertTrue(h._locked)

    def test_munlock_called_after_wipe(self):
        with (
            patch("src.crypto.signer._mlock_buffer", return_value=True),
            patch("src.crypto.signer._munlock_buffer") as mock_mu,
        ):
            with patch.dict("sys.modules", {"stellar_sdk": _make_mock_stellar()}):
                with SecureKeyHandle(_DUMMY_KEY):
                    pass
        mock_mu.assert_called_once()

    def test_munlock_not_called_when_lock_failed(self):
        with (
            patch("src.crypto.signer._mlock_buffer", return_value=False),
            patch("src.crypto.signer._munlock_buffer") as mock_mu,
        ):
            with patch.dict("sys.modules", {"stellar_sdk": _make_mock_stellar()}):
                with SecureKeyHandle(_DUMMY_KEY):
                    pass
        mock_mu.assert_not_called()

    def test_locked_false_after_wipe(self):
        with (
            patch("src.crypto.signer._mlock_buffer", return_value=True),
            patch("src.crypto.signer._munlock_buffer"),
        ):
            h = SecureKeyHandle(_DUMMY_KEY)
            h._do_wipe()
        self.assertFalse(h._locked)


# ---------------------------------------------------------------------------
# SecureSessionCredentials — lifecycle
# ---------------------------------------------------------------------------


class TestSecureSessionCredentialsLifecycle(unittest.TestCase):
    def test_get_requires_with_block(self):
        creds = SecureSessionCredentials(_DUMMY_CREDS)
        with self.assertRaises(SigningError):
            creds.get()

    def test_get_after_exit_raises(self):
        with SecureSessionCredentials(_DUMMY_CREDS) as c:
            pass
        with self.assertRaises(SigningError):
            c.get()

    def test_get_inside_with_block(self):
        with SecureSessionCredentials(_DUMMY_CREDS) as c:
            result = c.get()
        self.assertEqual(result, _DUMMY_CREDS)

    def test_wipe_idempotent(self):
        c = SecureSessionCredentials(_DUMMY_CREDS)
        c._do_wipe()
        c._do_wipe()

    def test_empty_credentials_raises(self):
        with self.assertRaises(ValueError):
            SecureSessionCredentials(b"")

    def test_buffer_zeroed_after_exit(self):
        with SecureSessionCredentials(_DUMMY_CREDS) as c:
            buf = c._buf
        self.assertEqual(buf, bytearray(len(_DUMMY_CREDS)))


# ---------------------------------------------------------------------------
# SecureSessionCredentials — mlock (new behaviour added by this PR)
# ---------------------------------------------------------------------------


class TestSecureSessionCredentialsMlock(unittest.TestCase):
    """Verify that session credential pages are pinned and later unlocked."""

    def test_mlock_called_on_init(self):
        with patch("src.crypto.signer._mlock_buffer", return_value=True) as mock_ml:
            c = SecureSessionCredentials(_DUMMY_CREDS)
        mock_ml.assert_called_once()
        self.assertTrue(c._locked)

    def test_munlock_called_after_wipe(self):
        with (
            patch("src.crypto.signer._mlock_buffer", return_value=True),
            patch("src.crypto.signer._munlock_buffer") as mock_mu,
        ):
            with SecureSessionCredentials(_DUMMY_CREDS):
                pass
        mock_mu.assert_called_once()

    def test_munlock_not_called_when_lock_failed(self):
        with (
            patch("src.crypto.signer._mlock_buffer", return_value=False),
            patch("src.crypto.signer._munlock_buffer") as mock_mu,
        ):
            with SecureSessionCredentials(_DUMMY_CREDS):
                pass
        mock_mu.assert_not_called()

    def test_locked_false_after_wipe(self):
        with (
            patch("src.crypto.signer._mlock_buffer", return_value=True),
            patch("src.crypto.signer._munlock_buffer"),
        ):
            c = SecureSessionCredentials(_DUMMY_CREDS)
            c._do_wipe()
        self.assertFalse(c._locked)

    def test_locked_slot_exists(self):
        """_locked must be a declared slot, not a dynamic attribute."""
        self.assertIn("_locked", SecureSessionCredentials.__slots__)


# ---------------------------------------------------------------------------
# VaultManager — basic contract
# ---------------------------------------------------------------------------


class TestVaultManagerContract(unittest.TestCase):
    def setUp(self):
        vault.purge()

    def test_register_and_retrieve(self):
        vault.register("k1", b"secret-value")
        ctx = vault.open_context("decryption")
        result = vault.retrieve("k1", ctx)
        self.assertEqual(result, b"secret-value")
        vault.close_context(ctx)

    def test_retrieve_invalid_context_raises(self):
        vault.register("k2", b"x" * 16)
        ctx = vault.open_context("s")
        vault.close_context(ctx)
        with self.assertRaises(PermissionError):
            vault.retrieve("k2", ctx)

    def test_duplicate_register_raises(self):
        vault.register("k3", b"v")
        with self.assertRaises(ValueError):
            vault.register("k3", b"v2")

    def test_revoke_removes_key(self):
        vault.register("k4", b"v")
        vault.revoke("k4")
        ctx = vault.open_context("s")
        with self.assertRaises(KeyError):
            vault.retrieve("k4", ctx)

    def test_purge_removes_all(self):
        vault.register("k5", b"v1")
        vault.register("k6", b"v2")
        vault.purge()
        ctx = vault.open_context("s")
        with self.assertRaises(KeyError):
            vault.retrieve("k5", ctx)
        with self.assertRaises(KeyError):
            vault.retrieve("k6", ctx)

    def test_vault_context_requires_manager(self):
        with self.assertRaises(TypeError):
            VaultContext(object(), "scope", b"tok")

    def test_retrieve_wrong_type_raises(self):
        vault.register("k7", b"v")
        with self.assertRaises(TypeError):
            vault.retrieve("k7", "not-a-context")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# VaultManager — mlock (new behaviour added by this PR)
# ---------------------------------------------------------------------------


class TestVaultManagerMlock(unittest.TestCase):
    def setUp(self):
        vault.purge()

    def test_mlock_called_on_register(self):
        with patch("src.crypto.vault_manager._mlock", return_value=True) as mock_ml:
            vault.register("mk1", b"key-material")
        mock_ml.assert_called_once()

    def test_munlock_called_on_revoke(self):
        with (
            patch("src.crypto.vault_manager._mlock", return_value=True),
            patch("src.crypto.vault_manager._munlock") as mock_mu,
        ):
            vault.register("mk2", b"key-material")
            vault.revoke("mk2")
        mock_mu.assert_called_once()

    def test_munlock_called_on_purge(self):
        with (
            patch("src.crypto.vault_manager._mlock", return_value=True),
            patch("src.crypto.vault_manager._munlock") as mock_mu,
        ):
            vault.register("mk3", b"key-a")
            vault.register("mk4", b"key-b")
            vault.purge()
        self.assertEqual(mock_mu.call_count, 2)

    def test_munlock_skipped_when_lock_failed(self):
        with (
            patch("src.crypto.vault_manager._mlock", return_value=False),
            patch("src.crypto.vault_manager._munlock") as mock_mu,
        ):
            vault.register("mk5", b"key-material")
            vault.revoke("mk5")
        mock_mu.assert_not_called()

    def test_buffer_zeroed_before_unlock_on_revoke(self):
        """The buffer must be all-zeros by the time _munlock is called."""
        captured = []

        def fake_munlock(buf):
            captured.append(bytearray(buf))

        with (
            patch("src.crypto.vault_manager._mlock", return_value=True),
            patch("src.crypto.vault_manager._munlock", side_effect=fake_munlock),
        ):
            vault.register("mk6", b"\xff" * 16)
            vault.revoke("mk6")

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], bytearray(16))

    def test_duplicate_register_does_not_leak_lock(self):
        """mlock is rolled back if register raises for a duplicate key_id."""
        with (
            patch("src.crypto.vault_manager._mlock", return_value=True),
            patch("src.crypto.vault_manager._munlock") as mock_mu,
        ):
            vault.register("dup", b"original")
            with self.assertRaises(ValueError):
                vault.register("dup", b"second")
        # The rolled-back mlock must have triggered one munlock call.
        mock_mu.assert_called_once()


if __name__ == "__main__":
    unittest.main()
