import fs from "fs";
import path from "path";
import { dbSandbox } from "../security/sandbox";

export interface RateLimitConfig {
  /** Rolling window duration in milliseconds (default: 900_000 = 15 min) */
  windowMs: number;
  /** Maximum requests per IP per window (default: 100) */
  maxRequests: number;
  /** Whether global rate limiting is active (default: true) */
  enabled: boolean;
}

export interface SandboxConfig {
  /** Whether subprocess sandboxing is enabled (default: true) */
  enabled: boolean;
  /** Maximum execution time in milliseconds (default: 30000 = 30 sec) */
  timeoutMs: number;
  /** Maximum memory in MB (default: 512) */
  maxMemoryMb: number;
  /** Whether to allow network access (default: true) */
  allowNetwork: boolean;
  /** Whether to allow file system writes (default: true) */
  allowFileWrites: boolean;
}

export interface AppConfig {
  fetchIntervalMs: number;
  sorobanPollIntervalMs: number;
  multiSigPollIntervalMs: number;
  hourlyAverageCheckIntervalMs: number;
  cacheDurationMs: number;
  batchWindowMs: number;
  rateLimit: RateLimitConfig;
  sandbox: SandboxConfig;
}

export const CONFIG_PATH = path.resolve(process.cwd(), "config.json");

const DEFAULTS: AppConfig = {
  fetchIntervalMs: 10000,
  sorobanPollIntervalMs: 15000,
  multiSigPollIntervalMs: 30000,
  hourlyAverageCheckIntervalMs: 900000,
  cacheDurationMs: 30000,
  batchWindowMs: 5000,
  rateLimit: {
    windowMs: 900000,
    maxRequests: 100,
    enabled: true,
  },
  sandbox: {
    enabled: true,
    timeoutMs: 30000,
    maxMemoryMb: 512,
    allowNetwork: true,
    allowFileWrites: true,
  },
};

function deepFreeze<T extends object>(obj: T): Readonly<T> {
  for (const value of Object.values(obj)) {
    if (
      value !== null &&
      typeof value === "object" &&
      !Object.isFrozen(value)
    ) {
      deepFreeze(value as object);
    }
  }
  return Object.freeze(obj);
}

function buildConfig(parsed: Partial<AppConfig>): Readonly<AppConfig> {
  return deepFreeze({
    ...DEFAULTS,
    ...parsed,
    rateLimit: { ...DEFAULTS.rateLimit, ...(parsed.rateLimit ?? {}) },
    sandbox: { ...DEFAULTS.sandbox, ...(parsed.sandbox ?? {}) },
  });
}

function loadConfig(): Readonly<AppConfig> {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, "utf-8");
    return buildConfig(JSON.parse(raw) as Partial<AppConfig>);
  } catch {
    return buildConfig({});
  }
}

// Internal mutable reference — replaced atomically on reload; never mutated in place
let _appConfig: Readonly<AppConfig> = loadConfig();

/** Returns the current frozen application configuration snapshot. */
export function getAppConfig(): Readonly<AppConfig> {
  return _appConfig;
}

/**
 * @deprecated Use {@link getAppConfig} instead.
 * Kept for backward compatibility — reads from the same internal reference.
 */
export const appConfig: Readonly<AppConfig> = new Proxy(
  {} as Readonly<AppConfig>,
  {
    get(_target, prop) {
      return (_appConfig as Record<string | symbol, unknown>)[prop];
    },
  },
);

/**
 * Starts a fs.watch watcher on config.json.
 * On change, builds a new frozen config and replaces the internal reference atomically.
 * Calls the optional `onChange` callback with the updated config after each reload.
 * Returns a cleanup function that stops the watcher.
 */
export function watchConfig(
  onChange?: (config: Readonly<AppConfig>) => void,
): () => void {
  if (!fs.existsSync(CONFIG_PATH)) {
    console.warn(
      `[ConfigWatcher] config.json not found at ${CONFIG_PATH}. Hot-reload disabled.`,
    );
    return () => {};
  }

  const watcher = fs.watch(CONFIG_PATH, (event: string) => {
    if (event !== "change") return;
    try {
      const raw = fs.readFileSync(CONFIG_PATH, "utf-8");
      const updated = buildConfig(JSON.parse(raw) as Partial<AppConfig>);
      _appConfig = updated;
      if (updated.sandbox) {
        try {
          dbSandbox.syncWithConfig(updated);
        } catch (err) {
          console.error("[ConfigWatcher] Failed to sync sandbox policy:", err);
        }
      }
      console.info("[ConfigWatcher] config.json reloaded:", updated);
      onChange?.(updated);
    } catch (err) {
      console.error("[ConfigWatcher] Failed to reload config.json:", err);
    }
  });

  console.info(`[ConfigWatcher] Watching ${CONFIG_PATH} for changes`);
  return () => watcher.close();
}
