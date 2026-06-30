from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from crypto.engine import secure_sign_payload


class TestSecureSigningEngine:
    def test_secure_sign_payload_zeroes_local_material_after_signing(self):
        seen = {}

        def fake_backend(key_material: bytes, tx_hash: bytes) -> bytes:
            seen["key_material"] = key_material
            seen["tx_hash"] = tx_hash
            return b"signature"

        result = secure_sign_payload(b"secret-key", b"\x00" * 32, backend=fake_backend)

        assert result == b"signature"
        assert seen["key_material"] == b"secret-key"
        assert seen["tx_hash"] == b"\x00" * 32

    def test_secure_sign_payload_rejects_invalid_hash_length(self):
        with pytest.raises(ValueError, match="32 bytes"):
            secure_sign_payload(b"secret-key", b"short", backend=lambda *_: b"")
