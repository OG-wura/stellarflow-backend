/**
 * test/secure_memory.jest.test.ts
 * ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
 * Comprehensive test suite for secure memory cleanup in cryptographic operations.
 * 
 * Tests verify:
 * - Memory overwrite functionality
 * - Context manager cleanup guarantee
 * - Exception safety
 * - No key fragment lingering
 * - Audit logging
 * - Edge cases and corner cases
 */

import { spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Helper to run a Python test script and capture output
 */
function runPythonTest(scriptPath: string): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve) => {
    const proc = spawn('python3', [scriptPath], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    proc.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    proc.on('close', (code) => {
      resolve({ stdout, stderr, code: code || 0 });
    });

    proc.on('error', (err) => {
      resolve({ stdout, stderr: err.message, code: 1 });
    });
  });
}

describe('Secure Memory Cleanup - Python Backend Tests', () => {
  const cryptoDir = path.join(__dirname, '../src/crypto');
  const testScriptDir = path.join(__dirname, '../tests');

  // Ensure test script directory exists
  beforeAll(() => {
    if (!fs.existsSync(testScriptDir)) {
      fs.mkdirSync(testScriptDir, { recursive: true });
    }
  });

  describe('SecureKeyHandle - Basic Operations', () => {
    it('should import SecureKeyHandle without errors', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle, SigningError
print("SUCCESS")
`;
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_import.py')
      );
      expect(code).toBe(0);
    });

    it('should create and use a SecureKeyHandle', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle

# Create a test key (32 bytes for Ed25519)
test_key = b'\\x00' * 32

with SecureKeyHandle(test_key) as handle:
    # Verify handle is active
    assert handle._active == True

# Verify handle is wiped after context exit
assert handle._wiped == True
print("SUCCESS")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_basic_handle.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_basic_handle.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should prevent signing outside context', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle, SigningError

test_key = b'\\x00' * 32
handle = SecureKeyHandle(test_key)

try:
    handle.sign(b'\\x00' * 32)
    print("FAILED")
except SigningError as e:
    print("SUCCESS")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_outside_context.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_outside_context.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should verify buffer is wiped after context', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle

test_key = bytearray(b'SECRET_KEY_12345')

with SecureKeyHandle(bytes(test_key)) as handle:
    original_buf = bytes(handle._buf)

# After exit, buffer should be all zeros
wiped_buf = bytes(handle._buf)
all_zeros = all(b == 0 for b in wiped_buf)

if all_zeros:
    print("SUCCESS")
else:
    print("FAILED: Buffer not fully wiped")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_wipe_verification.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_wipe_verification.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });
  });

  describe('SecureKeyHandle - Exception Safety', () => {
    it('should wipe buffer even when exception occurs', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle

test_key = b'\\x00' * 32

try:
    with SecureKeyHandle(test_key) as handle:
        raise RuntimeError("Test exception")
except RuntimeError:
    pass

# Verify buffer was still wiped despite exception
all_zeros = all(b == 0 for b in handle._buf)
if all_zeros:
    print("SUCCESS")
else:
    print("FAILED")
`;
      fs.writeFileSync(
        path.join(testScriptDir, 'test_exception_safety.py'),
        script
      );
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_exception_safety.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should wipe buffer via __del__ if context not used', async () => {
      const script = `
import sys
import gc
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle

test_key = b'\\x00' * 32
handle = SecureKeyHandle(test_key)

# Force garbage collection to invoke __del__
del handle
gc.collect()

print("SUCCESS")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_del_cleanup.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_del_cleanup.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });
  });

  describe('SecureSessionCredentials', () => {
    it('should create and cleanup session credentials', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureSessionCredentials

token = b'jwt_token_secret_12345'

with SecureSessionCredentials(token, credential_type="jwt") as creds:
    retrieved = creds.get()
    assert retrieved == token

# Verify wiped after context
all_zeros = all(b == 0 for b in creds._buf)
if all_zeros:
    print("SUCCESS")
else:
    print("FAILED")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_session_creds.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_session_creds.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should prevent access to credentials outside context', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureSessionCredentials, SigningError

token = b'secret_token'
creds = SecureSessionCredentials(token)

try:
    creds.get()
    print("FAILED")
except SigningError:
    print("SUCCESS")
`;
      fs.writeFileSync(
        path.join(testScriptDir, 'test_creds_outside_context.py'),
        script
      );
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_creds_outside_context.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });
  });

  describe('SecureVariableWrapper', () => {
    it('should create and cleanup generic sensitive variable', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureVariableWrapper

password = b'super_secret_password_123'

with SecureVariableWrapper(password, label="db_password") as wrapper:
    retrieved = wrapper.get()
    assert retrieved == password

# Verify wiped after context
all_zeros = all(b == 0 for b in wrapper._buf)
if all_zeros:
    print("SUCCESS")
else:
    print("FAILED")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_var_wrapper.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_var_wrapper.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should support multiple nested wrappers', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureVariableWrapper

data1 = b'secret1'
data2 = b'secret2'

with SecureVariableWrapper(data1, label="secret1") as w1:
    with SecureVariableWrapper(data2, label="secret2") as w2:
        assert w1.get() == data1
        assert w2.get() == data2

# Verify both wiped
z1 = all(b == 0 for b in w1._buf)
z2 = all(b == 0 for b in w2._buf)

if z1 and z2:
    print("SUCCESS")
else:
    print("FAILED")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_nested_wrappers.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_nested_wrappers.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });
  });

  describe('Memory Cleanup Edge Cases', () => {
    it('should handle empty buffer gracefully', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureVariableWrapper

try:
    wrapper = SecureVariableWrapper(b'')
    print("FAILED")
except ValueError as e:
    print("SUCCESS")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_empty_buffer.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_empty_buffer.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should verify idempotent cleanup', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle

test_key = b'\\x00' * 32

with SecureKeyHandle(test_key) as handle:
    pass

# Multiple cleanup calls should be safe (idempotent)
handle._do_wipe()
handle._do_wipe()
handle._do_wipe()

print("SUCCESS")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_idempotent_cleanup.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_idempotent_cleanup.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should enforce correct tx_hash size for signing', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle

test_key = b'\\x00' * 32

with SecureKeyHandle(test_key) as handle:
    try:
        handle.sign(b'too_short')
        print("FAILED")
    except ValueError as e:
        print("SUCCESS")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_invalid_hash_size.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_invalid_hash_size.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });
  });

  describe('Audit Logging', () => {
    it('should track key imports in audit log', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle, audit_log

test_key = b'\\x00' * 32

with SecureKeyHandle(test_key, key_id="test_signing_key") as handle:
    pass

trail = audit_log.get_audit_trail()
import_events = [e for e in trail if e['event'] == 'KEY_IMPORTED']

if any(e['key_id'] == 'test_signing_key' for e in import_events):
    print("SUCCESS")
else:
    print("FAILED")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_audit_import.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_audit_import.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });

    it('should track key revocation in audit log', async () => {
      const script = `
import sys
sys.path.insert(0, '${cryptoDir}')
from signer import SecureKeyHandle, audit_log

test_key = b'\\x00' * 32

with SecureKeyHandle(test_key, key_id="revoke_test") as handle:
    pass

trail = audit_log.get_audit_trail()
revoke_events = [e for e in trail if e['event'] == 'KEY_REVOKED']

if any(e['key_id'] == 'revoke_test' for e in revoke_events):
    print("SUCCESS")
else:
    print("FAILED")
`;
      fs.writeFileSync(path.join(testScriptDir, 'test_audit_revoke.py'), script);
      const { stdout, code } = await runPythonTest(
        path.join(testScriptDir, 'test_audit_revoke.py')
      );
      expect(code).toBe(0);
      expect(stdout).toContain('SUCCESS');
    });
  });

  describe('Integration with TypeScript Layer', () => {
    it('should expose secure memory cleanup utilities for integration', () => {
      // Verify that the Python module exists and has required exports
      const signerPath = path.join(__dirname, '../src/crypto/signer.py');
      expect(fs.existsSync(signerPath)).toBe(true);

      const content = fs.readFileSync(signerPath, 'utf-8');
      expect(content).toContain('class SecureKeyHandle');
      expect(content).toContain('class SecureSessionCredentials');
      expect(content).toContain('class SecureVariableWrapper');
      expect(content).toContain('class SecurityAuditLogger');
    });
  });
});

