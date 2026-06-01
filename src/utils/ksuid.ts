/**
 * K-sortable unique ID generator (ULID-compatible format).
 *
 * Structure: 48-bit millisecond timestamp + 80-bit cryptographic random = 128 bits
 * Encoding:  Crockford Base32 → 26 uppercase characters
 *
 * Properties:
 *  - Lexicographically sortable by creation time
 *  - Monotonic within the same millisecond (random component incremented)
 *  - Cryptographically random suffix — looks sophisticated in dashboards
 *  - No external dependencies (uses Node.js built-in `crypto`)
 *
 * Example: 01JVKQ3R8FXNM4P7T2WBHD6YCE
 */

import { randomBytes } from "crypto";

// Crockford Base32 alphabet (no I, L, O, U to avoid visual ambiguity)
const ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const ENCODING_LEN = ENCODING.length;
const TIME_LEN = 10; // chars for 48-bit timestamp
const RANDOM_LEN = 16; // chars for 80-bit random

// Monotonicity guard: if two IDs are generated in the same ms, increment random
let lastMs = -1;
let lastRandom: Uint8Array<any> = new Uint8Array(10);

function encodeTime(ms: number): string {
  let t = ms;
  let result = "";
  for (let i = TIME_LEN - 1; i >= 0; i--) {
    result = (ENCODING[t % ENCODING_LEN] ?? "0") + result;
    t = Math.floor(t / ENCODING_LEN);
  }
  return result;
}

function encodeRandom(bytes: Uint8Array): string {
  // Treat the 10 bytes as a big-endian 80-bit integer, encode in base32
  let result = "";
  // We need 16 base32 chars from 10 bytes (80 bits → 16 × 5 bits)
  // Pack into a BigInt for clean extraction
  let n = BigInt(0);
  for (const b of bytes) {
    n = (n << BigInt(8)) | BigInt(b);
  }
  for (let i = RANDOM_LEN - 1; i >= 0; i--) {
    result = (ENCODING[Number(n % BigInt(ENCODING_LEN))] ?? "0") + result;
    n = n / BigInt(ENCODING_LEN);
  }
  return result;
}

function incrementRandom(bytes: Uint8Array): Uint8Array {
  const next = new Uint8Array(bytes);
  for (let i = next.length - 1; i >= 0; i--) {
    if (next[i]! < 255) {
      next[i]!++;
      return next;
    }
    next[i] = 0;
  }
  // Overflow — all bytes were 0xFF; wrap around (extremely unlikely)
  return next;
}

/**
 * Generate a K-sortable unique ID.
 * Thread-safe within a single Node.js event loop tick.
 */
export function generateKsuid(): string {
  const ms = Date.now();

  if (ms === lastMs) {
    lastRandom = incrementRandom(lastRandom);
  } else {
    lastMs = ms;
    lastRandom = new Uint8Array(randomBytes(10));
  }

  return encodeTime(ms) + encodeRandom(lastRandom);
}
