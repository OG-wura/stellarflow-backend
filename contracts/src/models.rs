/*!
 * stellarflow-contracts/src/models.rs
 * =====================================
 *
 * Persistent ledger tracking and submission-gap enforcement.
 *
 * A ``LedgerTracker`` contract stores, for every registered relayer, the
 * exact Soroban ledger sequence at which it last successfully submitted a
 * transaction payload. On every subsequent submission the contract compares
 * the current ledger against the stored value and **rejects** the call if the
 * network has not advanced by at least ``MIN_BLOCK_GAP`` (3) ledgers.
 *
 * Storage layout
 * --------------
 * * ``LEDGER_TRACKER`` -- ``Map<Address, u32>`` keyed by the relayer's
 *   Stellar/G-address; value is the last-accepted ledger sequence.
 */

use soroban_sdk::{contract, contractimpl, Address, Env, Map, Symbol};

const MIN_BLOCK_GAP: u32 = 3;

fn ledger_tracker_key(env: &Env) -> Symbol {
    Symbol::new(env, "ledger_tracker")
}

fn relayer_submitted() -> (Symbol, Symbol) {
    (Symbol::short("relayer"), Symbol::short("submitted"))
}

#[contract]
pub struct LedgerTracker;

#[contractimpl]
impl LedgerTracker {
    /// Record (or update) a relayer's submission.
    ///
    /// Returns the previous ledger sequence on success so callers can audit
    /// the elapsed gap, or ``None`` if this is the relayer's first call.
    ///
    /// # Panics
    ///
    /// Panics with ``gap_too_small`` if the current ledger has not advanced
    /// by at least ``MIN_BLOCK_GAP`` since the last recorded submission.
    pub fn submit(
        env: Env,
        relayer: Address,
        payload_hash: Vec<u8>,
    ) -> Option<u32> {
        relayer.require_auth();

        let current_ledger: u32 = env.ledger().sequence();

        let mut tracker: Map<Address, u32> = env
            .storage()
            .persistent()
            .get(&ledger_tracker_key(&env))
            .unwrap_or(Map::new(&env));

        let last = tracker.get(relayer.clone());

        if let Some(last_seq) = last {
            if current_ledger < last_seq + MIN_BLOCK_GAP {
                panic!("gap_too_small");
            }
        }

        tracker.set(relayer.clone(), current_ledger);
        env.storage()
            .persistent()
            .set(&ledger_tracker_key(&env), &tracker);

        env.events().publish(
            relayer_submitted(),
            (relayer, current_ledger, payload_hash),
        );

        last
    }

    /// Query the last recorded ledger for a relayer without modifying state.
    pub fn last_submission(env: Env, relayer: Address) -> Option<u32> {
        let tracker: Map<Address, u32> = env
            .storage()
            .persistent()
            .get(&ledger_tracker_key(&env))
            .unwrap_or(Map::new(&env));
        tracker.get(relayer)
    }

    /// Return the configured minimum gap this contract enforces.
    pub fn min_block_gap() -> u32 {
        MIN_BLOCK_GAP
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use soroban_sdk::{testutils::LedgerClient, Address as SorobanAddress};

    #[test]
    fn first_submission_succeeds() {
        let env = Env::default();
        env.mock_all_auths();
        let client = LedgerTrackerClient::new(&env, &env.register_contract(None, LedgerTracker {}));

        let relayer: SorobanAddress = soroban_sdk::Address::generate(&env);
        let hash = Vec::from_array(&env, [0u8; 32]);

        env.ledger().set_sequence(100);
        let result = client.submit(&relayer, &hash);
        assert_eq!(result, None);
        assert_eq!(client.last_submission(&relayer), Some(100));
    }

    #[test]
    fn submission_rejected_below_min_gap() {
        let env = Env::default();
        env.mock_all_auths();
        let client = LedgerTrackerClient::new(&env, &env.register_contract(None, LedgerTracker {}));

        let relayer: SorobanAddress = soroban_sdk::Address::generate(&env);
        let hash = Vec::from_array(&env, [1u8; 32]);

        env.ledger().set_sequence(100);
        client.submit(&relayer, &hash).unwrap();

        env.ledger().set_sequence(101);
        let result = client.try_submit(&relayer, &hash);
        assert!(result.is_err());
    }

    #[test]
    fn submission_accepted_when_gap_met() {
        let env = Env::default();
        env.mock_all_auths();
        let client = LedgerTrackerClient::new(&env, &env.register_contract(None, LedgerTracker {}));

        let relayer: SorobanAddress = soroban_sdk::Address::generate(&env);
        let hash = Vec::from_array(&env, [2u8; 32]);

        env.ledger().set_sequence(100);
        client.submit(&relayer, &hash).unwrap();

        env.ledger().set_sequence(103);
        client.submit(&relayer, &hash).unwrap();
        assert_eq!(client.last_submission(&relayer), Some(103));
    }
}
