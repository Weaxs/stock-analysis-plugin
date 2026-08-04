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

interface ToolResult {
  content: { type: string; text: string }[];
  details: unknown;
}
interface Registered {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  execute: (toolCallId: string, args: Record<string, unknown>) => Promise<ToolResult>;
}
const registered: Registered[] = [];

const mockPi = {
  registerTool(cfg: Registered) {
    registered.push(cfg);
  },
  on() {},
  exec: async (cmd: string, args: string[] = []) => {
    const { stdout, stderr } = await execFileAsync(cmd, args, {
      maxBuffer: 32 * 1024 * 1024,
    });
    return { stdout, stderr, code: 0, killed: false };
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
  const toolCallsSeen: { name: string; args: any; result: any }[] = [];

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

      const tool = registered.find((r) => r.name === name);
      const rawResult = tool
        ? (await tool.execute(call.id, args)).content[0]?.text ?? ""
        : JSON.stringify({ error: "unknown tool" });

      let parsedResult: any = null;
      try {
        parsedResult = JSON.parse(rawResult);
      } catch {
        // leave as null — assertions handle missing structured result
      }
      toolCallsSeen.push({ name, args, result: parsedResult });

      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: rawResult,
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
// Assert on tool_use contract, not LLM's final text (deepseek-v4-flash sometimes
// returns content=null after tool_use — that's model variability, not our bug).
{
  const r = await runLoop(
    "I want to check on Nvidia and Apple. What are their exact stock tickers? " +
      "Use a tool to extract them — don't guess from memory. " +
      "Reply with just the tickers, comma-separated.",
    ["parse_stock_list"]
  );
  const parseCalls = r.toolCalls.filter((t) => t.name === "parse_stock_list");
  assert(
    parseCalls.length > 0,
    `LLM did not call parse_stock_list (english). calls=${JSON.stringify(r.toolCalls)}`
  );
  const symbols = new Set<string>();
  for (const c of parseCalls) {
    for (const item of (c.result?.items || []) as any[]) symbols.add(item.symbol);
  }
  assert(symbols.has("NVDA"), `tool didn't extract NVDA. items across calls: ${[...symbols]}`);
  assert(symbols.has("AAPL"), `tool didn't extract AAPL. items across calls: ${[...symbols]}`);
  console.log("  OK: parse_stock_list english flow");
}

// Test 1b: Chinese — full names → A-share codes. Requires akshare; soft-assert
// specific codes only when akshare actually resolved them.
{
  const r = await runLoop(
    "我想查贵州茅台和宁德时代的股票代码。用工具从文本里提取，不要凭记忆猜。只回答代码，逗号分隔。",
    ["parse_stock_list"]
  );
  const parseCalls = r.toolCalls.filter((t) => t.name === "parse_stock_list");
  assert(
    parseCalls.length > 0,
    `LLM did not call parse_stock_list (chinese). calls=${JSON.stringify(r.toolCalls)}`
  );

  // Probe akshare state outside the LLM path
  const tool = registered.find((r) => r.name === "parse_stock_list")!;
  const probe = JSON.parse(await tool.execute({ text: "贵州茅台和宁德时代" }));
  const probeCodes = new Set(
    (probe.items || []).filter((i: any) => i.market === "A").map((i: any) => i.symbol as string)
  );

  if (probeCodes.size > 0) {
    const gotCodes = new Set<string>();
    for (const c of parseCalls) {
      for (const item of (c.result?.items || []) as any[]) {
        if (item.market === "A") gotCodes.add(item.symbol);
      }
    }
    for (const expected of ["600519", "300750"]) {
      if (probeCodes.has(expected)) {
        assert(
          gotCodes.has(expected),
          `probe resolved ${expected} but LLM's tool calls only yielded ${[...gotCodes]}. ` +
            `Args LLM sent: ${JSON.stringify(parseCalls.map((c) => c.args))}`
        );
      }
    }
  }
  console.log("  OK: parse_stock_list chinese flow");
}

// Test 2: capabilities boundary. Assert on tool output, not LLM final text.
{
  const r = await runLoop(
    "港股支持获取资金流数据（capital_flow）吗？用工具查一下再回答。",
    ["get_market_capabilities"]
  );
  const capCalls = r.toolCalls.filter((t) => t.name === "get_market_capabilities");
  assert(
    capCalls.length > 0,
    `LLM did not call capabilities. calls=${JSON.stringify(r.toolCalls)}`
  );

  const hkCalls = capCalls.filter((c) => c.result?.market === "HK");
  assert(
    hkCalls.length > 0,
    `LLM did not query HK. args: ${JSON.stringify(capCalls.map((c) => c.args))}`
  );

  const unsupported = new Set<string>();
  for (const c of hkCalls) {
    for (const u of (c.result?.unsupported || []) as any[]) unsupported.add(u.tool);
  }
  assert(
    unsupported.has("get_capital_flow"),
    `HK capabilities should mark get_capital_flow unsupported, got: ${[...unsupported]}`
  );
  console.log("  OK: get_market_capabilities flow");
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll Pi LLM e2e checks passed");
