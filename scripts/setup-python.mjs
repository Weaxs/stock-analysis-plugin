#!/usr/bin/env node
import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const pkgDir = new URL("..", import.meta.url).pathname.replace(/\/$/, "");
const isWin = process.platform === "win32";
const venvDir = join(pkgDir, ".venv");
const pip = join(venvDir, isWin ? "Scripts" : "bin", "pip");
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
    run(`"${pip}" install -q -r "${requirements}"`);
  }
} catch (err) {
  console.warn(
    `\n[pi-stock-analysis] Python setup failed: ${err.message}\n` +
    "You can manually run: python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt\n"
  );
  process.exit(0);
}
