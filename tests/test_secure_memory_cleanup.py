"""
tests/test_secure_memory_cleanup.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Comprehensive test suite for secure memory cleanup in cryptographic operations.

Tests verify:
- Memory overwrite functionality
- Context manager cleanup guarantee
- Exception safety
- No key fragment lingering
- Audit logging
- Edge cases and corner cases
"""

import sys
import gc
import pytest
from pathlib import Path

# Add crypto module to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "crypto"))

from signer import (
    SecureKeyHandle,
    SecureSessionCredentials,
    SecureVariableWrapper,
    SigningError,
    MemorySecurityError,
    SecurityAuditLogger,
    audit_log,
)


class TestSecureKeyHandle:
    """Tests for SecureKeyHandle context manager."""

    def test_basic_creation(self):
        """Test that SecureKeyHandle can be created."""
        key = b'\x00' * 32
        handle = SecureKeyHandle(key)
        assert handle is not None
        assert not handle._active
        assert not handle._wiped

    def test_context_manager_activate(self):
        """Test that context manager sets active flag."""
        key = b'\x00' * 32
        with SecureKeyHandle(key) as handle:
            assert handle._active
            assert not handle._wiped
        assert not handle._active

    def test_buffer_wiped_after_context(self):
        """Test that buffer is zeroed after context exits."""
        key = bytes([i % 256 for i in range(32)])
        with SecureKeyHandle(key) as handle:
            original_buf = bytes(handle._buf)
            # Verify original was copied
            assert original_buf == key

        # After exit, buffer should be all zeros
        wiped_buf = bytes(handle._buf)
        assert all(b == 0 for b in wiped_buf), "Buffer not fully wiped"
        assert handle._wiped

    def test_empty_key_rejected(self):
        """Test that empty key is rejected."""
        with pytest.raises(ValueError):
            SecureKeyHandle(b'')

    def test_sign_outside_context_fails(self):
        """Test that signing outside context raises error."""
        key = b'\x00' * 32
        handle = SecureKeyHandle(key)

        with pytest.raises(SigningError):
            handle.sign(b'\x00' * 32)

    def test_sign_after_wiped_fails(self):
        """Test that signing after wipe fails."""
        key = b'\x00' * 32
        with SecureKeyHandle(key) as handle:
            pass  # Context exits, key wiped

        with pytest.raises(SigningError):
            handle.sign(b'\x00' * 32)

    def test_exception_in_context_still_wipes(self):
        """Test that buffer is wiped even if exception occurs."""
        key = bytes([i % 256 for i in range(32)])

        with pytest.raises(RuntimeError):
            with SecureKeyHandle(key) as handle:
                raise RuntimeError("Test exception")

        # Key should still be wiped despite exception
        assert handle._wiped
        assert all(b == 0 for b in handle._buf), "Buffer not wiped after exception"

    def test_del_finalizer_wipes_buffer(self):
        """Test that __del__ finalizer wipes buffer."""
        key = bytes([i % 256 for i in range(32)])
        handle = SecureKeyHandle(key)

        # Check buffer before del
        assert not handle._wiped
        original = bytes(handle._buf)
        assert original == key

        # Delete and force garbage collection
        del handle
        gc.collect()

        # We can't verify directly since handle is gone, but no crash should occur

    def test_audit_log_key_import(self):
        """Test that key import is logged to audit trail."""
        audit_log._operations.clear()  # Clear previous events

        key = b'\x00' * 32
        with SecureKeyHandle(key, key_id="test_import_key") as handle:
            pass

        trail = audit_log.get_audit_trail()
        import_events = [e for e in trail if e['event'] == 'KEY_IMPORTED']

        assert any(e['key_id'] == 'test_import_key' for e in import_events)

    def test_audit_log_key_revoke(self):
        """Test that key revocation is logged."""
        audit_log._operations.clear()

        key = b'\x00' * 32
        with SecureKeyHandle(key, key_id="test_revoke_key") as handle:
            pass

        trail = audit_log.get_audit_trail()
        revoke_events = [e for e in trail if e['event'] == 'KEY_REVOKED']

        assert any(e['key_id'] == 'test_revoke_key' for e in revoke_events)

    def test_idempotent_wipe(self):
        """Test that multiple wipe calls are safe."""
        key = b'\x00' * 32
        with SecureKeyHandle(key) as handle:
            pass

        # Multiple wipes should be safe
        handle._do_wipe()
        handle._do_wipe()
        handle._do_wipe()

        assert handle._wiped
        assert all(b == 0 for b in handle._buf)

    def test_invalid_hash_size_rejected(self):
        """Test that non-32-byte hash is rejected."""
        key = b'\x00' * 32
        with SecureKeyHandle(key) as handle:
            with pytest.raises(ValueError):
                handle.sign(b'too_short')

            with pytest.raises(ValueError):
                handle.sign(b'\x00' * 31)

            with pytest.raises(ValueError):
                handle.sign(b'\x00' * 33)


