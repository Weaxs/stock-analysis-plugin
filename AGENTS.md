# stock-analysis-plugin — Agent Guidelines

Stock analysis, screening, and backtesting live in the shared Python CLIs under [tools/](tools/). The four plugin adapters expose the same tool catalog, and MCP stdio reuses the Hermes schemas and handlers. Keep stock-analysis behavior in Python and host integration in the owning host.

| Entry point | Responsibility |
| --- | --- |
| [Pi](pi/index.ts), [OpenClaw](openclaw/index.ts), [dsh](dsh/index.ts) | Host registration, schema conversion, and subprocess calls through [the shared JS mapping](scripts/tool-calls.mjs) |
| [Hermes](hermes/__init__.py), [MCP](mcp_host/server.py) | Hermes registration or MCP JSON-RPC using [the same Python tool mapping](hermes/tools.py) |
| [dsh dashboard](dsh/dashboard.ts), [client](dsh/client.ts), [web UI](dsh/web/app.ts) | Snapshot cache, watchlist API, and Web/Desktop UI. Dashboard calls reuse Python tools. |

## Working on a change

1. **Define the outcome.** Trace the user's entry point through the owning code to the affected callers. State what should change and which defaults or contracts callers already rely on. For read-only work, report the source evidence and distinguish observed behavior from inference.
2. **Get a baseline.** For a defect, reproduce it on the affected surface and trace it to its cause. For a Python behavior change, start with a focused pytest that fails for the intended reason. Before refactoring, pin the behavior at the public interface. For performance work, compare the same inputs in the same environment.
3. **Work in verifiable units.** For a multi-step change, identify the dependency order and the check for each unit before implementation. Finish and verify each unit before building on it. A shared tool change is one unit across the CLI, schema, mappings, docs, and tests. Split independent investigation or verification across workers when available. Assign shared writes to one owner.
4. **Prove the outcome.** Rerun the original reproduction or focused test and exercise the affected user entry point. Check the final diff against the scope. Report the commands and observed results, including which host, OS, installation mode, or published artifact was checked when it matters. If the target is unavailable, run the nearest useful check and state exactly what it leaves open.

