/**
 * Tests for TokenExtractBuffer (issue #470)
 * Memory-efficient line-by-line token extraction for ingestion throughput.
 */

import { TokenExtractBuffer } from "../src/ingestion/tokenExtractBuffer.js";

let passed = 0;
let failed = 0;

function assert(description: string, condition: boolean): void {
  if (condition) {
    console.log(`  ✓ ${description}`);
    passed++;
  } else {
    console.error(`  ✗ ${description}`);
    failed++;
  }
}

function collectFeed(
  buf: TokenExtractBuffer,
  input: string | Uint8Array,
): string[][] {
  return [...buf.feed(input)];
}

console.log("🧪 Testing TokenExtractBuffer (issue #470)...\n");

// ── Basic line splitting ──────────────────────────────────────────────────────
console.log("📌 Basic line splitting");

{
  const buf = new TokenExtractBuffer();
  const result = collectFeed(buf, "foo bar\nbaz qux\n");
  assert("emits 2 token arrays", result.length === 2);
  assert("first line tokens", JSON.stringify(result[0]) === '["foo","bar"]');
  assert("second line tokens", JSON.stringify(result[1]) === '["baz","qux"]');
}

// ── CRLF line endings ────────────────────────────────────────────────────────
console.log("\n📌 CRLF line endings");

{
  const buf = new TokenExtractBuffer();
  const result = collectFeed(buf, "hello world\r\nfoo bar\r\n");
  assert("handles CRLF — 2 lines", result.length === 2);
  assert(
    "CRLF: first tokens",
    JSON.stringify(result[0]) === '["hello","world"]',
  );
  assert("CRLF: second tokens", JSON.stringify(result[1]) === '["foo","bar"]');
}

// ── Partial / cross-chunk lines ───────────────────────────────────────────────
console.log("\n📌 Cross-chunk partial lines");

{
  const buf = new TokenExtractBuffer();
  const r1 = collectFeed(buf, "tok");
  assert("no line emitted on partial chunk", r1.length === 0);
  assert("tail is buffered", buf.pendingBytes === 3);

  const r2 = collectFeed(buf, "en_a token_b\n");
  assert("line emitted after newline", r2.length === 1);
  assert(
    "tokens joined correctly across chunks",
    JSON.stringify(r2[0]) === '["token_a","token_b"]',
  );
  assert("tail cleared", buf.pendingBytes === 0);
}

// ── flush() emits incomplete final line ───────────────────────────────────────
console.log("\n📌 flush() — incomplete trailing line");

{
  const buf = new TokenExtractBuffer();
  collectFeed(buf, "line one\n");
  collectFeed(buf, "no newline here");
  assert("tail pending before flush", buf.pendingBytes > 0);

  const flushed = [...buf.flush()];
  assert("flush emits trailing tokens", flushed.length === 1);
  assert(
    "flush tokens correct",
    JSON.stringify(flushed[0]) === '["no","newline","here"]',
  );
  assert("tail cleared after flush", buf.pendingBytes === 0);
}

// ── Blank lines and extra whitespace ─────────────────────────────────────────
console.log("\n📌 Blank lines and adjacent delimiters");

{
  const buf = new TokenExtractBuffer();
  const result = collectFeed(buf, "\n  spaced  out  \n\n");
  // blank lines yield no tokens → skipEmpty omits them
  assert("blank lines produce no token arrays", result.length === 1);
  assert(
    "extra spaces stripped",
    JSON.stringify(result[0]) === '["spaced","out"]',
  );
}

// ── Custom delimiters ─────────────────────────────────────────────────────────
console.log("\n📌 Custom token delimiters (comma)");

{
  const buf = new TokenExtractBuffer({ tokenDelimiters: [0x2c] }); // ','
  const result = collectFeed(buf, "a,b,c\n1,2,3\n");
  assert("comma delimiter — 2 lines", result.length === 2);
  assert(
    "comma delimiter — first line",
    JSON.stringify(result[0]) === '["a","b","c"]',
  );
  assert(
    "comma delimiter — second line",
    JSON.stringify(result[1]) === '["1","2","3"]',
  );
}

// ── Binary (Uint8Array) input ─────────────────────────────────────────────────
console.log("\n📌 Uint8Array input");

{
  const buf = new TokenExtractBuffer();
  const input = Buffer.from("alpha beta\ngamma\n", "utf8");
  const result = collectFeed(buf, input);
  assert("binary input — 2 lines", result.length === 2);
  assert(
    "binary input — line 1",
    JSON.stringify(result[0]) === '["alpha","beta"]',
  );
  assert("binary input — line 2", JSON.stringify(result[1]) === '["gamma"]');
}

// ── Memory: only O(L) tail retained between feeds ────────────────────────────
console.log("\n📌 Memory efficiency — O(L) tail");

{
  const buf = new TokenExtractBuffer();
  // Feed 100 complete lines; tail should stay near zero after each feed.
  let totalLines = 0;
  for (let i = 0; i < 100; i++) {
    for (const _ of buf.feed(`token_${i}_a token_${i}_b\n`)) totalLines++;
  }
  assert("100 lines processed correctly", totalLines === 100);
  assert("tail is zero after complete lines", buf.pendingBytes === 0);
}

// ── Safety cap: oversized line is flushed mid-way ────────────────────────────
console.log("\n📌 Safety cap for oversized lines");

{
  const buf = new TokenExtractBuffer({ maxLineBytes: 16 });
  // 20-char token sequence with no newline — should flush at 16-byte mark.
  const oversized = "01234567890123456789";
  const result = collectFeed(buf, oversized);
  // At least one partial flush must have occurred.
  assert("oversized line gets flushed", result.length >= 1);
}

// ── Summary ───────────────────────────────────────────────────────────────────
console.log(`\n${"=".repeat(60)}`);
console.log(`${passed} passed, ${failed} failed`);

if (failed > 0) process.exit(1);