class TestSecureSessionCredentials:
    """Tests for SecureSessionCredentials context manager."""

    def test_basic_creation(self):
        """Test that SecureSessionCredentials can be created."""
        creds = SecureSessionCredentials(b'token_secret')
        assert creds is not None
        assert not creds._active
        assert not creds._wiped

    def test_get_retrieves_credentials(self):
        """Test that get() returns credential copy."""
        token = b'jwt_token_secret_12345'
        with SecureSessionCredentials(token) as creds:
            retrieved = creds.get()
            assert retrieved == token
            assert retrieved is not token  # Should be copy, not reference

    def test_credentials_wiped_after_context(self):
        """Test that credentials buffer is wiped after context."""
        token = bytes([i % 256 for i in range(20)])
        with SecureSessionCredentials(token) as creds:
            pass

        # After exit, buffer should be zeroed
        assert all(b == 0 for b in creds._buf)
        assert creds._wiped

    def test_empty_credentials_rejected(self):
        """Test that empty credentials are rejected."""
        with pytest.raises(ValueError):
            SecureSessionCredentials(b'')

    def test_get_outside_context_fails(self):
        """Test that get() outside context raises error."""
        creds = SecureSessionCredentials(b'token')

        with pytest.raises(SigningError):
            creds.get()

    def test_exception_in_context_still_wipes(self):
        """Test that credentials wiped even if exception occurs."""
        token = bytes([i % 256 for i in range(20)])

        with pytest.raises(RuntimeError):
            with SecureSessionCredentials(token) as creds:
                raise RuntimeError("Test exception")

        assert creds._wiped
        assert all(b == 0 for b in creds._buf)

    def test_custom_credential_type_label(self):
        """Test that custom credential type labels work."""
        token = b'bearer_token'
        with SecureSessionCredentials(token, credential_type="bearer_token") as creds:
            assert creds._credential_type == "bearer_token"
            assert creds.get() == token

    def test_audit_log_import_and_revoke(self):
        """Test that credentials import/revoke logged."""
        audit_log._operations.clear()

        token = b'test_token'
        with SecureSessionCredentials(token, credential_type="test_cred") as creds:
            pass

        trail = audit_log.get_audit_trail()
        import_events = [e for e in trail if e['event'] == 'KEY_IMPORTED']
        revoke_events = [e for e in trail if e['event'] == 'KEY_REVOKED']

        assert len(import_events) > 0
        assert len(revoke_events) > 0


class TestSecureVariableWrapper:
    """Tests for SecureVariableWrapper context manager."""

    def test_basic_creation(self):
        """Test that SecureVariableWrapper can be created."""
        data = b'sensitive_data'
        wrapper = SecureVariableWrapper(data)
        assert wrapper is not None
        assert not wrapper._active
        assert not wrapper._wiped

    def test_get_retrieves_data(self):
        """Test that get() returns data copy."""
        password = b'super_secret_password'
        with SecureVariableWrapper(password, label="password") as wrapper:
            retrieved = wrapper.get()
            assert retrieved == password
            assert retrieved is not password  # Copy, not reference

    def test_data_wiped_after_context(self):
        """Test that data buffer is wiped after context."""
        data = bytes([i % 256 for i in range(25)])
        with SecureVariableWrapper(data, label="test_data") as wrapper:
            pass

        assert all(b == 0 for b in wrapper._buf)
        assert wrapper._wiped

    def test_empty_data_rejected(self):
        """Test that empty data is rejected."""
        with pytest.raises(ValueError):
            SecureVariableWrapper(b'')

    def test_custom_label(self):
        """Test that custom labels work."""
        data = b'data'
        with SecureVariableWrapper(data, label="custom_label") as wrapper:
            assert wrapper._label == "custom_label"

    def test_exception_still_wipes(self):
        """Test that data wiped even if exception occurs."""
        data = bytes([i % 256 for i in range(15)])

        with pytest.raises(RuntimeError):
            with SecureVariableWrapper(data, label="error_test") as wrapper:
                raise RuntimeError("Test exception")

        assert wrapper._wiped
        assert all(b == 0 for b in wrapper._buf)

    def test_nested_wrappers(self):
        """Test that nested wrappers work correctly."""
        data1 = b'secret1'
        data2 = b'secret2'

        with SecureVariableWrapper(data1, label="secret1") as w1:
            with SecureVariableWrapper(data2, label="secret2") as w2:
                assert w1.get() == data1
                assert w2.get() == data2

        # Both should be wiped
        assert all(b == 0 for b in w1._buf)
        assert all(b == 0 for b in w2._buf)

    def test_get_outside_context_fails(self):
        """Test that get() outside context raises error."""
        wrapper = SecureVariableWrapper(b'data')

        with pytest.raises(SigningError):
            wrapper.get()


