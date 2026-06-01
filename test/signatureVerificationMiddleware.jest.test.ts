import { normalizeHexString } from "../src/middleware/signatureVerificationMiddleware";

describe("normalizeHexString", () => {
  it("removes whitespace and hidden characters from hex strings", () => {
    const raw = "  ab cd\n12\r34\t56\u200B78\u200C9a\u200Dbc\uFEFF";
    expect(normalizeHexString(raw)).toBe("abcd123456789abc");
  });

  it("does not modify valid hex strings", () => {
    const raw = "a1b2c3d4e5f6";
    expect(normalizeHexString(raw)).toBe(raw);
  });
});
