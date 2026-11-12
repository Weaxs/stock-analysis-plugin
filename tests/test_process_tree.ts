import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createJiti } from "jiti";
import { fileURLToPath } from "node:url";
import { execFileTree } from "../scripts/exec-process.mjs";

const { __defaultExecutor: dshExecutor } = await import("../dsh/index.js");
const { __defaultProcessExecutor: piExecutor } = await import("../pi/index.js");
(globalThis as any).__OPENCLAW_TEST__ = { fakeSdk: { definePluginEntry: (config: unknown) => config }, fakeTypebox: { Type: {} } };
const jiti = createJiti(fileURLToPath(import.meta.url), {
  alias: {
    "openclaw/plugin-sdk/plugin-entry": join(fileURLToPath(new URL(".", import.meta.url)), "_stubs/openclaw-sdk.ts"),
    typebox: join(fileURLToPath(new URL(".", import.meta.url)), "_stubs/typebox.ts"),
  },
});
const { __defaultExecutor: openclawExecutor } = await jiti.import("../openclaw/index.ts") as any;

function alive(pid: number): boolean {
  if (process.platform === "win32") {
    return execFileSync("tasklist", ["/FI", `PID eq ${pid}`], { encoding: "utf8" }).includes(String(pid));
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function cleanup(pid: number) {
  if (process.platform === "win32") {
    try { execFileSync("taskkill", ["/F", "/T", "/PID", String(pid)]); } catch {}
  } else {
    try { process.kill(pid, "SIGKILL"); } catch {}
  }
}

const runners = [
  ["dsh", (bin: string, argv: string[], timeoutMs: number, signal?: AbortSignal) => dshExecutor(bin, argv, timeoutMs, signal)],
  ["pi", async (bin: string, argv: string[], timeoutMs: number, signal?: AbortSignal) => {
    const result = await piExecutor(bin, argv, { timeout: timeoutMs, signal });
    return JSON.stringify({ error: result.killed ? "aborted" : result.stdout });
  }],
  ["openclaw", (bin: string, argv: string[], timeoutMs: number, signal?: AbortSignal) => openclawExecutor(bin, argv, signal)],
] as const;

for (const [host, run] of runners) {
  for (const mode of ["abort", "timeout"] as const) {
    const dir = mkdtempSync(join(tmpdir(), "stock-process-tree-"));
    const pidFile = join(dir, "grandchild.pid");
    const parent = `
      const { spawn } = require("node:child_process");
      const fs = require("node:fs");
      const child = spawn(process.execPath, ["-e", "setTimeout(() => {}, 20000)"], { stdio: "ignore" });
      fs.writeFileSync(process.argv[1], String(child.pid));
      setTimeout(() => {}, 20000);
    `;
    const controller = new AbortController();
    const pending = mode === "timeout"
      ? execFileTree(process.execPath, ["-e", parent, pidFile], { timeoutMs: 200 }).catch(error => {
          assert.equal(error.killed, true);
          return JSON.stringify({ error: "timeout" });
        })
      : run(process.execPath, ["-e", parent, pidFile], 60_000, controller.signal);
    const deadline = Date.now() + 10_000;
    while (!existsSync(pidFile) && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    assert(existsSync(pidFile), host + " grandchild started");
    const pid = Number(readFileSync(pidFile, "utf8"));
    try {
      const started = Date.now();
      let result: string;
      if (mode === "abort") controller.abort();
      result = await pending;
      assert.equal(typeof JSON.parse(result).error, "string");
      assert(Date.now() - started < 10_000, host + " " + mode + " returns promptly");
      const gone = Date.now() + 5_000;
      while (alive(pid) && Date.now() < gone) {
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      assert(!alive(pid), host + " " + mode + " must kill the nested process tree");
    } finally {
      cleanup(pid);
    }
  }
}
console.log("process-tree cancellation: Pi, dsh and OpenClaw abort/timeout kill nested children");

if (process.platform !== "win32") {
  const dir = mkdtempSync(join(tmpdir(), "stock-process-exit-"));
  const pidFile = join(dir, "child.pid");
  const execUrl = new URL("../scripts/exec-process.mjs", import.meta.url).href;
  const host = `
    import { execFileTree } from ${JSON.stringify(execUrl)};
    import { existsSync } from "node:fs";
    execFileTree(process.execPath, ["-e", "require('node:fs').writeFileSync(process.argv[1], String(process.pid)); setTimeout(() => {}, 20000)", ${JSON.stringify(pidFile)}]).catch(() => {});
    setInterval(() => { if (existsSync(${JSON.stringify(pidFile)})) process.exit(0); }, 5);
  `;
  execFileSync(process.execPath, ["--input-type=module", "-e", host], { timeout: 10_000 });
  const pid = Number(readFileSync(pidFile, "utf8"));
  try {
    const gone = Date.now() + 5_000;
    while (alive(pid) && Date.now() < gone) await new Promise(resolve => setTimeout(resolve, 10));
    assert(!alive(pid), "host exit must kill detached CLI process trees");
  } finally {
    cleanup(pid);
  }
  console.log("process-tree cleanup: host exit kills detached POSIX children");
}