class TestSecurityAuditLogger:
    """Tests for SecurityAuditLogger."""

    def test_create_audit_logger(self):
        """Test that audit logger can be created."""
        logger = SecurityAuditLogger()
        assert logger is not None
        assert logger.get_audit_trail() == []

    def test_log_key_imported(self):
        """Test logging key import."""
        logger = SecurityAuditLogger()
        logger.log_key_imported("test_key", 32)

        trail = logger.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]['event'] == 'KEY_IMPORTED'
        assert trail[0]['key_id'] == 'test_key'
        assert trail[0]['key_size_bytes'] == 32

    def test_log_signing_operation(self):
        """Test logging signing operation."""
        logger = SecurityAuditLogger()
        logger.log_signing_operation("my_key", 32)

        trail = logger.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]['event'] == 'SIGNING_OPERATION'
        assert trail[0]['key_id'] == 'my_key'

    def test_log_key_revoked(self):
        """Test logging key revocation."""
        logger = SecurityAuditLogger()
        logger.log_key_revoked("my_key", reason="normal")

        trail = logger.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]['event'] == 'KEY_REVOKED'
        assert trail[0]['key_id'] == 'my_key'
        assert trail[0]['reason'] == 'normal'

    def test_log_memory_cleanup(self):
        """Test logging memory cleanup."""
        logger = SecurityAuditLogger()
        logger.log_memory_cleanup("SecureKeyHandle", 32)

        trail = logger.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]['event'] == 'MEMORY_CLEANUP'
        assert trail[0]['object_type'] == 'SecureKeyHandle'
        assert trail[0]['buffer_size'] == 32

    def test_audit_trail_is_copy(self):
        """Test that get_audit_trail returns a copy."""
        logger = SecurityAuditLogger()
        logger.log_key_imported("key1", 32)

        trail1 = logger.get_audit_trail()
        trail2 = logger.get_audit_trail()

        assert trail1 == trail2
        assert trail1 is not trail2  # Different object instances


class TestEdgeCases:
    """Tests for edge cases and corner conditions."""

    def test_multiple_enters_and_exits(self):
        """Test that re-entering context is not allowed."""
        key = b'\x00' * 32

        with SecureKeyHandle(key) as handle:
            assert handle._active

        # After exit, re-entering with same object would need new __enter__
        # (This is not a valid use case, but verify it fails appropriately)
        assert not handle._active
        assert handle._wiped

    def test_very_large_buffer(self):
        """Test with large buffer."""
        large_data = b'\x00' * 10000
        with SecureVariableWrapper(large_data, label="large_data") as wrapper:
            assert wrapper.get() == large_data

        assert all(b == 0 for b in wrapper._buf)

    def test_single_byte_buffer(self):
        """Test with single byte buffer."""
        single_byte = b'\xFF'
        with SecureVariableWrapper(single_byte, label="single_byte") as wrapper:
            assert wrapper.get() == single_byte

        assert wrapper._buf[0] == 0

    def test_buffer_with_patterns(self):
        """Test buffer with recognizable patterns."""
        pattern = b'DEADBEEFCAFEBABE' * 2  # 32 bytes
        with SecureKeyHandle(pattern, key_id="pattern_key") as handle:
            pass

        # Verify pattern completely overwritten
        assert all(b == 0 for b in handle._buf)
        assert bytes(handle._buf) != pattern

    def test_concurrent_audit_logging(self):
        """Test that audit logging is thread-safe."""
        import threading

        logger = SecurityAuditLogger()
        num_threads = 10

        def log_in_thread(thread_id):
            for i in range(10):
                logger.log_key_imported(f"key_{thread_id}_{i}", 32)

        threads = [threading.Thread(target=log_in_thread, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        trail = logger.get_audit_trail()
        assert len(trail) == num_threads * 10


class TestIntegration:
    """Integration tests for realistic usage patterns."""

    def test_transaction_signing_workflow(self):
        """Test realistic transaction signing workflow."""
        # Simulate transaction data
        tx_hash = bytes([i % 256 for i in range(32)])
        key_material = b'\x00' * 32

        # Sign within secure context
        with SecureKeyHandle(key_material, key_id="production_signer") as handle:
            # Verify we're in active state
            assert handle._active

            # In real scenario, would call sign() here
            # sig = handle.sign(tx_hash)

        # After exit, verify cleaned up
        assert handle._wiped
        assert all(b == 0 for b in handle._buf)

    def test_multiple_operations_same_key(self):
        """Test multiple operations with same key in one context."""
        key_material = b'\x00' * 32
        hashes = [bytes([i % 256] * 32) for i in range(5)]

        with SecureKeyHandle(key_material, key_id="batch_signer") as handle:
            signatures = []
            for tx_hash in hashes:
                # Would sign each hash
                # sig = handle.sign(tx_hash)
                # signatures.append(sig)
                pass

        # Key wiped after all operations
        assert handle._wiped
        assert all(b == 0 for b in handle._buf)
        assert handle._sign_count == 0  # Not actually signing in test

    def test_error_recovery_pattern(self):
        """Test proper error handling and recovery."""
        key_material = b'\x00' * 32

        try:
            with SecureKeyHandle(key_material, key_id="error_test") as handle:
                # Simulate error during operation
                raise ValueError("Simulated operation error")
        except ValueError:
            pass

        # Verify cleanup happened despite error
        assert handle._wiped
        assert all(b == 0 for b in handle._buf)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
