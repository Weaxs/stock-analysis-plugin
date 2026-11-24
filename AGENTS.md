# stock-analysis-plugin

Python CLIs in [tools/](tools/) own stock analysis, screening, and backtesting. Pi, Hermes, OpenClaw, and dsh translate host calls into CLI invocations. [MCP](mcp_host/server.py) reuses Hermes schemas and handlers. Host registration, process lifecycle, and the dsh dashboard belong to their respective hosts.

## Workflow

1. Trace the reported behavior from the user's entry point to the owning code and affected callers. End a read-only investigation by citing what you inspected and distinguishing observed behavior from inference.
2. For a bug, reproduce it through the affected entry point before changing the implementation. For a Python behavior change, add or adapt a focused pytest and confirm it fails for the intended reason on the old code. Apply [ponytail](https://github.com/DietrichGebert/ponytail) by reusing the existing implementation and fixtures and making the smallest complete change.
3. Follow every section below that matches the change. Rerun the reproduction and focused tests that apply. Report which entry points and environments you verified.

Use the [engineering workflow skills](https://github.com/mattpocock/skills) when available. Use `diagnosing-bugs` for regressions and `tdd` for Python behavior changes. For a change that breaks an existing caller or spans multiple implementation phases, use the spec and ticket workflow to settle caller compatibility and acceptance checks before coding. Follow the steps above in any harness. Keep specs, plans, and review notes local or in the issue or PR description.

## Python CLI behavior and output

- Extend the existing argparse CLI in [tools/](tools/) and keep it Python 3.10-compatible. Write one JSON result to stdout and send diagnostics to stderr. Expected failures emit a JSON object with an `error` key and exit nonzero. Sanitize provider errors before returning them.
- **Output changes.** Trace all consumers of the affected fields and preserve existing keys or update every consumer and its assertions together. Verify the actual CLI JSON and exit status for the changed behavior with pytest. Reuse fixtures from [tests/conftest.py](tests/conftest.py).
- **Credentials and providers.** Read credentials from environment variables. Redact credentials, cookies, and full user content from logs. Return sanitized errors to users. Skip unconfigured optional providers so failover remains available.
- **Runtime dependencies.** Check both the npm-installed environment and the Hermes wheel. Keep [tools/requirements.txt](tools/requirements.txt) and the wheel dependencies in [pyproject.toml](pyproject.toml) consistent with what their installed CLIs import.

## Tool names and inputs

- **Tool input changes.** Trace the CLI parser, [shared schema](tools/tool_schemas.json), [JS argv mapping](scripts/tool-calls.mjs), and [Hermes argv mapping](hermes/tools.py). Update them together where the input contract changes. Verify both omitted and explicit values for optional arguments.
- **Tool catalog changes.** When adding, removing, or renaming a tool, also update [OpenClaw's manifest](openclaw/openclaw.plugin.json), [Hermes's manifest](hermes/plugin.yaml), and registration expectations. Update the [README tool list](README.md#工具列表) and affected workflows in [skills/](skills/) when the public interface changes.
- **Verification.** Run [four-host schema and argv parity](tests/test_adapter_parity.ts), [MCP integration](tests/test_mcp_integration.py), and the relevant registration checks in the [CI `compat` job](.github/workflows/ci.yml). For host configuration changes, assert the new behavior through that host's registration or execution entry point.

## Python startup and subprocesses

- **Interpreter, paths, or install layout.** Use [scripts/venv-python.mjs](scripts/venv-python.mjs) for JS interpreter selection and preserve Hermes's `sys.executable` fallback. Resolve native ESM paths with `fileURLToPath` and retain support for loaders that supply `__dirname`. Run pip using the target interpreter's `-m pip`.
- **OpenClaw installation.** Preserve checkout and staged-payload resolution. The installed venv belongs under `node_modules/.stock-analysis-python` because OpenClaw captures the package source, including symlinks outside the excluded `node_modules` directory. Start from the installed package when verifying installation issues. [Runtime entry](openclaw/index.ts) · [Environment resolver](scripts/venv-python.mjs)
- **Installer or process lifecycle.** Run [installer tests](tests/test_setup_python.mjs) after setup changes. For timeout or cancellation changes, verify that descendants exit through the affected JS host or MCP entry point. Use the [CI `compat` matrix](.github/workflows/ci.yml) to check the affected platforms and name any unverified OS or install layout.

## dsh dashboard and discovery

- **Dashboard.** Trace [dashboard.ts](dsh/dashboard.ts) or the [web client](dsh/web/app.ts). Use [dashboard tests](tests/test_dsh_dashboard.ts) for cache and API changes. Use the [browser regression](tests/test_dsh_web_poll.ts) for polling changes and assert the expected visible result.
- **Tool discovery.** Verify the changed registration or discovery behavior with [dsh integration](tests/test_dsh_integration.ts) and [discovery tests](tests/test_dsh_discovery.ts).

## Packaging and releases

- **Package changes.** Follow the affected job in [publish.yml](.github/workflows/publish.yml). Stage and inspect each affected package for its compiled entry, Python payload, and required patches or assets. Keep `@deepseek-ai/*` external in dsh bundles. When changing package names, verify the scoped and unscoped variants, including dsh's patch and client package references.
- **Releases.** Publish from a `vX.Y.Z` tag. CI runs [scripts/sync-version.sh](scripts/sync-version.sh), which writes manifest versions in its checkout. Verify the blocking real-host smoke in the `e2e-host` job and confirm the published artifact in each intended channel. Read the workflow for channel-specific failure handling and the [README's E2E setup](README.md#参与开发) for credentials.

## Local verification

Use `npm ci` to prepare the Python environment, then install [requirements-dev.txt](requirements-dev.txt) with that venv's `python -m pip`. Run Python tests with the venv interpreter and TypeScript checks with `npx tsc --noEmit` when relevant. Use [ci.yml](.github/workflows/ci.yml) for lint, format, and test commands. Keep default tests and host round-trips deterministic and independent of network services, LLMs, and secrets. See [pytest configuration](pyproject.toml) for integration markers.
