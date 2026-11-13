import * as cp from "node:child_process";

function terminateTree(child) {
  if (!child.pid) return;
  if (process.platform === "win32") {
    cp.execFile("taskkill", ["/F", "/T", "/PID", String(child.pid)], { timeout: 10_000 }, () => {});
    return;
  }
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch {
    try { child.kill("SIGKILL"); } catch { /* already exited */ }
  }
}

function failure(stdout, code, extra = {}) {
  return Object.assign(new Error("Python command failed"), { stdout, code, ...extra });
}

// POSIX children run in their own session, so the host's terminal hangup no longer reaches them.
const live = new Set();
process.on("exit", () => { for (const child of live) terminateTree(child); });

/** execFile-compatible stdout collection that also terminates nested CLI children.
 * @param {string} bin
 * @param {string[]} argv
 * @param {{timeoutMs?: number, maxBuffer?: number, signal?: AbortSignal}} [options]
 */
export function execFileTree(bin, argv, { timeoutMs, maxBuffer = 32 * 1024 * 1024, signal } = {}) {
  if (signal?.aborted) {
    return Promise.reject(Object.assign(new Error("The operation was aborted"), { name: "AbortError", stdout: "" }));
  }
  return new Promise((resolve, reject) => {
    const child = cp.spawn(bin, argv, {
      detached: process.platform !== "win32",
      stdio: ["ignore", "pipe", "ignore"],
      windowsHide: true,
    });
    live.add(child);
    const chunks = [];
    const stdout = () => Buffer.concat(chunks).toString("utf8");
    let bytes = 0;
    let settled = false;
    let timedOut = false;
    let aborted = false;
    let overflow = false;

    const finish = (error) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      if (error) reject(error);
      else resolve(stdout());
    };
    const onAbort = () => {
      aborted = true;
      terminateTree(child);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    const timer = timeoutMs > 0 ? setTimeout(() => {
      timedOut = true;
      terminateTree(child);
    }, timeoutMs) : null;
    timer?.unref();

    child.stdout.on("data", chunk => {
      if (bytes + chunk.length > maxBuffer) {
        overflow = true;
        terminateTree(child);
        return;
      }
      bytes += chunk.length;
      chunks.push(chunk);
    });
    child.on("error", error => {
      error.stdout = stdout();
      finish(error);
    });
    child.on("close", code => {
      live.delete(child);
      if (overflow) finish(failure(stdout(), "ERR_CHILD_PROCESS_STDIO_MAXBUFFER", { killed: true }));
      else if (timedOut) finish(failure(stdout(), null, { killed: true }));
      else if (aborted) finish(Object.assign(failure(stdout(), null), { name: "AbortError" }));
      else if (code !== 0) finish(failure(stdout(), code));
      else finish();
    });
  });
}
