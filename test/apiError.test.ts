import assert from "node:assert/strict";
import test from "node:test";
import {
  WIKI_BASE_URL,
  apiErrorPayload,
  buildHelpLink,
} from "../src/lib/apiError.ts";

test("buildHelpLink points at wiki Errors slug", () => {
  const link = buildHelpLink("MISSING_API_KEY");
  assert.equal(link, `${WIKI_BASE_URL}/Errors/MISSING_API_KEY`);
});

test("apiErrorPayload uses new schema { success: false, error: { code, message, timestamp } }", () => {
  const body = apiErrorPayload("VALIDATION_ERROR", "currency is required");
  assert.equal(body.success, false);
  assert.equal(body.error.code, "VALIDATION_ERROR");
  assert.equal(body.error.message, "currency is required");
  assert.ok(body.error.timestamp, "should include timestamp");
  assert.ok(new Date(body.error.timestamp) instanceof Date);
});

test("apiErrorPayload falls back to catalog message", () => {
  const body = apiErrorPayload("NOT_FOUND");
  assert.equal(body.error.message, "The requested resource was not found.");
});
