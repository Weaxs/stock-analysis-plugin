#!/usr/bin/env node
/**
 * Real-host smoke QA: install the plugin into a real host runtime and run one
 * QA turn through the host's own agent loop.
 *
 * Usage: node tests/e2e/host_smoke.mjs <pi|openclaw|hermes|dsh>
 *
 * Env:
 *   OPENAI_BASE_URL    (required) OpenAI-compatible API base URL
 *   OPENAI_API_KEY     (required) API key
 *   OPENAI_MODEL       (required) model ID
 *   SMOKE_WORKDIR      (optional) scratch dir, default: mktemp
 *
 * Assertions (both required, retried up to 2 times):
 *   1. the host actually invoked our get_quote tool (tool-call trace)
 *   2. the final answer contains a number (real data reached the reply)
 */
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { venvPythonPath } from "../../scripts/venv-python.mjs";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

const host = process.argv[2];
if (!["pi", "openclaw", "hermes", "dsh"].includes(host)) {
  console.error("usage: host_smoke.mjs <pi|openclaw|hermes|dsh>");
  process.exit(2);
}

const API_KEY = process.env.OPENAI_API_KEY || "";
const BASE_URL = process.env.OPENAI_BASE_URL || "";
const MODEL = process.env.OPENAI_MODEL || "";
if (!API_KEY || !BASE_URL || !MODEL) {
  console.error("OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL are required");
  process.exit(2);
}

const WORK = process.env.SMOKE_WORKDIR || mkdtempSync(join(tmpdir(), `host-smoke-${host}-`));
const QUESTION =
  "Use the get_quote tool to check AAPL's latest price. Reply with only the price number.";
const MAX_ATTEMPTS = 2;

function sh(cmd, args, opts = {}) {
  console.log(`+ ${cmd} (${args.length} args)`);
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
  const candidates = [process.env.SMOKE_PYTHON, "python3.14", "python3"].filter(Boolean);
  for (const c of candidates) {
    try {
      const out = execFileSync(c, ["--version"], { stdio: ["ignore", "pipe", "pipe"] }).toString();
      const m = out.match(/(\d+)\.(\d+)/);
      if (m && Number(m[1]) === 3 && Number(m[2]) === 14) return c;
    } catch { /* try next */ }
  }
  throw new Error("Hermes v0.22.5 requires Python 3.14 (set SMOKE_PYTHON)");
}

