#!/usr/bin/env node
/**
 * Real-host smoke QA: install the plugin into a real host runtime and run one
 * QA turn through the host's own agent loop.
 *
 * Usage: node tests/e2e/host_smoke.mjs <pi|openclaw|hermes|dsh>
 *
 * Env:
 *   SMOKE_LLM_API_KEY  (required) API key for the QA model
 *   SMOKE_LLM_BASE_URL (required) OpenAI-compatible base URL
 *   SMOKE_LLM_MODEL    (optional) default: deepseek-v4-flash
 *   SMOKE_WORKDIR      (optional) scratch dir, default: mktemp
 *
 * Assertions (both required, retried up to 2 times):
 *   1. the host actually invoked our get_quote tool (tool-call trace)
 *   2. the final answer contains a number (real data reached the reply)
 */
import { execFileSync, execFile } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

const host = process.argv[2];
if (!["pi", "openclaw", "hermes", "dsh"].includes(host)) {
  console.error("usage: host_smoke.mjs <pi|openclaw|hermes|dsh>");
  process.exit(2);
}

const API_KEY = process.env.SMOKE_LLM_API_KEY || "";
const BASE_URL = process.env.SMOKE_LLM_BASE_URL || "";
const MODEL = process.env.SMOKE_LLM_MODEL || "deepseek-v4-flash";
const PROVIDER = process.env.SMOKE_LLM_PROVIDER || "deepseek";
if (!API_KEY || !BASE_URL) {
  console.error("SMOKE_LLM_API_KEY and SMOKE_LLM_BASE_URL are required");
  process.exit(2);
}

const WORK = process.env.SMOKE_WORKDIR || mkdtempSync(join(tmpdir(), `host-smoke-${host}-`));
const QUESTION =
  "Use the get_quote tool to check AAPL's latest price. Reply with only the price number.";
const MAX_ATTEMPTS = 2;

function sh(cmd, args, opts = {}) {
  console.log(`+ ${cmd} ${args.join(" ")}`.slice(0, 160));
  return execFileSync(cmd, args, {
    maxBuffer: 64 * 1024 * 1024,
    stdio: ["ignore", "pipe", "pipe"],
    ...opts,
  }).toString();
}

function assertCond(cond, msg) {
  if (!cond) throw new Error(`assert failed: ${msg}`);
}

// Run one QA attempt via run(attempt) up to MAX_ATTEMPTS times. run() returns
// { ok, log, tail }: ok = pass this attempt, log = per-attempt status line,
// tail = raw output for the failure message. Returns on first ok, else throws.
function retryQA(label, run) {
  let tail = "";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const r = run(attempt);
    tail = r.tail;
    console.log(`attempt ${attempt}: ${r.log}`);
    if (r.ok) return;
  }
  throw new Error(`${label} smoke failed; last output:\n${tail.slice(-800)}`);
}

function findPython() {
  const candidates = [process.env.SMOKE_PYTHON, "python3.13", "python3.12", "python3.11", "python3"].filter(Boolean);
  for (const c of candidates) {
    try {
      const out = execFileSync(c, ["--version"], { stdio: ["ignore", "pipe", "pipe"] }).toString();
      const m = out.match(/(\d+)\.(\d+)/);
      if (m && (Number(m[1]) > 3 || (Number(m[1]) === 3 && Number(m[2]) >= 11))) return c;
    } catch { /* try next */ }
  }
  throw new Error("python >= 3.11 not found (set SMOKE_PYTHON)");
}

// ---------------------------------------------------------------- pi -------
async function smokePi() {
  // Real install path: project-local `pi install <repo>` records the package in
  // .pi/settings.json; --approve bypasses the interactive project-trust prompt.
  // No separate list assertion: the QA below is the load proof — an unloaded
  // plugin can never produce a get_quote tool call.
  sh("pi", ["install", repoRoot, "-l"], { cwd: WORK });

  retryQA("pi", () => {
    const out = sh(
      "pi",
      [
        "--approve", "-p", "--mode", "json", "--no-session",
        "--provider", PROVIDER, "--model", MODEL, "--api-key", API_KEY,
        QUESTION,
      ],
      { cwd: WORK }
    );
    const calledGetQuote = out.includes('"toolName":"get_quote"');
    // last assistant text in the NDJSON stream
    let finalText = "";
    for (const line of out.split("\n")) {
      if (!line.trim()) continue;
      try {
        const ev = JSON.parse(line);
        if (ev.type === "message_end" && ev.message?.role === "assistant") {
          const text = (ev.message.content || [])
            .filter((c) => c.type === "text")
            .map((c) => c.text)
            .join("");
          if (text.trim()) finalText = text;
        }
      } catch { /* partial lines */ }
    }
    return {
      ok: calledGetQuote && /\d/.test(finalText),
      log: `toolCalled=${calledGetQuote} final=${finalText.slice(0, 80)}`,
      tail: out,
    };
  });
}