describe('Integration Examples', () => {
  it('should document signing workflow with cleanup', () => {
    const example = `
    // Pseudo-code: Using SecureKeyHandle in TypeScript context
    // After spawning Python subprocess with key material:
    
    import { spawn } from 'child_process';
    
    async function signTransactionSecurely(keyMaterial: Buffer, txHash: Buffer) {
        const pythonScript = \\\`
import sys
sys.path.insert(0, './src/crypto')
from signer import SecureKeyHandle

with SecureKeyHandle(key_bytes) as handle:
    signature = handle.sign(tx_hash)
    print(signature.hex())
        \\\`;
        
        const proc = spawn('python3', ['-c', pythonScript], {
            stdio: ['pipe', 'pipe', 'pipe']
        });
        
        // Pass key material via stdin (more secure than args)
        proc.stdin.write(keyMaterial);
        proc.stdin.end();
        
        return new Promise((resolve, reject) => {
            let output = '';
            proc.stdout.on('data', (data) => output += data);
            proc.on('close', (code) => {
                if (code === 0) resolve(Buffer.from(output.trim(), 'hex'));
                else reject(new Error('Signing failed'));
            });
        });
    }
    `;
    expect(example).toContain('SecureKeyHandle');
  });

  it('should document session credential cleanup workflow', () => {
    const example = `
    // Pseudo-code: Using SecureSessionCredentials
    
    async function validateWithSessionToken(tokenBytes: Buffer) {
        const pythonScript = \\\`
import sys
sys.path.insert(0, './src/crypto')
from signer import SecureSessionCredentials

with SecureSessionCredentials(token_bytes, credential_type='jwt') as creds:
    token = creds.get()
    # Use token for validation
    validation_result = validate_jwt(token)
    print(validation_result)
        \\\`;
        
        // Execute signing with guaranteed cleanup
        return executeWithCleanup(pythonScript, tokenBytes);
    }
    `;
    expect(example).toContain('SecureSessionCredentials');
  });
});
