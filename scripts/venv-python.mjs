// Canonical venv-python resolution, shared by the postinstall script and the
// e2e harnesses. Kept in one place so the Windows/POSIX shape can't drift
// (previously each site hardcoded its own Scripts/bin + python.exe/python3).
import { existsSync } from "node:fs";
import { join } from "node:path";

const isWin = process.platform === "win32";

// Windows venvs ship Scripts/python.exe (there is no python3); POSIX venvs ship bin/python3.
export function venvPythonPath(rootDir) {
  return join(rootDir, ".venv", isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python3");
}

// Interpreter to actually invoke: the venv python when present, else the
// platform fallback — "python" on Windows, where a bare "python3" is usually
// the Microsoft Store stub.
export function resolvePython(rootDir) {
  const venvPython = venvPythonPath(rootDir);
  return existsSync(venvPython) ? venvPython : isWin ? "python" : "python3";
}
