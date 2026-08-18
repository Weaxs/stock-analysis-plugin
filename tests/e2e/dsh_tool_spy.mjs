// dsh tool-call spy for the real-host smoke (tests/e2e/host_smoke.mjs dsh).
// Inserted into the headless profile via a --patch overlay; logs the name of
// every executed tool, one per line, to config.logFile. The smoke reads that
// file to prove our tools were actually invoked — dsh's durable session log
// only carries the session header in one-shot runs, so it can't serve as the
// tool-call trace.
import { appendFileSync } from "node:fs";

export const name = "smoke-tool-spy";
export const inject = ["tools"];

export function apply(ctx, config) {
  // Emit-mode event fired once per settled tool dispatch.
  ctx.on("tools/result", (exec) => {
    appendFileSync(config.logFile, `${exec.name}\n`);
  });
}
