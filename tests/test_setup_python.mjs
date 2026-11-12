import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = mkdtempSync(join(tmpdir(), "stock-analysis-setup-$STOCK_SETUP_LITERAL-"));
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
  assert(existsSync(join(root, ".venv")), "venv must be created under the literal package path");
  writeFileSync(join(root, "openclaw.plugin.json"), "{}");
  const upgrade = spawnSync(process.execPath, [join(root, "scripts/setup-python.mjs")], { encoding: "utf8" });
  assert.equal(upgrade.status, 1, upgrade.stderr);
  assert(!existsSync(join(root, ".venv")), "OpenClaw must not capture the Python environment as plugin source");
  const runtime = join(root, "node_modules", ".stock-analysis-python", ".venv");
  assert(existsSync(runtime), "existing environment must be preserved under node_modules");
  const python = join(runtime, process.platform === "win32" ? "Scripts/python.exe" : "bin/python3");
  assert.equal(spawnSync(python, ["-c", "import sys; assert sys.prefix != sys.base_prefix"]).status, 0);
  rmSync(runtime, { recursive: true, force: true });
  mkdirSync(runtime, { recursive: true });
  const repair = spawnSync(process.execPath, [join(root, "scripts/setup-python.mjs")], { encoding: "utf8" });
  assert.equal(repair.status, 1, repair.stderr);
  assert(existsSync(python), "a half-built environment without pip must be rebuilt");
  writeFileSync(join(root, "tools/requirements.txt"), "# optional providers\ninvalid requirement !!!\n");
  const optional = spawnSync(process.execPath, [join(root, "scripts/setup-python.mjs")], { encoding: "utf8" });
  assert.equal(optional.status, 0, optional.stderr);
  assert.match(optional.stderr, /optional providers skipped: invalid requirement !!!/);
} finally {
  rmSync(root, { recursive: true, force: true });
}
