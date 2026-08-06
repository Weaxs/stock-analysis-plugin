#!/usr/bin/env node
import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { venvPythonPath } from "./venv-python.mjs";

// fileURLToPath (not URL.pathname) — the latter yields a bogus "/C:/..." path
// on Windows, which would put the venv in the wrong place or fail outright.
const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const venvDir = join(pkgDir, ".venv");
// Drive pip through the venv's own python (`-m pip`) instead of a pip/pip.exe
// shim — resolves correctly on both POSIX (bin/python3) and Windows (Scripts/python.exe).
const venvPython = venvPythonPath(pkgDir);
const requirements = join(pkgDir, "tools", "requirements.txt");

function run(cmd) {
  execSync(cmd, { stdio: "pipe", timeout: 300_000 });
}

function findPython() {
  for (const name of ["python3", "python"]) {
    try {
      const ver = execSync(`${name} --version`, { stdio: "pipe" }).toString().trim();
      const match = ver.match(/(\d+)\.(\d+)/);
      if (match && (parseInt(match[1]) > 3 || (parseInt(match[1]) === 3 && parseInt(match[2]) >= 9))) {
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
      "\n[pi-stock-analysis] python3 >= 3.9 not found.\n" +
      "Install Python 3.9+ and re-run: npm rebuild pi-stock-analysis\n"
    );
    process.exit(0);
  }

  if (!existsSync(venvDir)) {
    run(`${python} -m venv "${venvDir}"`);
  }

  if (existsSync(requirements)) {
    run(`"${venvPython}" -m pip install -q -r "${requirements}"`);
  }
} catch (err) {
  console.warn(
    `\n[pi-stock-analysis] Python setup failed: ${err.message}\n` +
    "You can manually create the venv and install tools/requirements.txt with your platform's python.\n"
  );
  process.exit(0);
}
