import { Mutex } from "async-mutex";
import stellarProvider from "../lib/stellarProvider";

/**
 * SequenceManager
 *
 * Synchronizes Stellar sequence numbers per source account to prevent tx_bad_seq
 * collisions when multiple backend workers submit transactions concurrently.
 */
export class SequenceManager {
  private static instance: SequenceManager;

  private readonly mutexes = new Map<string, Mutex>();
  private readonly currentSequences = new Map<string, bigint>();

  private constructor() {}

  public static getInstance(): SequenceManager {
    if (!SequenceManager.instance) {
      SequenceManager.instance = new SequenceManager();
    }

    return SequenceManager.instance;
  }

  private getMutex(address: string): Mutex {
    let mutex = this.mutexes.get(address);

    if (!mutex) {
      mutex = new Mutex();
      this.mutexes.set(address, mutex);
    }

    return mutex;
  }

  /**
   * Fetch the true account sequence from Horizon and update the local cache.
   *
   * This is used on startup/cache miss and after tx_bad_seq conflicts so the
   * local queue realigns with the ledger's absolute state.
   */
  public async syncSequence(address: string): Promise<string> {
    const mutex = this.getMutex(address);

    return mutex.runExclusive(async () => {
      console.info(
        `[SequenceManager] Syncing sequence from ledger for ${address}...`,
      );

      const account = await stellarProvider.getServer().loadAccount(address);
      const ledgerSequence = BigInt(account.sequenceNumber());

      this.currentSequences.set(address, ledgerSequence);

      return ledgerSequence.toString();
    });
  }

  /**
   * Return the next sequence number for the given account.
   *
   * If the account has no cached sequence, this fetches the current ledger
   * sequence. Otherwise it increments the local per-account sequence linearly.
   */
  public async getNextSequence(address: string): Promise<string> {
    const mutex = this.getMutex(address);

    return mutex.runExclusive(async () => {
      try {
        const cachedSequence = this.currentSequences.get(address);

        if (cachedSequence === undefined) {
          console.info(
            `[SequenceManager] Fetching sequence from Horizon for ${address}...`,
          );

          const account = await stellarProvider.getServer().loadAccount(address);
          const ledgerSequence = BigInt(account.sequenceNumber());

          this.currentSequences.set(address, ledgerSequence);
          return ledgerSequence.toString();
        }

        const nextSequence = cachedSequence + 1n;
        this.currentSequences.set(address, nextSequence);

        return nextSequence.toString();
      } catch (error) {
        this.currentSequences.delete(address);
        throw error;
      }
    });
  }

  /**
   * Invalidate the cached sequence for one account, or all accounts if no
   * account is provided.
   */
  public invalidate(address?: string): void {
    if (address) {
      this.currentSequences.delete(address);
      console.info(
        `[SequenceManager] Sequence invalidated for ${address}. Next call will sync from Horizon.`,
      );
      return;
    }

    this.currentSequences.clear();
    console.info(
      "[SequenceManager] All cached sequences invalidated. Next calls will sync from Horizon.",
    );
  }
}

export const sequenceManager = SequenceManager.getInstance();