// ----------------------------------------------------------- openclaw -------
function smokeOpenclaw() {
  // Replicate the publish payload: stage shared dirs, build dist, pack, install.
  for (const d of ["tools", "skills", "schemas", "templates", "strategies", "scripts"]) {
    sh("rm", ["-rf", join(repoRoot, "openclaw", d)]);
    sh("cp", ["-R", join(repoRoot, d), join(repoRoot, "openclaw", d)]);
  }
  sh("npm", ["run", "build:openclaw"], { cwd: repoRoot });
  const packOut = sh("npm", ["pack"], { cwd: join(repoRoot, "openclaw") });
  const tgz = join(repoRoot, "openclaw", packOut.trim().split("\n").pop());

  const oc = (args, opts = {}) =>
    sh("openclaw", ["--profile", "ci-smoke", ...args], opts);
  oc(["plugins", "install", tgz, "--force"]);
  const extDir = join(process.env.HOME, ".openclaw-ci-smoke", "extensions", "stock-analysis");
  assertCond(existsSync(join(extDir, "dist", "index.js")), "installed plugin has dist/index.js");
  assertCond(existsSync(join(extDir, "tools", "stock_data.py")), "installed plugin has tools/");

  // Lifecycle scripts don't run on plugin install — set the python env up like
  // the docs tell users to.
  if (!existsSync(join(extDir, ".venv"))) {
    sh("node", [join(extDir, "scripts", "setup-python.mjs")], { cwd: extDir });
  }

  // DeepSeek via openai-completions custom provider.
  oc([
    "config", "set", "models.providers.smokellm",
    JSON.stringify({
      baseUrl: BASE_URL,
      apiKey: API_KEY,
      api: "openai-completions",
      models: [{
        id: MODEL, name: MODEL, reasoning: false, input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000, maxTokens: 8192,
      }],
    }),
    "--strict-json",
  ]);
  oc(["config", "set", "agents.defaults.model.primary", `smokellm/${MODEL}`]);

  retryQA("openclaw", () => {
    const sessionId = `smoke-${Date.now()}`;
    const out = oc([
      "agent", "--local", "--session-id", sessionId,
      "-m", QUESTION, "--json", "--timeout", "180",
    ]);
    const parsed = JSON.parse(out);
    const text = (parsed.payloads || []).map((p) => p.text || "").join("\n");
    // tool-call trace lives in the session transcript file
    const sessionFile = parsed.meta?.agentMeta?.sessionFile;
    let calledGetQuote = false;
    if (sessionFile && existsSync(sessionFile)) {
      calledGetQuote = readFileSync(sessionFile, "utf-8").includes("get_quote");
    }
    return {
      ok: calledGetQuote && /\d/.test(text),
      log: `toolCalled=${calledGetQuote} final=${text.slice(0, 80)}`,
      tail: text,
    };
  });
}

// ------------------------------------------------------------- hermes -------
function smokeHermes() {
  const venv = join(WORK, "venv");
  sh(findPython(), ["-m", "venv", venv]);
  const pip = join(venv, "bin", "pip");
  const hermes = join(venv, "bin", "hermes");
  sh(pip, ["install", "--quiet", "--upgrade", "pip"]);
  sh(pip, ["install", "--quiet", "hermes-agent"]);
  sh(pip, ["install", "--quiet", repoRoot]);
  // handlers shell out to "python3" — make it resolve to this venv, which has
  // the full data-source deps (yfinance etc.) from tools/requirements.txt
  sh(pip, ["install", "--quiet", "-r", join(repoRoot, "tools", "requirements.txt")]);

  const plugins = sh(hermes, ["plugins", "list"]);
  assertCond(/stock-analysis/.test(plugins), "hermes plugins list should show stock-analysis");
  sh(hermes, ["plugins", "enable", "stock-analysis", "--no-allow-tool-override"]);

  const env = {
    ...process.env,
    PATH: `${join(venv, "bin")}:${process.env.PATH}`,
    DEEPSEEK_API_KEY: API_KEY,
    DEEPSEEK_BASE_URL: BASE_URL,
    KIMI_API_KEY: API_KEY,
    KIMI_BASE_URL: BASE_URL,
    OPENAI_API_KEY: API_KEY,
    OPENAI_BASE_URL: BASE_URL,
  };
  retryQA("hermes", (attempt) => {
    const usageFile = join(WORK, `hermes-usage-${attempt}.json`);
    const out = execFileSync(
      hermes,
      [
        "-z", QUESTION,
        "-m", MODEL,
        "--provider", PROVIDER,
        "-t", "stock-analysis",
        "--usage-file", usageFile,
      ],
      { env, maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] }
    ).toString();
    // oneshot prints the final response only; tool usage is asserted via the
    // usage report: a tool round-trip means >= 2 API calls
    const usage = JSON.parse(readFileSync(usageFile, "utf-8"));
    const toolRoundTrip = usage.completed === true && usage.failed === false && usage.api_calls >= 2;
    const hasNumber = /\d{2,}/.test(out);
    return {
      ok: toolRoundTrip && hasNumber,
      log: `apiCalls=${usage.api_calls} numeric=${hasNumber} out=${out.trim().slice(0, 60)}`,
      tail: out,
    };
  });
}

