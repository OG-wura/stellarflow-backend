export interface ConfirmedPrice {
    currency: string;
    rate: number;
    txHash: string;
    memoId: string | null;
    ledgerSeq: number;
    confirmedAt: Date;
}
export declare class SorobanEventListener {
    private bpManager;
    private server;
    private oraclePublicKey;
    private isRunning;
    private pollIntervalMs;
    private lastProcessedLedger;
    private pollTimer;
    constructor(pollIntervalMs?: number);
    start(): Promise<void>;
    /**
     * Worker loop that processes packets from the queue at a controlled pace.
     */
    private startWorker;
    private pollTransactions;
    private extractMemoId;
    private parseOperations;
    stop(): void;
    isActive(): boolean;
}
//# sourceMappingURL=sorobanEventListener.d.ts.map