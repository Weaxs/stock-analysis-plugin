# stock-analysis-plugin

Stock analysis, screening, and backtesting live in the shared Python CLIs under [tools/](tools/). Four plugin adapters expose the same tool catalog, and the MCP stdio server reuses the Hermes schemas and handlers. Keep analysis logic in Python and keep host integration in the owning adapter.

| Entry point | Responsibility |
| --- | --- |
| [Pi](pi/index.ts), [OpenClaw](openclaw/index.ts), [dsh](dsh/index.ts) | Tool registration, CLI argument mapping via [scripts/tool-calls.mjs](scripts/tool-calls.mjs), skill discovery in Pi and dsh, and schema adaptation in OpenClaw and dsh |
| [Hermes](hermes/__init__.py), [MCP](mcp_host/server.py) | Hermes plugin registration and MCP stdio JSON-RPC via [hermes/tools.py](hermes/tools.py) |
| [dsh dashboard](dsh/dashboard.ts), [client](dsh/client.ts), [web UI](dsh/web/app.ts) | Snapshot cache, watchlist HTTP API, and web and desktop UI backed by Python tools |

## Working on a change

1. **Define the contract.** Trace the entry point across the Python CLI, adapters, dashboard routes, and [skills/](skills/) callers. State what changes and which defaults or output keys callers rely on. For read-only questions, cite the inspected files and separate observed behavior from inference.
2. **Pin the baseline.** For a defect, reproduce it through the affected CLI, adapter, or OS, trace it to the first wrong decision, and check sibling adapters for the same bug. For a Python behavior change, write a focused pytest that fails on the old code for the intended reason first. For performance work, compare the same inputs with [scripts/profile-offline.py](scripts/profile-offline.py) or [scripts/profile-workflows.py](scripts/profile-workflows.py).
3. **Work in verifiable units.** For multi-step work, set the dependency order and verification check for each unit before editing, and verify each unit before building on it. Follow [ponytail](https://github.com/DietrichGebert/ponytail) by reusing [scripts/tool-calls.mjs](scripts/tool-calls.mjs), [scripts/venv-python.mjs](scripts/venv-python.mjs), [tools/_subproc.py](tools/_subproc.py), and [tests/conftest.py](tests/conftest.py) instead of adding parallel helpers or dependencies.
4. **Prove the change.** Rerun the failing reproduction and exercise the real affected entry point. Report the exact commands, observed results, and the host, OS, install mode, or artifact checked. When a target such as Windows, macOS, or an installed host is unavailable, run the nearest local check and state what remains unverified.

When the external [Matt Pocock skills](https://github.com/mattpocock/skills) are installed locally, use `diagnosing-bugs` for regressions, `tdd` for Python behavior changes, and `to-spec` with `to-tickets` for breaking or multi-phase changes. Keep specs, review notes, and external skill files out of commits.

## Local setup and verification

CI runs Node 24 and Python 3.10, 3.11, and 3.14. On Windows, replace `.venv/bin/python` with `.venv/Scripts/python.exe` in every command in this guide. The full pytest suite and [tests/test_subproc.py](tests/test_subproc.py) target POSIX, matching the Ubuntu `test` job in [.github/workflows/ci.yml](.github/workflows/ci.yml). On Windows, run the pytest subset from the `compat` job instead.

First-time setup builds `.venv` through [scripts/setup-python.mjs](scripts/setup-python.mjs) and installs Python dev tools:

```bash
npm ci
.venv/bin/python -m pip install -r requirements-dev.txt
```

Baseline verification:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check tools/ skills/ tests/ hermes/ mcp_host/
.venv/bin/python -m ruff format --check tools/ skills/ tests/ hermes/ mcp_host/
npx tsc --noEmit
```

Run the focused checks for changed areas, and inspect [.github/workflows/ci.yml](.github/workflows/ci.yml) for the full CI matrix. Reuse fixtures in [tests/conftest.py](tests/conftest.py). Because [.gitignore](.gitignore) ignores `*.json` by default, allowlist any new committed fixture or JSON config file there. Keep default tests and offline host round-trips deterministic and free of live providers, LLMs, and credentials. Opt into live tests explicitly with `-m integration_network` or `-m integration_llm`.

## Python CLI behavior

- **CLI contract.** Keep Python 3.10 compatibility and expose CLI actions as `argparse` subcommands. Call `utf8_stdio()` from [tools/_subproc.py](tools/_subproc.py) at CLI entry, and serialize output through `json_safe()` or `json.dumps(..., allow_nan=False)` so Windows stdout stays UTF-8 and non-finite floats never break strict `JSON.parse`. Print one JSON value to stdout and send diagnostics to stderr. On expected failures, print a JSON object with an `error` key and exit nonzero. Never turn a failed read, corrupt store line, or missing provider into silent empty data. Find CLI invocations in [scripts/tool-calls.mjs](scripts/tool-calls.mjs), [hermes/tools.py](hermes/tools.py), or [README CLI examples](README.md#独立-cli-使用), and check output and failure semantics in [docs/tool-behavior.md](docs/tool-behavior.md).
- **Output changes.** Find every consumer of a changed field across adapters, [skills/](skills/), dashboard routes, and tests. Add new keys instead of renaming or removing existing ones, or update every consumer and test in the same change. Assert the CLI exit code and parsed JSON stdout. When failure paths change, assert the nonzero exit code and error JSON too.
- **Provider changes.** Read credentials from environment variables only. Keep credentials, cookies, user content, raw provider responses, and tracebacks out of stderr, logs, and model-facing stdout. When an optional provider is missing or fails, fall back to the remaining configured providers.
- **Runtime dependencies.** Keep [tools/requirements.txt](tools/requirements.txt) and the Python wheel dependencies in [pyproject.toml](pyproject.toml) identical, as enforced by [tests/test_hermes_integration.py](tests/test_hermes_integration.py). Put failover-only packages below the `# optional providers` marker in [tools/requirements.txt](tools/requirements.txt) so [scripts/setup-python.mjs](scripts/setup-python.mjs) can skip any optional provider that fails to install without failing setup. When runtime dependencies change, verify both the npm `.venv` and the Python wheel.

## Shared tool contract

- **Tool name or parameter changes.** Read the CLI parser, [tools/tool_schemas.json](tools/tool_schemas.json), [scripts/tool-calls.mjs](scripts/tool-calls.mjs), and [hermes/tools.py](hermes/tools.py) first, then update every affected file together. Update [README tool list](README.md#工具列表) and every reference under [skills/](skills/).
- **Adding, removing, or renaming a tool.** Also update [tools/capabilities.py](tools/capabilities.py), [openclaw/openclaw.plugin.json](openclaw/openclaw.plugin.json), [hermes/plugin.yaml](hermes/plugin.yaml), [tools/cli_budgets.json](tools/cli_budgets.json) if the command needs a non-default timeout, and [dsh/research-role.ts](dsh/research-role.ts) plus [tests/e2e/test_dsh_research.mjs](tests/e2e/test_dsh_research.mjs) when research presets include the tool. Update tool lists, hardcoded tool counts, and sample inputs in [tests/test_pi_integration.ts](tests/test_pi_integration.ts), [tests/test_hermes_integration.py](tests/test_hermes_integration.py), [tests/test_openclaw_integration.ts](tests/test_openclaw_integration.ts), [tests/test_dsh_integration.ts](tests/test_dsh_integration.ts), [tests/test_adapter_parity.ts](tests/test_adapter_parity.ts), and [tests/test_dsh_discovery.ts](tests/test_dsh_discovery.ts).
- **Adding, removing, or renaming a skill.** Update `pi.skills` in [package.json](package.json) and `EXPECTED_SKILLS` in [tests/test_hermes_integration.py](tests/test_hermes_integration.py).
- **Optional parameter proof.** Run one real tool call that omits the new parameter and one that supplies it against an offline fixture, such as a local store or a stubbed provider. Assert the returned JSON at a real host boundary and run [tests/test_adapter_parity.ts](tests/test_adapter_parity.ts). Parity alone still passes if both [scripts/tool-calls.mjs](scripts/tool-calls.mjs) and [hermes/tools.py](hermes/tools.py) drop the new CLI flag. The e2e scripts below only exercise fixed offline tools, so add a focused boundary test for any other tool.
- **Stub vs real SDK coverage.** The dsh integration, e2e, and parity scripts below stub `@deepseek-ai/dsh-tools` with [tests/_stubs/dsh-tools.ts](tests/_stubs/dsh-tools.ts), OpenClaw integration and e2e scripts stub `typebox` with [tests/_stubs/typebox.ts](tests/_stubs/typebox.ts), and all OpenClaw checkout tests stub `openclaw/plugin-sdk/plugin-entry` with [tests/_stubs/openclaw-sdk.ts](tests/_stubs/openclaw-sdk.ts). Run [tests/test_dsh_sdk_compat.ts](tests/test_dsh_sdk_compat.ts) to compile all schemas through the real `@deepseek-ai/dsh-tools` SDK and real `typebox`, validate closed JSON-input schemas with `validateJsonSchemaValue` and `typebox/value`, and run [tests/test_dsh_discovery.ts](tests/test_dsh_discovery.ts). Run [tests/test_llm_hosts.ts](tests/test_llm_hosts.ts) to verify offline Pi, OpenClaw, and dsh loading and Python dispatch. When MCP protocol or execution behavior changes, run [tests/test_mcp_integration.py](tests/test_mcp_integration.py).

```bash
npx tsx tests/test_adapter_parity.ts
npx tsx tests/test_pi_integration.ts
npx tsx tests/test_openclaw_integration.ts
npx tsx tests/test_dsh_integration.ts
npx tsx tests/test_dsh_sdk_compat.ts
npx tsx tests/test_llm_hosts.ts
.venv/bin/python -m pytest -q tests/test_capabilities.py tests/test_hermes_integration.py tests/test_mcp_integration.py
.venv/bin/python -m pytest -q tests/e2e/test_host_to_python.py
npx tsx tests/e2e/test_pi_e2e.ts
npx tsx tests/e2e/test_openclaw_e2e.ts
npx tsx tests/e2e/test_dsh_e2e.ts
```

## Interpreter, installation, and subprocess changes

- **Python resolution.** Reuse [scripts/venv-python.mjs](scripts/venv-python.mjs). JS adapters use `.venv/bin/python3` on POSIX with a `python3` fallback and `.venv/Scripts/python.exe` on Windows with a `python` fallback. [hermes/tools.py](hermes/tools.py) and [tools/_subproc.py](tools/_subproc.py) use the same `.venv` layout and fall back to `sys.executable`. Invoke pip through the target interpreter with `-m pip`, never a bare `pip` shim.
- **Paths and packaged layouts.** Keep native ESM and `__dirname` loader support in [pi/index.ts](pi/index.ts). Convert `import.meta.url` with `fileURLToPath`, never `URL.pathname`. Preserve both checkout and packaged payload resolution in [openclaw/index.ts](openclaw/index.ts), [dsh/index.ts](dsh/index.ts), and [hermes/tools.py](hermes/tools.py). In an installed OpenClaw package, resolve from `api.rootDir` and keep the venv under `node_modules/.stock-analysis-python/.venv` so host source capture skips its symlinks.
- **Setup and JSON input transport.** When changing [scripts/setup-python.mjs](scripts/setup-python.mjs), test failure, recovery, and migration in [tests/test_setup_python.mjs](tests/test_setup_python.mjs) and run affected host registration tests such as [tests/test_openclaw_integration.ts](tests/test_openclaw_integration.ts). When changing large JSON input transport, verify both [scripts/json-input.mjs](scripts/json-input.mjs) with [tests/test_json_input.ts](tests/test_json_input.ts) and `_run_analysis` in [hermes/tools.py](hermes/tools.py) with [tests/test_hermes_integration.py](tests/test_hermes_integration.py).
- **Cancellation, timeouts, and installed hosts.** When changing timeouts, keep [tools/cli_budgets.json](tools/cli_budgets.json), [scripts/cli-timeout.mjs](scripts/cli-timeout.mjs), and [tools/_subproc.py](tools/_subproc.py) aligned. When changing cancellation or timeouts, verify that JS hosts kill descendant processes and return a JSON error, and that MCP cancellation kills descendant processes and suppresses the response for that request ID. Cover cross-platform fixes in the Linux, macOS, and Windows `compat` job of [.github/workflows/ci.yml](.github/workflows/ci.yml). Whenever the target OS and host are available, verify installed-host defects against a built, installed artifact because checkout tests with stub SDKs do not exercise installed layouts. Otherwise run the nearest local setup and adapter checks and state what remains unverified.

```bash
node tests/test_setup_python.mjs
npx tsx tests/test_json_input.ts
npx tsx tests/test_process_tree.ts
.venv/bin/python -m pytest -q tests/test_mcp_integration.py tests/test_subproc.py
```

## dsh dashboard and discovery

- **Cache and watchlist API.** Verify snapshot caching, persistence, and HTTP routes in [dsh/dashboard.ts](dsh/dashboard.ts) with [tests/test_dsh_dashboard.ts](tests/test_dsh_dashboard.ts).
- **Web UI and polling.** Verify browser polling and rendering in [dsh/web/app.ts](dsh/web/app.ts) with [tests/test_dsh_web_poll.ts](tests/test_dsh_web_poll.ts). For a watchlist race, control the arrival order of the write and snapshot responses and assert that the affected symbol appears or disappears in the rendered rows. Counting requests only proves that a fetch ran. [tests/test_dsh_web_poll.ts](tests/test_dsh_web_poll.ts) uses a simulated DOM. When the installed dsh web or desktop bundle is available, rebuild with `npm run build:dsh` and reproduce host-only UI issues there.
- **Registration, SDK schemas, discovery, and research presets.** Verify [dsh/index.ts](dsh/index.ts) and [dsh/discovery.ts](dsh/discovery.ts) with [tests/test_dsh_integration.ts](tests/test_dsh_integration.ts) and [tests/test_dsh_sdk_compat.ts](tests/test_dsh_sdk_compat.ts). When only discovery changes, run [tests/test_dsh_discovery.ts](tests/test_dsh_discovery.ts) alone because [tests/test_dsh_sdk_compat.ts](tests/test_dsh_sdk_compat.ts) already invokes it. [tsconfig.json](tsconfig.json) omits [dsh/research-role.ts](dsh/research-role.ts). After changing research presets or roles, run `npm run build:dsh`, then verify offline against an installed `@deepseek-ai/dsh` package with `node tests/e2e/test_dsh_research.mjs "$(npm root -g)/@deepseek-ai/dsh"`.

```bash
npx tsx tests/test_dsh_dashboard.ts
npx tsx tests/test_dsh_web_poll.ts
npx tsx tests/test_dsh_integration.ts
npx tsx tests/test_dsh_sdk_compat.ts
```

## Packaging and releases

- **Staging and package contents.** Follow the matching job in [.github/workflows/publish.yml](.github/workflows/publish.yml) to stage and inspect each affected package. Build host bundles with `npm run build:openclaw` and `npm run build:dsh`. To verify the Python wheel, install `build` into `.venv` and run `.venv/bin/python -m build`. Run `npm pack --dry-run` at the repo root and inside staged `openclaw/` and `dsh/` directories to check compiled entries, Python payloads, patches, and web assets. Stage in a disposable checkout, or remove the copied payload directories and `LICENSE` files from `openclaw/` and `dsh/` afterward so adapters do not read stale copies or leave untracked `LICENSE` files.
- **Externals and package names.** Keep `openclaw/*` external in OpenClaw bundles and `@deepseek-ai/*` external in dsh bundles. Every npm package publishes under both a scoped `@weaxs/*` name and an unscoped name. When a package name changes, check both variants, including the dsh package references in [dsh/cordis.patch.yml](dsh/cordis.patch.yml), [dsh/research.patch.yml](dsh/research.patch.yml), [dsh/client.ts](dsh/client.ts), and the unscoped rewrites in [.github/workflows/publish.yml](.github/workflows/publish.yml).
- **Versioning.** Push a `vX.Y.Z` git tag to trigger [.github/workflows/publish.yml](.github/workflows/publish.yml) and derive release versions from the tag. Do not hand-edit manifest versions for a release. Run [scripts/sync-version.sh](scripts/sync-version.sh) locally only in a disposable checkout because it modifies manifests in place.
- **Release gates.** [.github/workflows/publish.yml](.github/workflows/publish.yml) gates publication on [.github/workflows/ci.yml](.github/workflows/ci.yml), including the blocking `e2e-host` jobs that run [tests/e2e/host_smoke.mjs](tests/e2e/host_smoke.mjs) in real Pi, Hermes, OpenClaw, and dsh runtimes. The dsh `e2e-host` job also runs [tests/e2e/test_dsh_research.mjs](tests/e2e/test_dsh_research.mjs) offline after `npm run build:dsh`. For `e2e-host` and `e2e-llm` model credentials, see [README development section](README.md#参与开发). The `network` and `e2e-llm` jobs set `continue-on-error: true` and are advisory. After release, verify the published artifacts on npm for both scoped and unscoped root, OpenClaw, and dsh packages, on PyPI, and on the GitHub Release. ClawHub may emit a warning while the release workflow still succeeds.
