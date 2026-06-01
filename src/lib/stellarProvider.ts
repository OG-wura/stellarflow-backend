import { Horizon, rpc as SorobanRpc } from "@stellar/stellar-sdk";
import dotenv from "dotenv";
import { logger } from "../utils/logger";
import { getStellarNetwork } from "./stellarNetwork";

dotenv.config();

/**
 * Whether an error from the Horizon SDK or RPC should trigger a failover to the next node.
 * Covers HTTP 5xx responses and common network-level errors.
 */
function isFailoverError(error: unknown): boolean {
  if (error && typeof error === "object") {
    const err = error as Record<string, any>;

    // HTTP 5xx from Horizon or RPC
    const httpStatus: unknown =
      err.response?.status ?? err.status ?? err.statusCode;
    if (typeof httpStatus === "number" && httpStatus >= 500) {
      return true;
    }

    // Network-level errors
    const networkCodes = new Set([
      "ECONNREFUSED",
      "ECONNRESET",
      "ETIMEDOUT",
      "ECONNABORTED",
      "ENETUNREACH",
      "EHOSTUNREACH",
    ]);
    if (typeof err.code === "string" && networkCodes.has(err.code)) {
      return true;
    }

    // SDK timeout messages or RPC errors indicating connection issues
    if (typeof err.message === "string") {
      const msg = err.message.toLowerCase();
      if (
        msg.includes("timeout") ||
        msg.includes("network error") ||
        msg.includes("econnrefused") ||
        msg.includes("fetch failed")
      ) {
        return true;
      }
    }
  }

  return false;
}

/**
 * Builds the ordered list of fallback Horizon URLs for a given network.
 */
function buildHorizonUrls(network: string): string[] {
  const isMainnet = network === "PUBLIC";

  const sdfUrl = isMainnet
    ? "https://horizon.stellar.org"
    : "https://horizon-testnet.stellar.org";

  const publicNodeUrl = isMainnet
    ? "https://horizon.publicnode.org"
    : "https://horizon-testnet.publicnode.org";

  const urls: string[] = [];

  const customUrl = process.env.HORIZON_URL?.trim();
  if (customUrl) {
    urls.push(customUrl);
  }

  urls.push(sdfUrl, publicNodeUrl);

  return urls;
}

/**
 * Builds the ordered list of fallback RPC URLs for a given network.
 */
function buildRpcUrls(network: string): string[] {
  const isMainnet = network === "PUBLIC";

  const sdfUrl = isMainnet
    ? "https://rpc.mainnet.stellar.org"
    : "https://rpc.testnet.stellar.org";

  const urls: string[] = [];

  const customUrl = process.env.RPC_URL?.trim();
  if (customUrl) {
    urls.push(customUrl);
  }

  // Load configurable fallback RPC URLs (comma-separated)
  const customFallbacks = process.env.FALLBACK_RPC_URLS?.trim();
  if (customFallbacks) {
    urls.push(
      ...customFallbacks
        .split(",")
        .map((u) => u.trim())
        .filter(Boolean)
    );
  }

  // Ensure default SDF node is in the list
  if (!urls.includes(sdfUrl)) {
    urls.push(sdfUrl);
  }

  return urls;
}

/**
 * StellarProvider — singleton that manages a pool of Horizon and RPC servers with
 * automatic failover.
 */
class StellarProvider {
  private readonly network: string;
  
  // Horizon properties
  private readonly urls: readonly string[];
  private currentIndex: number = 0;
  private server: Horizon.Server;

  // RPC properties
  private readonly rpcUrls: readonly string[];
  private rpcCurrentIndex: number = 0;
  private rpcServer: SorobanRpc.Server;

  constructor() {
    this.network = getStellarNetwork();

    // Initialize Horizon
    this.urls = buildHorizonUrls(this.network);
    this.server = new Horizon.Server(this.urls[0]!);
    logger.info(
      `[StellarProvider] Initialized Horizon with ${this.urls.length} node(s). Primary: ${this.urls[0]!}`,
    );

    // Initialize RPC
    this.rpcUrls = buildRpcUrls(this.network);
    this.rpcServer = new SorobanRpc.Server(this.rpcUrls[0]!, {
      allowHttp: this.network === "TESTNET",
    });
    logger.info(
      `[StellarProvider] Initialized RPC with ${this.rpcUrls.length} node(s). Primary: ${this.rpcUrls[0]!}`,
    );
  }

  // ==========================================
  // Horizon methods
  // ==========================================
  getServer(): Horizon.Server {
    return this.server;
  }

  getCurrentUrl(): string {
    return this.urls[this.currentIndex]!;
  }

  reportFailure(error: unknown): boolean {
    if (!isFailoverError(error)) {
      return false;
    }

    const failedUrl = this.urls[this.currentIndex]!;
    const nextIndex = (this.currentIndex + 1) % this.urls.length;

    if (nextIndex === this.currentIndex) {
      logger.networkError(
        `[StellarProvider] Horizon Node ${failedUrl} failed and no fallback is available.`,
      );
      return false;
    }

    this.currentIndex = nextIndex;
    this.server = new Horizon.Server(this.urls[this.currentIndex]!);

    logger.warn(
      `[StellarProvider] ⚠️ Horizon Node "${failedUrl}" returned an error. ` +
        `Failing over to "${this.urls[this.currentIndex]!}" ` +
        `(node ${this.currentIndex + 1}/${this.urls.length}).`,
      { isNetwork: true }
    );

    return true;
  }

  // ==========================================
  // RPC methods
  // ==========================================
  getRpcServer(): SorobanRpc.Server {
    return this.rpcServer;
  }

  getCurrentRpcUrl(): string {
    return this.rpcUrls[this.rpcCurrentIndex]!;
  }

  reportRpcFailure(error: unknown): boolean {
    if (!isFailoverError(error)) {
      return false;
    }

    const failedUrl = this.rpcUrls[this.rpcCurrentIndex]!;
    const nextIndex = (this.rpcCurrentIndex + 1) % this.rpcUrls.length;

    if (nextIndex === this.rpcCurrentIndex) {
      logger.networkError(
        `[StellarProvider] RPC Node ${failedUrl} failed and no fallback is available.`,
      );
      return false;
    }

    this.rpcCurrentIndex = nextIndex;
    this.rpcServer = new SorobanRpc.Server(this.rpcUrls[this.rpcCurrentIndex]!, {
      allowHttp: this.network === "TESTNET",
    });

    logger.warn(
      `[StellarProvider] ⚠️ RPC Node "${failedUrl}" returned an error. ` +
        `Failing over to "${this.rpcUrls[this.rpcCurrentIndex]!}" ` +
        `(node ${this.rpcCurrentIndex + 1}/${this.rpcUrls.length}).`,
      { isNetwork: true }
    );

    return true;
  }
}

const stellarProvider = new StellarProvider();
export default stellarProvider;
