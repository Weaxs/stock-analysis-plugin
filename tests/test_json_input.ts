import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname } from "node:path";
import { withJsonInput } from "../scripts/json-input.mjs";

let captured = "";
const input = { text: "原文".repeat(100000), previous: { price: null } };
const result = await withJsonInput(input, async (path: string) => {
  captured = path;
  assert.deepEqual(JSON.parse(readFileSync(path, "utf8")), input);
  if (process.platform !== "win32") {
    assert.equal(statSync(path).mode & 0o777, 0o600);
    assert.equal(statSync(dirname(path)).mode & 0o777, 0o700);
  }
  return '{"ok":true}';
});
assert.equal(result, '{"ok":true}');
assert(!existsSync(dirname(captured)), "transport directory must be removed after success");

const failure = await withJsonInput(input, async (path: string) => {
  captured = path;
  throw new Error("private input must not escape");
});
assert.equal(typeof JSON.parse(failure).error, "string");
assert(!failure.includes("private input"));
assert(!existsSync(dirname(captured)), "transport directory must be removed after failure");
console.log("JSON input transport checks passed");
