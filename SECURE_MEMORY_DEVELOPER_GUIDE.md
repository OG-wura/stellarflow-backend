# Secure Memory Operations - Developer Guide

## Quick Start

### For TypeScript/Node.js Developers

Use the Python subprocess wrapper for cryptographic operations:

```typescript
import { spawn } from 'child_process';

// Secure signing with automatic key cleanup
async function sign(keyMaterial: Buffer, txHash: Buffer): Promise<Buffer> {
    const script = `
import sys
sys.path.insert(0, './src/crypto')
from signer import SecureKeyHandle

with SecureKeyHandle(sys.stdin.buffer.read()[:32]) as handle:
    sys.stdout.buffer.write(handle.sign(tx_hash))
    `;
    
    return spawnPythonProcess(script, keyMaterial);
}

// Secure session validation with automatic token cleanup
async function validateToken(token: Buffer): Promise<boolean> {
    const script = `
import sys
sys.path.insert(0, './src/crypto')
from signer import SecureSessionCredentials

with SecureSessionCredentials(sys.stdin.buffer.read()) as creds:
    validated = validate_jwt(creds.get())
    print('VALID' if validated else 'INVALID')
    `;
    
    const result = await spawnPythonProcess(script, token);
    return result.toString() === 'VALID';
}
```

### For Python Developers

Use context managers for guaranteed cleanup:

```python
from signer import SecureKeyHandle, SecureSessionCredentials, SecureVariableWrapper

# Signing - use SecureKeyHandle
with SecureKeyHandle(private_key_bytes, key_id="production_signer") as handle:
    signature = handle.sign(transaction_hash)
    return signature
# Key automatically wiped after with block

# Session validation - use SecureSessionCredentials  
with SecureSessionCredentials(jwt_token, credential_type="bearer_token") as creds:
    token = creds.get()
    is_valid = jwt.decode(token, options={"verify_signature": False})
    return is_valid
# Token automatically wiped after with block

# Generic sensitive data - use SecureVariableWrapper
with SecureVariableWrapper(database_password, label="db_credentials") as wrapper:
    password = wrapper.get()
    connect_to_database(host="prod-db", password=password)
# Password automatically wiped after with block
```

## Patterns by Use Case

### Pattern: Transaction Signing

```python
def sign_stellar_transaction(tx: TransactionBuilder, signing_key: bytes) -> str:
    """Sign a Stellar transaction with guaranteed key cleanup.
    
    Args:
        tx: Stellar TransactionBuilder
        signing_key: Raw 32-byte signing key
        
    Returns:
        Signed transaction XDR
        
    Example:
        signed_xdr = sign_stellar_transaction(tx, key_bytes)
    """
    # Build transaction hash
    tx_hash = tx.hash()
    
    # Sign with guaranteed cleanup
    with SecureKeyHandle(signing_key, key_id="stellar_signer") as handle:
        signature = handle.sign(tx_hash)
        
    # Now sign in transaction
    tx.sign_raw(signature)
    return tx.to_xdr()
```

### Pattern: API Key Validation

```python
def validate_request_with_api_key(request_headers: dict, api_key: bytes) -> bool:
    """Validate incoming request API key with guaranteed key cleanup.
    
    Args:
        request_headers: HTTP request headers dict
        api_key: Secret API key bytes
        
    Returns:
        True if valid, False otherwise
        
    Example:
        is_valid = validate_request_with_api_key(headers, secret_key)
    """
    provided_key = request_headers.get('X-API-Key', '').encode()
    
    with SecureVariableWrapper(api_key, label="api_key_validator") as wrapper:
        expected_key = wrapper.get()
        # Constant-time comparison
        is_valid = provided_key == expected_key
        
    return is_valid
```

### Pattern: Database Credential Management

```python
def connect_to_production_database(db_config: dict) -> Connection:
    """Connect to database with encrypted credentials and guaranteed cleanup.
    
    Args:
        db_config: Config dict with encrypted password
        
    Returns:
        Database connection
        
    Example:
        conn = connect_to_production_database(config)
    """
    # Decrypt password (e.g., from KMS)
    encrypted_password = db_config['password_encrypted']
    decryption_key = get_kms_key()
    
    decrypted_password = decrypt_with_kms(encrypted_password, decryption_key)
    password_bytes = decrypted_password.encode()
    
    # Use password in secure context
    with SecureVariableWrapper(password_bytes, label="db_password") as wrapper:
        pwd = wrapper.get()
        connection = connect(
            host=db_config['host'],
            user=db_config['user'],
            password=pwd.decode()
        )
    
    # Password wiped here, connection remains open
    return connection
```

### Pattern: Nested Sensitive Operations

