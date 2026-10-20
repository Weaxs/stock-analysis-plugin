#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, renameSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { pythonEnvironmentRoot, venvPythonPath } from "./venv-python.mjs";

// fileURLToPath (not URL.pathname) — the latter yields a bogus "/C:/..." path
// on Windows, which would put the venv in the wrong place or fail outright.
const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const envRoot = pythonEnvironmentRoot(pkgDir);
const venvDir = join(envRoot, ".venv");
// Drive pip through the venv's own python (`-m pip`) instead of a pip/pip.exe
// shim — resolves correctly on both POSIX (bin/python3) and Windows (Scripts/python.exe).
const venvPython = venvPythonPath(envRoot);
const requirements = join(pkgDir, "tools", "requirements.txt");

function run(bin, args) {
  execFileSync(bin, args, { stdio: "inherit", timeout: 1_800_000 });
}

function findPython() {
  for (const name of ["python3", "python"]) {
    try {
      const ver = execFileSync(name, ["--version"], { stdio: "pipe" }).toString().trim();
      const match = ver.match(/(\d+)\.(\d+)/);
      if (match && (parseInt(match[1]) > 3 || (parseInt(match[1]) === 3 && parseInt(match[2]) >= 10))) {
        return name;
      }
    } catch {}
  }
  return null;
}

try {
  const python = findPython();
  if (!python) {
    console.warn(
      "\n[stock-analysis-plugin] python3 >= 3.10 not found.\n" +
      "Install Python 3.10+, then re-run: npm install\n"
    );
    process.exit(1);
  }

  if (envRoot !== pkgDir && existsSync(join(pkgDir, ".venv"))) {
    // Preserve installed dependencies; never leave an old environment in captured source.
    if (existsSync(venvDir)) throw new Error("Both legacy and current Python environments exist; move the legacy .venv outside the plugin package");
    mkdirSync(envRoot, { recursive: true });
    renameSync(join(pkgDir, ".venv"), venvDir);
  }
  if (!existsSync(venvDir)) {
    run(python, ["-m", "venv", venvDir]);
  }

  run(venvPython, ["-m", "pip", "install", "-q", "-r", requirements]);
} catch (err) {
  console.warn(
    `\n[pi-stock-analysis] Python setup failed: ${err.message}\n` +
    "You can manually create the venv and install tools/requirements.txt with your platform's python.\n"
  );
  process.exit(1);
}
