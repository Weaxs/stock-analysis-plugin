import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = mkdtempSync(join(tmpdir(), "stock-analysis-setup-"));
try {
  mkdirSync(join(root, "scripts"));
  mkdirSync(join(root, "tools"));
  cpSync(new URL("../scripts/setup-python.mjs", import.meta.url), join(root, "scripts/setup-python.mjs"));
  cpSync(new URL("../scripts/venv-python.mjs", import.meta.url), join(root, "scripts/venv-python.mjs"));
  writeFileSync(join(root, "tools/requirements.txt"), "invalid requirement !!!\n");
  const result = spawnSync(process.execPath, [join(root, "scripts/setup-python.mjs")], {
    encoding: "utf8",
  });
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /Python setup failed/);
} finally {
  rmSync(root, { recursive: true, force: true });
}
