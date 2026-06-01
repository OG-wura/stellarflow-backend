import { Networks } from "@stellar/stellar-sdk";

const STELLAR_NETWORK_ENV = "STELLAR_NETWORK";
const STELLAR_NETWORK_PASSPHRASE_ENV = "STELLAR_NETWORK_PASSPHRASE";

export function getStellarNetwork(): "PUBLIC" | "TESTNET" {
  const explicitNetwork = process.env[STELLAR_NETWORK_ENV]?.trim().toUpperCase();
  if (explicitNetwork === "PUBLIC" || explicitNetwork === "TESTNET") {
    return explicitNetwork;
  }

  const explicitPassphrase = process.env[STELLAR_NETWORK_PASSPHRASE_ENV]?.trim();
  if (explicitPassphrase?.toUpperCase() === "PUBLIC") {
    return "PUBLIC";
  }
  if (explicitPassphrase?.toUpperCase() === "TESTNET") {
    return "TESTNET";
  }

  return "TESTNET";
}

export function getStellarNetworkPassphrase(): string {
  const providedPassphrase = process.env[STELLAR_NETWORK_PASSPHRASE_ENV]?.trim();
  if (providedPassphrase) {
    const normalized = providedPassphrase.toUpperCase();
    if (normalized === "PUBLIC") {
      return Networks.PUBLIC;
    }
    if (normalized === "TESTNET") {
      return Networks.TESTNET;
    }
    return providedPassphrase;
  }

  return getStellarNetwork() === "PUBLIC" ? Networks.PUBLIC : Networks.TESTNET;
}