```python
def multi_sig_transaction_signing(
    tx_hash: bytes,
    signer_keys: dict,  # {signer_id: key_bytes}
) -> dict:
    """Sign with multiple keys in nested secure contexts.
    
    Args:
        tx_hash: Transaction hash to sign
        signer_keys: Dict of signer_id -> key_bytes
        
    Returns:
        Dict of signer_id -> signature bytes
        
    Example:
        sigs = multi_sig_transaction_signing(hash, {"alice": key1, "bob": key2})
    """
    signatures = {}
    
    for signer_id, key_bytes in signer_keys.items():
        with SecureKeyHandle(key_bytes, key_id=f"signer_{signer_id}") as handle:
            signature = handle.sign(tx_hash)
            signatures[signer_id] = signature
            # Key wiped here before next iteration
            
    return signatures
```

### Pattern: Batch Operations with Resource Limits

```python
def batch_sign_transactions(transactions: list, signing_key: bytes) -> list:
    """Sign multiple transactions, cleaning key between operations.
    
    Args:
        transactions: List of transaction hashes
        signing_key: Single signing key used for all
        
    Returns:
        List of signatures
        
    Example:
        sigs = batch_sign_transactions([hash1, hash2, hash3], key)
    """
    signatures = []
    
    # Key is only held for duration of entire batch
    with SecureKeyHandle(signing_key, key_id="batch_signer") as handle:
        for tx_hash in transactions:
            signature = handle.sign(tx_hash)
            signatures.append(signature)
    
    # Key wiped after all operations complete
    return signatures
```

## Error Handling

### Handling SigningError

```python
from signer import SecureKeyHandle, SigningError

try:
    with SecureKeyHandle(key) as handle:
        sig = handle.sign(tx_hash)
except SigningError as e:
    # Key was already wiped, safe to log error
    logger.error("Signing failed: %s", str(e))  # No key material in error
    raise
```

### Handling Missing Dependencies

```python
from signer import SecureKeyHandle, SigningError

try:
    with SecureKeyHandle(key) as handle:
        sig = handle.sign(tx_hash)
except SigningError as e:
    if "neither 'stellar_sdk' nor 'PyNaCl'" in str(e):
        logger.error("Crypto library not installed: %s", str(e))
        # Install missing dependency
    raise
```

### Handling Invalid Input

```python
from signer import SecureKeyHandle, SigningError, MemorySecurityError

try:
    with SecureKeyHandle(key) as handle:
        if len(tx_hash) != 32:
            raise ValueError("tx_hash must be 32 bytes")
        sig = handle.sign(tx_hash)
except ValueError as e:
    logger.warning("Invalid input: %s", str(e))
    raise
except MemorySecurityError as e:
    # Critical security error - escalate immediately
    logger.critical("Memory security error: %s", str(e))
    alert_security_team()
    raise
```

## Testing Your Integration

### Unit Test Template

```python
import pytest
from signer import SecureKeyHandle, SecureSessionCredentials

def test_signing_with_secure_cleanup():
    """Verify signing works and key is cleaned up."""
    key_bytes = b'\x00' * 32
    tx_hash = b'\x01' * 32
    
    with SecureKeyHandle(key_bytes) as handle:
        sig = handle.sign(tx_hash)
        assert len(sig) == 64  # Ed25519 signature
    
    # Verify key was wiped
    assert all(b == 0 for b in handle._buf), "Key not wiped!"

def test_signing_fails_outside_context():
    """Verify signing fails outside context manager."""
    key_bytes = b'\x00' * 32
    handle = SecureKeyHandle(key_bytes)
    
    with pytest.raises(SigningError):
        handle.sign(b'\x01' * 32)

def test_exception_still_cleans_up():
    """Verify cleanup happens even if exception occurs."""
    key_bytes = b'\x00' * 32
    
    with pytest.raises(RuntimeError):
        with SecureKeyHandle(key_bytes) as handle:
            raise RuntimeError("Test error")
    
    # Key still wiped despite exception
    assert handle._wiped
    assert all(b == 0 for b in handle._buf)

def test_session_credentials_cleanup():
    """Verify session credentials are cleaned up."""
    token = b'jwt_secret_token'
    
    with SecureSessionCredentials(token) as creds:
        retrieved = creds.get()
        assert retrieved == token
    
    # Verify wiped
    assert all(b == 0 for b in creds._buf)

def test_audit_logging():
    """Verify audit trail records operations."""
    from signer import audit_log
    
    key_bytes = b'\x00' * 32
    
    with SecureKeyHandle(key_bytes, key_id="test_key") as handle:
        handle.sign(b'\x01' * 32)
    
    trail = audit_log.get_audit_trail()
    
    # Verify events recorded
    assert any(e['event'] == 'KEY_IMPORTED' for e in trail)
    assert any(e['event'] == 'SIGNING_OPERATION' for e in trail)
    assert any(e['event'] == 'KEY_REVOKED' for e in trail)
```