// ---------------------------------------------------------------- pi -------
async function smokePi() {
  const piDir = join(WORK, "pi-agent");
  mkdirSync(piDir, { recursive: true });
  writeFileSync(join(piDir, "models.json"), JSON.stringify({
    providers: { smokellm: {
      baseUrl: BASE_URL, api: "openai-completions", apiKey: "$OPENAI_API_KEY",
      models: [{ id: MODEL }],
    } },
  }));
  const env = { ...process.env, PI_CODING_AGENT_DIR: piDir };
  // Real install path: project-local `pi install <repo>` records the package in
  // .pi/settings.json; --approve bypasses the interactive project-trust prompt.
  // No separate list assertion: the QA below is the load proof — an unloaded
  // plugin can never produce a get_quote tool call.
  sh("pi", ["install", repoRoot, "-l"], { cwd: WORK, env });

  retryQA("pi", () => {
    const out = sh(
      "pi",
      [
        "--approve", "-p", "--mode", "json", "--no-session",
        "--provider", "smokellm", "--model", MODEL,
        QUESTION,
      ],
      { cwd: WORK, env }
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
  oc(["plugins", "install", tgz, "--force", "--accept-capabilities"]);
  const extDir = join(process.env.HOME, ".openclaw-ci-smoke", "extensions", "stock-analysis");
  assertCond(existsSync(join(extDir, "dist", "index.js")), "installed plugin has dist/index.js");
  assertCond(existsSync(join(extDir, "tools", "stock_data.py")), "installed plugin has tools/");

  // Plugin installs may leave an existing but incomplete venv.
  sh("node", [join(extDir, "scripts", "setup-python.mjs")], { cwd: extDir });

  // OpenAI-compatible custom provider; resolve the key from the environment.
  oc([
    "config", "set", "models.providers.smokellm",
    JSON.stringify({
      baseUrl: BASE_URL,
      apiKey: "${OPENAI_API_KEY}",
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
  oc(["config", "set", "tools.allow", '["get_quote"]', "--strict-json"]);

  retryQA("openclaw", () => {
    const sessionKey = `agent:main:smoke-${Date.now()}`;
    const out = oc([
      "agent", "--local", "--session-key", sessionKey,
      "-m", QUESTION, "--json", "--timeout", "180",
    ]);
    const parsed = JSON.parse(out);
    const text = (parsed.payloads || []).map((p) => p.text || "").join("\n");
    // Current OpenClaw stores active sessions in SQLite, not session files.
    const calledGetQuote = oc(["sessions", "tail", "--session-key", sessionKey, "--tail", "80"]).includes("get_quote");
    return {
      ok: calledGetQuote && /\d/.test(text),
      log: `toolCalled=${calledGetQuote} final=${text.slice(0, 80)}`,
      tail: text,
    };
  });
}

// ------------------------------------------------------------- hermes -------
function smokeHermes() {
  const venv = join(WORK, ".venv");
  sh(findPython(), ["-m", "venv", venv]);
  const python = venvPythonPath(WORK);
  const binDir = dirname(python);
  const hermes = join(binDir, process.platform === "win32" ? "hermes.exe" : "hermes");
  sh(python, ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]);
  const source = join(WORK, "hermes-source");
  sh("git", ["clone", "--quiet", "--depth", "1", "--branch", "v0.22.5", "https://github.com/NousResearch/hermes-agent.git", source]);
  assertCond(sh("git", ["-C", source, "rev-parse", "HEAD"]).trim() === "bf0203a794a07aac3ff764972ab9087baa4e4f4e", "Hermes source commit must match verified release");
  // Hermes' uv.lock carries dependency overrides that pip cannot represent.
  sh(python, ["-m", "pip", "install", "--quiet", "uv==0.13.6"]);
  const uv = join(binDir, process.platform === "win32" ? "uv.exe" : "uv");
  sh(uv, ["sync", "--locked", "--no-dev", "--inexact", "--project", source, "--python", python], {
    env: { ...process.env, UV_PROJECT_ENVIRONMENT: venv },
  });
  sh(python, ["-m", "pip", "install", "--quiet", repoRoot]);
  // handlers shell out to "python3" — make it resolve to this venv, which has
  // the full data-source deps (yfinance etc.) from tools/requirements.txt
  sh(python, ["-m", "pip", "install", "--quiet", "-r", join(repoRoot, "tools", "requirements.txt")]);

  const hermesHome = join(WORK, "hermes-home");
  const spyDir = join(hermesHome, "plugins", "smoke-tool-spy");
  mkdirSync(spyDir, { recursive: true });
  writeFileSync(join(spyDir, "plugin.yaml"), "name: smoke-tool-spy\nversion: 0.1.0\ndescription: Record tool names for smoke QA\n");
  writeFileSync(join(spyDir, "__init__.py"), readFileSync(join(repoRoot, "tests", "e2e", "hermes_tool_spy.py")));
  const calledToolsLog = join(WORK, "hermes-called-tools.jsonl");
  const env = {
    ...process.env,
    HERMES_HOME: hermesHome,
    HERMES_SMOKE_TOOL_LOG: calledToolsLog,
    PATH: `${binDir}${delimiter}${process.env.PATH}`,
    OPENAI_API_KEY: API_KEY,
    OPENAI_BASE_URL: BASE_URL,
  };
  const plugins = sh(hermes, ["plugins", "list"], { env });
  assertCond(/stock-analysis/.test(plugins), "hermes plugins list should show stock-analysis");
  sh(hermes, ["plugins", "enable", "stock-analysis", "--no-allow-tool-override"], { env });
  sh(hermes, ["plugins", "enable", "smoke-tool-spy", "--no-allow-tool-override"], { env });
  // Enabling plugins selects a PM-owned environment, hiding packages in the bootstrap venv.
  const runtimePython = sh(python, ["-c", "import hermes_bootstrap; from pathlib import Path; from pm.environments import project_python; print(project_python(Path(__import__('hermes_cli').__file__).parent.parent))"], { env }).trim();
  sh(runtimePython, ["-m", "ensurepip", "--upgrade"], { env });
  sh(runtimePython, ["-m", "pip", "install", "--quiet", repoRoot], { env });
  env.PATH = `${dirname(runtimePython)}${delimiter}${env.PATH}`;
  // openai-api selects Responses; the openai alias honors our Chat Completions proxy.
  const provider = "openai";
  sh(runtimePython, ["-c", `
import hermes_bootstrap, os, sys
from hermes_cli.oneshot import _validate_explicit_toolsets
from hermes_cli.runtime_provider import resolve_runtime_provider
toolsets, error = _validate_explicit_toolsets(['stock-analysis'])
assert toolsets == ['stock-analysis'] and error is None, error
runtime = resolve_runtime_provider(requested=sys.argv[1])
assert runtime['api_mode'] == 'chat_completions', 'smoke requires Chat Completions'
assert runtime['base_url'].rstrip('/') == os.environ['OPENAI_BASE_URL'].rstrip('/'), 'smoke endpoint mismatch'
`, provider], { env });
  retryQA("hermes", (attempt) => {
    writeFileSync(calledToolsLog, "");
    const usageFile = join(WORK, `hermes-usage-${attempt}.json`);
    const processResult = spawnSync(
      hermes,
      [
        "-z", QUESTION,
        "-m", MODEL,
        "--provider", provider,
        "--reasoning", "low",
        "-t", "stock-analysis",
        "--usage-file", usageFile,
      ],
      { env, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] }
    );
    const out = processResult.stdout || "";
    // Auxiliary model calls also count in usage, so require the actual tool hook.
    const usage = existsSync(usageFile) ? JSON.parse(readFileSync(usageFile, "utf-8")) : {};
    const calledGetQuote = readFileSync(calledToolsLog, "utf-8").split("\n").includes(JSON.stringify("get_quote"));
    const toolRoundTrip = processResult.status === 0 && calledGetQuote && usage.completed === true && usage.failed === false;
    const hasNumber = /\d{2,}/.test(out);
    return {
      ok: toolRoundTrip && hasNumber,
      log: `exit=${processResult.status} toolCalled=${calledGetQuote} apiCalls=${usage.api_calls} numeric=${hasNumber}`,
      tail: `${out}\n${processResult.stderr || ""}\n${usage.failure || ""}`.replaceAll(API_KEY, "<REDACTED>"),
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
  const env = { ...process.env, DSH_HOME: dshHome };
  const dsh = (args, opts = {}) => sh("dsh", args, { env, ...opts });

  dsh(["plugin", "--profile", "headless", "add", tgz]);
  const pkgDir = join(dshHome, "profiles", "headless", "node_modules", "@weaxs", "dsh-stock-analysis");
  assertCond(existsSync(join(pkgDir, "dist", "index.js")), "installed plugin has dist/index.js");
  assertCond(existsSync(join(pkgDir, "tools", "stock_data.py")), "installed plugin has tools/");

  // Profile installs go through pnpm, which skips dependency lifecycle scripts
  // without an allowBuilds entry — set the python env up like the docs tell
  // users to.
  sh("node", [join(pkgDir, "scripts", "setup-python.mjs")], { cwd: pkgDir, env });

  // One --patch overlay for the whole smoke:
  //   - smoke-tool-spy: logs every executed tool name (dsh's durable session
  //     log only carries the session header in one-shot runs, so the spy —
  //     via the emit-mode `tools/result` event — is the tool-call trace).
  //   - tool-web disabled: removes the built-in web_search fallback so the
  //     model must reach the price through OUR get_quote, not a search.
  //   - llm-pi-ai / agent-default-model: route to the configured endpoint.
  const calledToolsLog = join(WORK, "dsh-called-tools.log");
  const spyPlugin = join(repoRoot, "tests", "e2e", "dsh_tool_spy.mjs");
  const overlay =
    `- insert:\n` +
    `    - id: smoke-tool-spy\n` +
    `      name: "${spyPlugin}"\n` +
    `      config:\n` +
    `        logFile: "${calledToolsLog}"\n` +
    `- id: tool-web\n` +
    `  disabled: true\n` +
    `- id: llm-pi-ai\n` +
    `  config:\n` +
    `    providers:\n` +
    `      smokellm:\n` +
    `        apiKeyEnv: OPENAI_API_KEY\n` +
    `        api: openai-completions\n` +
    `        baseURL: ${JSON.stringify(BASE_URL)}\n` +
    `        models:\n` +
    `          - id: ${JSON.stringify(MODEL)}\n` +
    `- id: agent-default-model\n` +
    `  config:\n` +
    `    provider: smokellm\n` +
    `    model: ${JSON.stringify(MODEL)}\n`;
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
