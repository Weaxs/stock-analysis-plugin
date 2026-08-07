# stock-analysis-plugin — Agent Guidelines

## Project Overview

A multi-host agent plugin for stock analysis, screening, and strategy backtesting across **A / HK / US / JP / KR / TW** markets. The same capability ships as a **Pi Agent** extension, a **Hermes Agent** plugin, and an **OpenClaw** plugin.

The architecture is **one Python core, three thin host adapters**:

- `tools/*.py` — the shared CLI tools. **This is the single source of truth** for all behavior. Every tool prints JSON to stdout.
- `pi/index.ts` — Pi extension. `pi.registerTool` ×39, each spawns a `tools/*.py` CLI.
- `hermes/` — Hermes plugin (`plugin.yaml` + `register(ctx)` in `__init__.py`; `tools.py` handlers → subprocess to the same CLIs). Discovered via the `hermes_agent.plugins` entry point.
- `openclaw/` — OpenClaw plugin (`openclaw.plugin.json` manifest + `index.ts` `definePluginEntry` + `registerTool` ×39). Bundled with esbuild.
- `skills/*/SKILL.md` — 20 workflow / strategy-methodology skills shared by all three hosts.

> **The invariant that matters most:** the three adapters expose the *same* 39 tools over the *same* Python CLIs. A change to a tool's name, parameters, or JSON output shape must be applied to **all three adapters and the relevant SKILL.md together** — never just one.

---

## Required Agent Skills

Two external skill sets are mandatory when developing in this repo. Install them for your harness before starting work; if your harness cannot install skills, follow the rules inlined below.

### ponytail — minimal-code discipline

> https://github.com/DietrichGebert/ponytail

Every code change follows ponytail's ladder — stop at the first rung that holds:

```
1. Does this need to exist?   → no: skip it (YAGNI)
2. Already in this codebase?  → reuse it, don't rewrite (check tools/ and the three host adapters first)
3. Stdlib does it?            → use it
4. Native platform feature?   → use it
5. Installed dependency?      → use it — no new deps for one-call problems
6. One line?                  → one line
7. Only then: the minimum that works
```

The ladder runs *after* understanding the problem, never instead of it: read the code the change touches and trace the real flow before picking a rung. Lazy about the solution, never about reading — and never about cross-platform paths, data-loss handling, security, or the host↔Python contract.

Install:
- Claude Code: `/plugin marketplace add DietrichGebert/ponytail`, then `/plugin install ponytail@ponytail` (two separate prompts)
- Codex: `codex plugin marketplace add DietrichGebert/ponytail && codex plugin add ponytail@ponytail`, then trust its two lifecycle hooks under `/hooks`

### mattpocock/skills — engineering workflow skills

> https://github.com/mattpocock/skills

Use this skill set for process work in this repo:
- `to-spec` / `to-tickets` — before non-trivial implementations
- `tdd` — behavior changes in `tools/` (see Testing). The Python core already has broad pytest coverage, so new tool behavior starts with a failing test.
- `diagnosing-bugs` — no fix without a reproduced root cause. Especially for cross-platform path bugs, reproduce on the failing OS shape before patching.
- `research`, `code-review`, `resolving-merge-conflicts`, `triage` — as named

Install:
- Any harness (skills.sh): `npx skills@latest add mattpocock/skills` — include `/setup-matt-pocock-skills` and run it once per clone (issue tracker: GitHub)
- Claude Code plugin: `/plugin marketplace add mattpocock/skills`, then `/plugin install mattpocock-skills@mattpocock`

Files the skills.sh installer copies into the repo are local tooling — do not commit them unless the team explicitly adopts a skill as a local fork.

---

## Architecture: One Core, Three Adapters

All real work happens in `tools/*.py`. The adapters only translate a host-native tool call into a subprocess invocation of a CLI and relay the JSON stdout back. Keep them thin — no business logic in TypeScript or in `hermes/tools.py` beyond argument mapping and subprocess plumbing.

