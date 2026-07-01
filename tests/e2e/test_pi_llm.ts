// Layer 3 e2e: LLM → tool_use → Pi's subprocess executor → answer.
//
// Same agentic loop as tests/e2e/test_hermes_llm.py but exercises Pi's
// subprocess-based tool executor. Only meaningful difference vs Hermes is
// the tool-call path — the LLM prompt/protocol is identical.
//
// Requires DEEPSEEK_API_KEY. Skip (exit 0) if unset — this test is opt-in.
// Model: DEEPSEEK_MODEL env (default: deepseek-v4-flash).

import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { existsSync } from "fs";
import { createJiti } from "jiti";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import OpenAI from "openai";

const execFileAsync = promisify(execFile);

const apiKey = process.env.DEEPSEEK_API_KEY;
if (!apiKey) {
  console.log("DEEPSEEK_API_KEY not set — skipping Pi LLM e2e (opt-in)");
  process.exit(0);
}

const MODEL = process.env.DEEPSEEK_MODEL || "deepseek-v4-flash";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..");
const isWin = process.platform === "win32";
const venvPython = join(repoRoot, ".venv", isWin ? "Scripts" : "bin", "python3");
const python = existsSync(venvPython) ? venvPython : "python3";

// --- Load Pi and capture tool registrations -------------------------------

interface Registered {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  execute: (args: Record<string, unknown>) => Promise<string>;
}
const registered: Registered[] = [];

const mockPi = {
  registerTool(cfg: Registered) {
    registered.push(cfg);
  },
  on() {},
  exec: async (cmd: string) => {
    const parts = cmd.split(/\s+/);
    const bin = parts[0];
    const argv = parts.slice(1);
    const { stdout, stderr } = await execFileAsync(bin, argv, {
      maxBuffer: 32 * 1024 * 1024,
    });
    return { stdout, stderr, exitCode: 0 };
  },
};

const jiti = createJiti(fileURLToPath(import.meta.url));
const mod = (await jiti.import("../../pi/index.ts")) as {
  default: (pi: unknown) => void;
};
mod.default(mockPi);

console.log(`Pi LLM e2e (python: ${python}, model: ${MODEL})`);

// --- DeepSeek client ------------------------------------------------------

const client = new OpenAI({
  apiKey,
  baseURL: "https://api.deepseek.com",
});

function toOpenAITools(names: string[]) {
  return names.map((n) => {
    const t = registered.find((r) => r.name === n)!;
    return {
      type: "function" as const,
      function: {
        name: t.name,
        description: t.description,
        parameters: t.parameters as any,
      },
    };
  });
}

async function runLoop(userMsg: string, toolNames: string[], maxTurns = 3) {
  const tools = toOpenAITools(toolNames);
  const messages: any[] = [{ role: "user", content: userMsg }];
  const toolCallsSeen: { name: string; args: any }[] = [];

  for (let turn = 0; turn < maxTurns; turn++) {
    const resp = await client.chat.completions.create({
      model: MODEL,
      messages,
      tools,
      tool_choice: "auto",
    });
    const msg = resp.choices[0].message;
    messages.push(msg);

    if (!msg.tool_calls || msg.tool_calls.length === 0) {
      return { final: msg.content ?? "", toolCalls: toolCallsSeen };
    }

    for (const call of msg.tool_calls) {
      const name = call.function.name;
      const args = JSON.parse(call.function.arguments || "{}");
      toolCallsSeen.push({ name, args });

      const tool = registered.find((r) => r.name === name);
      const result = tool ? await tool.execute(args) : JSON.stringify({ error: "unknown tool" });

      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: result,
      });
    }
  }
  return { final: "", toolCalls: toolCallsSeen, note: "hit max turns" };
}

// --- Assertions -----------------------------------------------------------

let failures = 0;
function assert(cond: boolean, msg: string) {
  if (!cond) {
    console.error(`  FAIL: ${msg}`);
    failures++;
  }
}

// Test 1a: English — company names → US tickers. Fully offline (regex only).
{
  const r = await runLoop(
    "I want to check on Nvidia and Apple. What are their exact stock tickers? " +
      "Use a tool to extract them — don't guess from memory. " +
      "Reply with just the tickers, comma-separated.",
    ["parse_stock_list"]
  );
  const names = r.toolCalls.map((t) => t.name);
  assert(
    names.includes("parse_stock_list"),
    `LLM did not call parse_stock_list (english). calls=${JSON.stringify(r.toolCalls)}`
  );
  assert(r.final.includes("NVDA"), `final missing NVDA: ${r.final}`);
  assert(r.final.includes("AAPL"), `final missing AAPL: ${r.final}`);
  console.log("  OK: parse_stock_list english flow");
}

// Test 1b: Chinese — full names → A-share codes. Requires akshare for name
// resolution; soft-assert codes to avoid coupling to upstream data health.
{
  const r = await runLoop(
    "我想查贵州茅台和宁德时代的股票代码。用工具从文本里提取，不要凭记忆猜。只回答代码，逗号分隔。",
    ["parse_stock_list"]
  );
  const names = r.toolCalls.map((t) => t.name);
  assert(
    names.includes("parse_stock_list"),
    `LLM did not call parse_stock_list (chinese). calls=${JSON.stringify(r.toolCalls)}`
  );

  // Check what akshare actually returned this time; only assert on codes it resolved.
  const tool = registered.find((r) => r.name === "parse_stock_list")!;
  const probeRaw = await tool.execute({ text: "贵州茅台和宁德时代" });
  const probe = JSON.parse(probeRaw);
  const resolvedCodes = new Set(
    (probe.items || []).filter((i: any) => i.market === "A").map((i: any) => i.symbol)
  );
  for (const expected of ["600519", "300750"]) {
    if (resolvedCodes.has(expected)) {
      assert(
        r.final.includes(expected),
        `tool resolved ${expected} but final answer missing: ${r.final}`
      );
    }
  }
  console.log("  OK: parse_stock_list chinese flow");
}

// Test 2: capabilities boundary
{
  const r = await runLoop(
    "港股支持获取资金流数据（capital_flow）吗？用工具查一下再回答，答案只需 是/否。",
    ["get_market_capabilities"]
  );
  const names = r.toolCalls.map((t) => t.name);
  assert(
    names.includes("get_market_capabilities"),
    `LLM did not call capabilities. calls=${JSON.stringify(r.toolCalls)}`
  );
  const lower = r.final.toLowerCase();
  assert(
    r.final.includes("否") || r.final.includes("不支持") || lower.includes("no"),
    `LLM should answer negative: ${r.final}`
  );
  console.log("  OK: get_market_capabilities flow");
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll Pi LLM e2e checks passed");