// ------------------------------------------------------------- dsh --------
function smokeDsh() {
  // Replicate the publish payload: stage shared dirs, build dist, pack.
  for (const d of ["tools", "skills", "schemas", "templates", "strategies", "scripts"]) {
    sh("rm", ["-rf", join(repoRoot, "dsh", d)]);
    sh("cp", ["-R", join(repoRoot, d), join(repoRoot, "dsh", d)]);
  }
  sh("npm", ["run", "build:dsh"], { cwd: repoRoot });
  const packOut = sh("npm", ["pack"], { cwd: join(repoRoot, "dsh") });
  const tgz = join(repoRoot, "dsh", packOut.trim().split("\n").pop());

  // Isolated Harness home. The "headless" profile auto-initializes from the
  // shipped template (dsh-base + dsh-headless) on first `dsh plugin add`;
  // the CLI reconciles our bundle into dsh.profile.bundles from the package's
  // dsh.bundle.patch manifest.
  const dshHome = join(WORK, "dsh-home");
  const env = { ...process.env, DSH_HOME: dshHome, DEEPSEEK_API_KEY: API_KEY };
  const dsh = (args, opts = {}) => sh("dsh", args, { env, ...opts });

  dsh(["plugin", "--profile", "headless", "add", tgz]);
  const pkgDir = join(dshHome, "profiles", "headless", "node_modules", "@weaxs", "dsh-stock-analysis");
  assertCond(existsSync(join(pkgDir, "dist", "index.js")), "installed plugin has dist/index.js");
  assertCond(existsSync(join(pkgDir, "tools", "stock_data.py")), "installed plugin has tools/");

  // Profile installs go through pnpm, which skips dependency lifecycle scripts
  // without an allowBuilds entry — set the python env up like the docs tell
  // users to.
  if (!existsSync(join(pkgDir, ".venv"))) {
    sh("node", [join(pkgDir, "scripts", "setup-python.mjs")], { cwd: pkgDir, env });
  }

  // Non-default endpoint/model overrides ride the documented surfaces: the
  // `llm-deepseek:` section of $DSH_HOME/settings.yaml (what the Models page
  // writes) and a --patch overlay replacing the agent-default-model row.
  // The CI defaults (api.deepseek.com / deepseek-v4-flash) need neither —
  // the key resolves from the inherited DEEPSEEK_API_KEY env per request.
  if (BASE_URL && BASE_URL !== "https://api.deepseek.com") {
    writeFileSync(join(dshHome, "settings.yaml"), `llm-deepseek:\n  baseURL: ${BASE_URL}\n`);
  }

  // One --patch overlay for the whole smoke:
  //   - smoke-tool-spy: logs every executed tool name (dsh's durable session
  //     log only carries the session header in one-shot runs, so the spy —
  //     via the emit-mode `tools/result` event — is the tool-call trace).
  //   - tool-web disabled: removes the built-in web_search fallback so the
  //     model must reach the price through OUR get_quote, not a search.
  //   - agent-default-model: only when SMOKE_LLM_MODEL is non-default.
  const calledToolsLog = join(WORK, "dsh-called-tools.log");
  const spyPlugin = join(repoRoot, "tests", "e2e", "dsh_tool_spy.mjs");
  let overlay =
    `- insert:\n` +
    `    - id: smoke-tool-spy\n` +
    `      name: "${spyPlugin}"\n` +
    `      config:\n` +
    `        logFile: "${calledToolsLog}"\n` +
    `- id: tool-web\n` +
    `  disabled: true\n`;
  if (MODEL !== "deepseek-v4-flash") {
    overlay += `- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: ${MODEL}\n`;
  }
  const overlayPath = join(WORK, "dsh-smoke-overlay.yml");
  writeFileSync(overlayPath, overlay);
  const bootArgs = ["--profile", "headless", "--patch", overlayPath];

  retryQA("dsh", () => {
    // The headless runner prints the last assistant text to stdout and exits
    // non-zero on an incompleted turn — catch so a failed attempt retries.
    let out = "";
    try {
      out = dsh([...bootArgs, QUESTION], { timeout: 480_000 });
    } catch (err) {
      const tail = String(err.stdout ?? "") + String(err.stderr ?? "");
      return { ok: false, log: `exit=${err.status ?? "?"}`, tail: tail.slice(-800) };
    }
    const calledTools = existsSync(calledToolsLog) ? readFileSync(calledToolsLog, "utf-8") : "";
    const calledGetQuote = calledTools.split("\n").includes("get_quote");
    return {
      ok: calledGetQuote && /\d/.test(out),
      log: `toolCalled=${calledGetQuote} tools=[${calledTools.trim().replaceAll("\n", ",")}] final=${out.trim().slice(0, 80)}`,
      tail: out,
    };
  });
}

const runners = { pi: smokePi, openclaw: smokeOpenclaw, hermes: smokeHermes, dsh: smokeDsh };
try {
  await runners[host]();
  console.log(`\nOK: ${host} real-host smoke passed`);
} catch (err) {
  console.error(`\nFAIL: ${host} real-host smoke: ${err.message}`);
  process.exit(1);
}