### Pi — `pi/index.ts`

```typescript
// baseDir: __dirname under jiti/CJS-style loaders, else import.meta.url under native ESM.
// venv python resolved with the Windows/POSIX rule below; each tool spawns the CLI.
const py = (script: string, args: string[]) => pi.exec(python, [`${toolsDir}/${script}`, ...args]);

pi.registerTool({
  name: "get_kline",
  description: "…",
  parameters: { type: "object", properties: { /* … */ }, required: ["symbol"] },
  async execute(_id, { symbol, period = "daily", count = 60 }) {
    const result = await py("stock_data.py", ["kline", symbol, "--period", period, "--count", String(count)]);
    return { content: [{ type: "text" as const, text: result.stdout }], details: {} };
  },
});
```

### Hermes — `hermes/tools.py`

```python
# _find_python() applies the same Windows/POSIX venv rule; _run() shells the CLI
# with a 120s timeout and always returns a JSON string (errors included).
def get_kline(args: dict, **kwargs) -> str:
    return _run("stock_data.py", f"kline {args['symbol']} --period {args.get('period','daily')} --count {args.get('count',60)}")
```

### OpenClaw — `openclaw/index.ts`

```typescript
// definePluginEntry + typebox schemas. The executor is indirected via
// __setExecutor() so tests can stub it without touching node:child_process.
// toolsDir has a dev vs published fallback; venv has a repo-root vs staged-payload fallback.
```

---

## Cross-Platform & Path Invariants

This is the #1 regression surface (issues #2 / #7; the `compat` CI job exists specifically to catch it). The ubuntu-only jobs structurally cannot see these bugs — **think about Windows and macOS whenever you touch a path, an interpreter, or a subprocess.**

- **venv python location**: Windows venvs ship `.venv/Scripts/python.exe` (there is no `python3`); POSIX venvs ship `.venv/bin/python3`. When no venv exists, fall back to `python` on Windows (a bare `python3` is usually the Microsoft Store stub) and `python3` on POSIX. This exact rule is **duplicated** in `pi/index.ts`, `openclaw/index.ts`, `hermes/tools.py`, and canonically in `scripts/venv-python.mjs` (`venvPythonPath` / `resolvePython`). If you change it, change it everywhere — do not let the copies drift. New host-side code should reuse `scripts/venv-python.mjs`, not re-hardcode.
- **baseDir resolution**: `__dirname` is undefined under native ESM; use the `__dirname`-else-`import.meta.url` fallback. Use `fileURLToPath`, never `URL.pathname` (yields a bogus `/C:/...` path on Windows).
- **toolsDir layout differs dev vs published**: `openclaw/index.ts` already handles both (`tools/` next to `index.ts` when packed, `../tools` in a dev checkout). Preserve that fallback.
- Drive pip through the venv's own python (`-m pip`) rather than a `pip`/`pip.exe` shim — resolves correctly on both layouts.

---

## Python Tool Conventions (`tools/`)

- **Output is JSON on stdout, always.** Host adapters parse `result.stdout`. Human-readable prose goes to stderr, never stdout.
- **Errors are data**: print a JSON object with an `error` key (and exit non-zero) rather than an uncaught traceback, so adapters can surface a clean message.
- **argparse CLI per script**, subcommands per capability (`stock_data.py kline|quote|news|...`). See the README "独立 CLI 使用" section for the established shape.
- **Keep `tools/` py3.9-compatible** (`ruff.toml` targets `py39`; OpenClaw hosts only guarantee Python ≥ 3.9) even though the Hermes wheel declares `requires-python >= 3.10`. No 3.10+-only syntax in `tools/`.
- **Line length 120**, ruff rules `E,F,W,I,UP,B,SIM` (E501 ignored).
- Adding a runtime dependency means adding it to `tools/requirements.txt` (the postinstall-built `.venv`) **and** considering the Hermes wheel's `dependencies` in `pyproject.toml`. Follow ponytail rung 5 — don't add a dependency for a one-call problem.

