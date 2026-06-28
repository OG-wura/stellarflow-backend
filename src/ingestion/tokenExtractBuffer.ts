/**
 * TokenExtractBuffer — memory-efficient line-by-line token extraction for the
 * ingestion pipeline (issue #470).
 *
 * Feeds raw binary/string chunks into an internal tail buffer and emits one
 * string[] of tokens per complete newline-delimited line without ever holding
 * the full stream in memory simultaneously.
 *
 * Design goals:
 * - O(L) memory where L is the length of the longest single line (not the
 *   total stream).
 * - Uses Node.js Buffer throughout — no TextEncoder/TextDecoder, no full-stream
 *   copies, minimum intermediate allocations.
 * - Configurable token delimiter (default: whitespace — space 0x20, tab 0x09).
 */

const NEWLINE = 0x0a; // '\n'
const CARRIAGE_RETURN = 0x0d; // '\r'

export interface TokenExtractOptions {
  /** Byte values that separate tokens within a line.  Default: space + tab. */
  tokenDelimiters?: ReadonlyArray<number>;
  /** Maximum bytes held in the incomplete-line tail before flushing as-is. */
  maxLineBytes?: number;
  /** If true, empty token strings (from adjacent delimiters) are omitted. */
  skipEmpty?: boolean;
}

/**
 * Incremental line-by-line token extraction buffer.
 *
 * @example
 * ```ts
 * const buf = new TokenExtractBuffer();
 * for (const tokens of buf.feed(Buffer.from("foo bar\nbaz qux\n"))) {
 *   console.log(tokens); // ["foo", "bar"] then ["baz", "qux"]
 * }
 * ```
 */
export class TokenExtractBuffer {
  private _tail: Buffer = Buffer.alloc(0);
  private readonly _delimiters: ReadonlyArray<number>;
  private readonly _maxLineBytes: number;
  private readonly _skipEmpty: boolean;

  constructor(options: TokenExtractOptions = {}) {
    this._delimiters = options.tokenDelimiters ?? [0x20, 0x09]; // space, tab
    this._maxLineBytes = options.maxLineBytes ?? 65_536; // 64 KiB safety cap
    this._skipEmpty = options.skipEmpty ?? true;
  }

  /**
   * Ingest a chunk of data and yield tokens for every complete line found.
   *
   * Partial lines are retained in an internal tail buffer and merged with the
   * next chunk, ensuring no cross-chunk token splits.
   *
   * Time:  O(n) where n = tail.length + chunk.length.
   * Space: O(L) where L = length of the longest single line in the stream.
   */
  *feed(chunk: Buffer | Uint8Array | string): Generator<string[], void, void> {
    const incoming: Buffer =
      typeof chunk === "string"
        ? Buffer.from(chunk, "utf8")
        : Buffer.isBuffer(chunk)
          ? chunk
          : Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength);

    // Merge tail + incoming into one contiguous buffer.
    const data: Buffer =
      this._tail.length === 0
        ? incoming
        : Buffer.concat([this._tail, incoming]);
    this._tail = Buffer.alloc(0);

    let lineStart = 0;

    for (let i = 0; i < data.length; i++) {
      if (data[i] === NEWLINE) {
        // Exclude a preceding '\r' from the line content (CRLF support).
        const lineEnd =
          i > lineStart && data[i - 1] === CARRIAGE_RETURN ? i - 1 : i;

        if (lineEnd > lineStart) {
          const tokens = this._tokenize(data, lineStart, lineEnd);
          if (tokens.length > 0) yield tokens;
        }

        lineStart = i + 1;
      }

      // Safety cap: flush oversized partial line to avoid unbounded growth.
      if (i - lineStart >= this._maxLineBytes) {
        const tokens = this._tokenize(data, lineStart, i + 1);
        if (tokens.length > 0) yield tokens;
        lineStart = i + 1;
      }
    }

    // Keep the incomplete trailing fragment for the next feed() call.
    if (lineStart < data.length) {
      this._tail = data.subarray(lineStart);
    }
  }

  /**
   * Flush any buffered incomplete line and reset the internal state.
   * Returns tokens from the remaining tail if it contains non-delimiter bytes.
   */
  *flush(): Generator<string[], void, void> {
    if (this._tail.length > 0) {
      const tokens = this._tokenize(this._tail, 0, this._tail.length);
      if (tokens.length > 0) yield tokens;
    }
    this._tail = Buffer.alloc(0);
  }

  /** Number of bytes currently held in the incomplete-line tail. */
  get pendingBytes(): number {
    return this._tail.length;
  }

  // ── private ────────────────────────────────────────────────────────────────

  /** Split a byte range within `buf` into UTF-8 string tokens. */
  private _tokenize(buf: Buffer, start: number, end: number): string[] {
    const tokens: string[] = [];
    let tokenStart = -1;

    for (let i = start; i <= end; i++) {
      const isDelim = i === end || this._delimiters.includes(buf[i] as number);

      if (!isDelim && tokenStart === -1) {
        tokenStart = i;
      } else if (isDelim && tokenStart !== -1) {
        const token = buf.toString("utf8", tokenStart, i);
        if (!this._skipEmpty || token.length > 0) tokens.push(token);
        tokenStart = -1;
      }
    }

    return tokens;
  }
}

export default TokenExtractBuffer;
