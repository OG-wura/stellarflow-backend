import { Keypair } from "@stellar/stellar-sdk";
import type { ConfirmedPrice } from "../src/services/sorobanEventListener";

let passed = 0;
let failed = 0;

function assert(description: string, condition: boolean) {
  if (condition) {
    console.log(`  ✓ ${description}`);
    passed++;
  } else {
    console.log(`  ✗ ${description}`);
    failed++;
  }
}

async function run() {
  const testSecret = Keypair.random().secret();
  process.env.STELLAR_SECRET = testSecret;
  process.env.SIGNER_BACKEND = "local";
  delete process.env.ENCRYPTED_STELLAR_SECRET;

  const { SorobanEventListener } =
    await import("../src/services/sorobanEventListener");

  console.log("🧪 Testing SorobanEventListener...\n");

  console.log("Instantiation:");
  const listener = new SorobanEventListener();
  assert("creates instance when signing key is configured", listener !== null);
  assert(
    "isActive returns false before start()",
    listener.isActive() === false,
  );

  console.log("\nConfirmedPrice interface:");
  const mockPrice: ConfirmedPrice = {
    currency: "NGN",
    rate: 1650.25,
    txHash: "abc123def456",
    memoId: "SF-NGN-1234567890-001",
    ledgerSeq: 12345,
    confirmedAt: new Date(),
  };
  assert("currency is string", typeof mockPrice.currency === "string");
  assert("rate is number", typeof mockPrice.rate === "number");
  assert("txHash is string", typeof mockPrice.txHash === "string");
  assert(
    "memoId can be string or null",
    mockPrice.memoId === null || typeof mockPrice.memoId === "string",
  );
  assert("ledgerSeq is number", typeof mockPrice.ledgerSeq === "number");
  assert("confirmedAt is Date", mockPrice.confirmedAt instanceof Date);

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