---

## Testing

Frameworks: **pytest** (Python) + **tsx** (TypeScript integration/e2e). No vitest/jest.

- Python unit tests live in `tests/test_*.py`; shared fixtures in `tests/conftest.py` (e.g. `make_kline_data` for synthetic OHLCV). Reuse these fixtures instead of hand-rolling DataFrames.
- `tests/conftest.py` imports numpy/pandas at module load, so **any** pytest run needs the tools' runtime deps in the active interpreter. Use the repo venv: `.venv/bin/python -m pytest …`.
- Default run excludes network/LLM tests via `addopts = "-m 'not integration_network and not integration_llm'"`. Markers:
  - `integration_network` — real provider calls (akshare/yfinance/…), needs network.
  - `integration_llm` — real LLM calls, needs `DEEPSEEK_API_KEY` + network.
- Deterministic by design: the CI `compat` job and the host↔python round-trips use only the non-network tool set — no LLM, no secrets. Keep new e2e tests deterministic the same way.

Commands:

```bash
.venv/bin/python -m pytest -q                              # unit (offline)
.venv/bin/python -m pytest --cov=tools --cov-report=term-missing   # with coverage (CI)
.venv/bin/python -m pytest tests/test_hermes_integration.py -v     # Hermes registration
npx tsx tests/test_pi_integration.ts                       # Pi registration (jiti load + skill paths)
npx tsx tests/test_openclaw_integration.ts                 # OpenClaw registration
.venv/bin/python -m pytest tests/e2e/test_host_to_python.py -v     # host↔python round-trip
npx tsx tests/e2e/test_pi_e2e.ts                           # Pi real-subprocess e2e
npx tsx tests/e2e/test_openclaw_e2e.ts                     # OpenClaw real-subprocess e2e
```

`tests/e2e/host_smoke.mjs <pi|openclaw|hermes>` is the **blocking** real-host smoke (installs the plugin into the actual runtime and runs one QA turn). It needs `SMOKE_LLM_API_KEY` and is a CI gate — run it before releasing, not on every edit.

---

## Lint, Format, Typecheck

```bash
ruff check tools/ skills/ tests/ hermes/          # lint
ruff format --check tools/ skills/ tests/ hermes/ # format check (autofix.yml auto-fixes on PRs)
ruff check --fix tools/ skills/ tests/ hermes/ && ruff format tools/ skills/ tests/ hermes/   # apply locally
npx tsc --noEmit                                  # TS type check (pi/, openclaw/, tests/*.ts)
```

---

## Versioning & Release

Versions live in **four** manifests that must stay in lockstep: `pyproject.toml`, `package.json`, `package-lock.json`, `openclaw/package.json`.

- **Do not hand-edit versions for a release.** Publishing is tag-driven: push a `vX.Y.Z` tag and `publish.yml` runs `scripts/sync-version.sh "$TAG"`, which rewrites all four in-CI (never committed back). The repo's checked-in version is just the last-released baseline.
- `scripts/sync-version.sh` is idempotent and validates the tag shape; you can dry-run it locally with `GITHUB_REF_NAME=v9.9.9 scripts/sync-version.sh`.
- Verify package contents before publishing: `npm pack --dry-run` (the `build` CI job does this and uploads the tarball).
- Build the OpenClaw bundle with `npm run build:openclaw` (esbuild → `openclaw/dist/index.js`).
- The `postinstall` (`scripts/setup-python.mjs`) builds the repo `.venv` and installs `tools/requirements.txt`. Syntax-check it with `node --check scripts/setup-python.mjs` after edits.

---

## Implementation Discipline