### Integration Test Template

```typescript
import { spawn } from 'child_process';
import * as fs from 'fs';

async function testSecureSigningIntegration() {
    // Test key (32 bytes)
    const keyMaterial = Buffer.from('0'.repeat(64), 'hex');
    const txHash = Buffer.from('1'.repeat(64), 'hex');
    
    // Python script using secure cleanup
    const script = `
import sys
sys.path.insert(0, './src/crypto')
from signer import SecureKeyHandle

key = sys.stdin.buffer.read(32)
with SecureKeyHandle(key) as handle:
    sig = handle.sign(${JSON.stringify(txHash.toString('hex'))})
    sys.stdout.buffer.write(sig)
    `;
    
    const proc = spawn('python3', ['-c', script]);
    
    return new Promise((resolve, reject) => {
        let output = Buffer.alloc(0);
        
        proc.stdin.write(keyMaterial);
        proc.stdin.end();
        
        proc.stdout.on('data', (chunk) => {
            output = Buffer.concat([output, chunk]);
        });
        
        proc.on('close', (code) => {
            if (code === 0 && output.length === 64) {
                resolve(output);
            } else {
                reject(new Error('Signing failed'));
            }
        });
    });
}
```

## Performance Optimization

### Cache Expensive Operations

```python
# SLOW: Recreate handle for each operation
for tx_hash in tx_hashes:
    with SecureKeyHandle(key) as handle:
        sig = handle.sign(tx_hash)  # Handle recreated each iteration

# FAST: Reuse handle for batch
with SecureKeyHandle(key) as handle:
    for tx_hash in tx_hashes:
        sig = handle.sign(tx_hash)  # No handle recreation
```

### Minimize Context Nesting

```python
# SLOW: Deeply nested contexts
with SecureKeyHandle(key1) as k1:
    with SecureSessionCredentials(token) as t:
        with SecureVariableWrapper(pwd) as p:
            result = do_operation(k1, t, p)

# FAST: Flat structure
with SecureKeyHandle(key1) as k1:
    sig1 = k1.sign(hash1)

with SecureSessionCredentials(token) as t:
    val = t.get()

with SecureVariableWrapper(pwd) as p:
    pwd_val = p.get()
```

## Troubleshooting

### Issue: "mlock unavailable" warning appears

**Cause**: Process lacks `CAP_IPC_LOCK` capability (Linux) or equivalent privilege

**Solution**: Grant capability to Node process
```bash
# Linux
setcap cap_ipc_lock=+ep /usr/bin/node

# Or raise resource limits
ulimit -l unlimited

# Or run with elevated privileges (not recommended for production)
sudo node app.js
```

### Issue: "Neither 'stellar_sdk' nor 'PyNaCl' is installed"

**Cause**: Cryptography library not available in Python environment

**Solution**: Install one
```bash
pip install stellar-sdk
# or
pip install PyNaCl
```

### Issue: Signing returns invalid signature

**Cause**: Transaction hash not exactly 32 bytes

**Solution**: Verify hash size
```python
if len(tx_hash) != 32:
    raise ValueError(f"tx_hash must be 32 bytes, got {len(tx_hash)}")
```

## Security Best Practices

### 1. Never Log Key Material

```python
# ❌ BAD
logger.debug(f"Key: {key_bytes.hex()}")

# ✅ GOOD
logger.debug("Key size: %d bytes", len(key_bytes))
audit_log.log_key_imported("my_key", len(key_bytes))
```

### 2. Use Context Managers Always

```python
# ❌ BAD
handle = SecureKeyHandle(key)
sig = handle.sign(tx_hash)

# ✅ GOOD
with SecureKeyHandle(key) as handle:
    sig = handle.sign(tx_hash)
```

### 3. Pass Keys via stdin, Not Args

```typescript
// ❌ BAD - key visible in process listing
spawn('python3', ['-c', script, key.toString('hex')])

// ✅ GOOD - key passed via stdin
const proc = spawn('python3', ['-c', script]);
proc.stdin.write(key);
```

### 4. Never Store Key References

```python
# ❌ BAD
key_ref = None
with SecureKeyHandle(key) as handle:
    key_ref = handle._buf  # Reference escapes!

# ✅ GOOD
with SecureKeyHandle(key) as handle:
    sig = handle.sign(tx_hash)
    # No references escape context
```

### 5. Validate All Inputs

```python
# ✅ GOOD
if not key_bytes or len(key_bytes) != 32:
    raise ValueError("Invalid key")
if not tx_hash or len(tx_hash) != 32:
    raise ValueError("Invalid tx_hash")

with SecureKeyHandle(key_bytes) as handle:
    sig = handle.sign(tx_hash)
```

---

**Version**: 1.0  
**Status**: Production Ready  
**Last Updated**: 2026-06-29
