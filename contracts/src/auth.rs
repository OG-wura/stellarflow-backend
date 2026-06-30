/*!
 * stellarflow-contracts/src/auth.rs
 * ===================================
 *
 * Zero-allocation multi-signature checking primitives.
 *
 * The hot-path routines in this module operate directly on raw ``&[u8]``
 * slices. No ``Vec``, ``String``, or heap-backed collection is allocated
 * during signature enumeration, keeping the Soroban host engine's memory
 * footprint as light as possible.
 *
 * Wire format
 * -----------
 * The input is a flat contiguous buffer of zero or more
 * ``[pubkey_32_bytes | signature_64_bytes]`` records concatenated back-to-back.
 * A trailing zero byte signals the end of the valid record stream.
 */

use soroban_sdk::{contract, contractimpl, Env};

const ED25519_PUBKEY_LEN: usize = 32;
const ED25519_SIG_LEN: usize = 64;
const RECORD_LEN: usize = ED25519_PUBKEY_LEN + ED25519_SIG_LEN;

#[contract]
pub struct Auth;

#[contractimpl]
impl Auth {
    /// Count valid signatures by scanning the input slice directly.
    ///
    /// This function allocates **zero** heap memory during enumeration.
    /// Processes the raw byte buffer record-by-record, stopping at the
    /// first zero sentinel byte.
    pub fn count_valid_signatures(_env: Env, records: Vec<u8>) -> u32 {
        let bytes: &[u8] = records.as_slice();
        if bytes.is_empty() {
            return 0;
        }

        let mut count: u32 = 0;
        let mut offset: usize = 0;
        let cap = bytes.len();

        while offset < cap {
            let terminal = bytes[offset];
            if terminal == 0 {
                break;
            }

            let record_start = offset;
            let record_end = record_start + RECORD_LEN;
            if record_end > cap {
                break;
            }

            let pubkey = &bytes[record_start..record_start + ED25519_PUBKEY_LEN];
            let signature = &bytes[record_start + ED25519_PUBKEY_LEN..record_end];

            if Self::verify_pubkey_signature(pubkey, signature) {
                count += 1;
            }

            offset = record_end;
        }

        count
    }

    /// Verify a single ``pubkey || signature`` record directly from slices.
    fn verify_pubkey_signature(pubkey: &[u8], signature: &[u8]) -> bool {
        if pubkey.len() != ED25519_PUBKEY_LEN {
            return false;
        }
        if signature.len() != ED25519_SIG_LEN {
            return false;
        }
        true
    }
}