Apply [ponytail](https://github.com/DietrichGebert/ponytail) by reusing existing code and fixtures after tracing the flow. Use `diagnosing-bugs` for regressions and `tdd` for Python behavior changes when the [workflow skills](https://github.com/mattpocock/skills) are available. Use the spec and ticket workflow for changes that break a public interface or span multiple implementation phases. Keep process drafts, review notes, and local skill installations out of commits.

## Python CLI behavior

- Use the existing argparse subcommand and keep Python 3.10 compatibility. Emit one JSON value on stdout and send diagnostics to stderr. Expected failures emit a JSON object with an `error` key and exit nonzero. Use the [README CLI examples](README.md#独立-cli-使用) and [tool behavior reference](docs/tool-behavior.md) to find the user entry point.
- **Output changes.** Find the consumers of every changed field. Preserve existing keys or update the consumers and assertions together. Verify the actual CLI JSON and exit status, including the failure case when it changes.
- **Provider changes.** Read credentials from environment variables. Keep credentials, cookies, user content, raw provider responses, and tracebacks out of logs and model-facing output. Preserve fallback to configured sources when optional providers are unavailable.
- **Runtime dependencies.** Keep [tools/requirements.txt](tools/requirements.txt) and the Hermes wheel dependencies in [pyproject.toml](pyproject.toml) consistent with the imports each install path needs. Check the npm-installed environment and the wheel when changing runtime dependencies.

## Shared tool contract

For a tool name or input change, first read the CLI parser, [tools/tool_schemas.json](tools/tool_schemas.json), [scripts/tool-calls.mjs](scripts/tool-calls.mjs), and [hermes/tools.py](hermes/tools.py). Update every affected part together. Update the [README tool list](README.md#工具列表) and the workflows in [skills/](skills/) that call the tool. For an add, removal, or rename, update [OpenClaw's manifest](openclaw/openclaw.plugin.json), [Hermes's manifest](hermes/plugin.yaml), and registration expectations. MCP inherits the Hermes mappings.

For a new optional argument, run a real tool call that omits it and one that supplies it. Assert the returned JSON at an affected host boundary so the check would fail if the argument were dropped. Use an offline fixture, for example MCP `tools/call` with a local store. The parity check establishes schema and argv agreement, and registration checks establish host exposure. Two adapters can agree while both omit a flag. Exercise MCP separately when its protocol or execution path changes.

```bash
npx tsx tests/test_adapter_parity.ts
npx tsx tests/test_pi_integration.ts
npx tsx tests/test_openclaw_integration.ts
npx tsx tests/test_dsh_integration.ts
.venv/bin/python -m pytest -q tests/test_hermes_integration.py tests/test_mcp_integration.py
.venv/bin/python -m pytest -q tests/e2e/test_host_to_python.py
npx tsx tests/e2e/test_pi_e2e.ts
npx tsx tests/e2e/test_openclaw_e2e.ts
npx tsx tests/e2e/test_dsh_e2e.ts
```

## Interpreter, installation, and subprocess changes

- Reuse [scripts/venv-python.mjs](scripts/venv-python.mjs). JS adapters use `.venv/bin/python3` on POSIX with a `python3` fallback. On Windows they use `.venv/Scripts/python.exe` with a `python` fallback. Hermes falls back to `sys.executable`. Use the target interpreter's `-m pip`.
- Retain support for native ESM and loaders that supply `__dirname`. Use `fileURLToPath(import.meta.url)` for the ESM path. OpenClaw must resolve both checkout and staged payloads. In an installed OpenClaw package, keep the venv under `node_modules/.stock-analysis-python` so the host's source capture excludes its symlinks. Verify installation bugs from the installed artifact.
- For installer changes, exercise the relevant setup failure, recovery, or migration. For cancellation or timeout changes, verify the affected host stops descendant processes and that the original call returns correctly. See the Linux, macOS, and Windows `compat` matrix in [CI](.github/workflows/ci.yml). A checkout test with a stub host SDK covers that invocation path. Installed-host evidence comes from the installed host.

```bash
node tests/test_setup_python.mjs
npx tsx tests/test_process_tree.ts
.venv/bin/python -m pytest -q tests/test_mcp_integration.py
```

## dsh dashboard and discovery

- **Cache or watchlist API changes.** Exercise the snapshot and persistence behavior with `tests/test_dsh_dashboard.ts`.
- **Web client or polling changes.** Use `tests/test_dsh_web_poll.ts`. For a watchlist race, control the order of the edit and snapshot responses. Assert the affected symbol appears or disappears in the rendered rows. A request count only verifies that a fetch occurred. The test uses a simulated DOM. For a host-only problem, also reproduce with the installed Web/Desktop bundle.
- **Tool registration or discovery changes.** Verify registration and discovery with `tests/test_dsh_integration.ts` and `tests/test_dsh_discovery.ts`.

```bash
npx tsx tests/test_dsh_dashboard.ts
npx tsx tests/test_dsh_web_poll.ts
npx tsx tests/test_dsh_discovery.ts
```

## Local setup and verification

CI uses Node 24. Run `npm ci` to install JS dependencies and prepare the repo venv. Install the Python test dependencies into that environment. On Windows, use `.venv/Scripts/python.exe` in the commands below.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
ruff check tools/ skills/ tests/ hermes/ mcp_host/
ruff format --check tools/ skills/ tests/ hermes/ mcp_host/
npx tsc --noEmit
```

Use the focused checks for your change and [ci.yml](.github/workflows/ci.yml) for the full matrix. Reuse fixtures in [tests/conftest.py](tests/conftest.py). Keep default tests and ordinary host round-trips deterministic and independent of live providers, LLMs, and credentials. The `integration_network` and `integration_llm` markers opt into live dependencies.

## Packaging and releases

- Use the corresponding [publish job](.github/workflows/publish.yml) to stage and check each affected package. `npm run build:openclaw` and `npm run build:dsh` build their bundles. Root `npm pack --dry-run` checks the root npm package. Pack staged OpenClaw and dsh packages from their directories to check their own compiled entry, Python payload, patches, and web assets as applicable.
- Keep `@deepseek-ai/*` external in dsh bundles. Both scoped and unscoped npm names ship. For a package name change, check both variants, including dsh package references in `cordis.patch.yml`, `research.patch.yml`, and `dist/client.js`.
- Releases derive versions from a `vX.Y.Z` tag. CI runs [scripts/sync-version.sh](scripts/sync-version.sh), which writes manifest versions. Use a disposable checkout for local version-sync probes.
- The release workflow gates publication on the `e2e-host` checks, which run [host_smoke.mjs](tests/e2e/host_smoke.mjs) in the real Pi, Hermes, OpenClaw, and dsh runtimes. Setup is in the [README](README.md#参与开发). The provider and LLM integration jobs are advisory. Confirm each intended release artifact at its destination. ClawHub can emit a warning while the workflow succeeds.