- **Match the existing adapter you're editing** — read the sibling registrations first and copy their style, argument mapping, and error shape. The three adapters are deliberately parallel; keep them parallel.
- **One tool = three registrations + docs.** Adding/renaming/re-paraming a tool touches `pi/index.ts`, `openclaw/index.ts`, `hermes/tools.py` + `hermes/schemas.py`, the relevant `skills/*/SKILL.md`, and the README tool table — together, in one change. Don't land a tool in one host only.
- **Keep the JSON contract stable.** Host adapters and tests parse `tools/` stdout. Changing a key is a breaking change across all three hosts — update all consumers and tests in the same change, or add the new key alongside the old.
- Minimal, scoped diffs. Don't "improve" unrelated code or do cross-cutting refactors unless asked.
- New config keys / tool params need corresponding test coverage (pytest for the core, tsx for the adapter registration).
- Don't swallow exceptions: expected failures (missing symbol, bad input, provider down) become clean JSON `error` results; unexpected exceptions should not leak raw stack traces or response bodies to the user.

---

## Security / Secrets

- Data-source and search credentials come from **env vars only** (`TUSHARE_TOKEN`, `LONGBRIDGE_APP_KEY/SECRET/ACCESS_TOKEN`, `ALPHAVANTAGE_API_KEY`, `FINNHUB_API_KEY`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `SERPAPI_KEY`, `BOCHA_API_KEY`, `SENTIMENT_API_KEY`, …). Never hardcode or commit them. Unconfigured sources are skipped in the failover chain — preserve that graceful-degradation behavior.
- Never log tokens, API keys, cookies, or full user content. stdout of `tools/` is JSON that may reach the model — keep secrets and raw stack traces out of it.
- The LLM e2e jobs are gated on repo-owned secrets and skipped on forks to prevent secret exposure. Don't add tests that print env vars or require secrets in the always-on jobs.

---

## Code Review Rules

- **Adapter sync** — flag any tool name/param/JSON-shape change that landed in fewer than all three adapters + SKILL.md + README. Safe path: one PR updates every host together.
- **Cross-platform paths** — flag hardcoded `bin/python3`/`Scripts/python.exe`, `__dirname` without the ESM fallback, `URL.pathname`, or pip-shim invocations. Safe path: reuse `scripts/venv-python.mjs` and the existing dev/published and venv/fallback chains.
- **Host↔Python contract** — flag `tools/` changes that emit non-JSON on stdout, emit secrets/stack traces, or rename output keys without updating all adapters and tests. Safe path: JSON-only stdout, additive key changes, coverage in pytest + tsx.
- **Dependency additions** — flag new entries to `tools/requirements.txt` / `pyproject.toml` that duplicate stdlib or an installed dep (ponytail rung 5).

---

## Adding a New Tool

1. Implement the CLI in the appropriate `tools/*.py` (argparse subcommand, JSON stdout, py3.9-compatible).
2. Add a failing pytest first (`tests/test_<module>.py`), then make it pass (see `tdd`).
3. Register it in all three adapters: `pi/index.ts`, `openclaw/index.ts`, `hermes/tools.py` + `hermes/schemas.py` — same name, same params.
4. Wire it into the relevant `skills/*/SKILL.md` workflow if an agent should call it.
5. Update the README tool table (and tool count if surfaced).
6. Verify: `ruff check …`, `npx tsc --noEmit`, pytest, and the three registration tests (`test_pi_integration.ts`, `test_openclaw_integration.ts`, `test_hermes_integration.py`).

---

## Build & Test (quick reference)

```bash
npm install                        # also builds repo .venv via postinstall
ruff check tools/ skills/ tests/ hermes/ && ruff format --check tools/ skills/ tests/ hermes/
npx tsc --noEmit
.venv/bin/python -m pytest -q
npx tsx tests/test_pi_integration.ts && npx tsx tests/test_openclaw_integration.ts
.venv/bin/python -m pytest tests/test_hermes_integration.py -v
npm run build:openclaw             # esbuild bundle for OpenClaw
npm pack --dry-run                 # verify publish contents
```
