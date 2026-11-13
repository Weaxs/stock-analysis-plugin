# Pi Agent Integration Guide

Use `stock-analysis-plugin` as a [Pi Agent](https://pi.dev) extension.

## Prerequisites

- Node.js >= 22.19.0
- Python >= 3.10
- Pi Agent installed and working

## Installation

### Recommended: Pi package manager

```bash
pi install npm:stock-analysis-plugin
```

This installs globally (`~/.pi/agent/npm/`); add `-l` to install for the current project only (written to `.pi/settings.json`). The same package is also published as `@weaxs/stock-analysis-plugin` with identical contents.

The `postinstall` script finds `python3` (`python` on Windows, >= 3.10), creates a `.venv` in the plugin directory and installs `tools/requirements.txt`.

You can also declare it in `settings.json` and let Pi install it on startup:

```json
{
  "packages": ["npm:stock-analysis-plugin"]
}
```

### From source

```bash
# Global: available in every session
cd ~/.pi/agent/extensions
# Or project-level: only in this project's sessions
# mkdir -p .pi/extensions && cd .pi/extensions

git clone https://github.com/Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

## Verify

Start Pi and type `/skills`. You should see 21 Skills, such as `stock-analysis`, `stock-screener` and `strategy-backtest`. Then ask:

```
Get the real-time quote for AAPL
```

The agent should call `get_quote` and return market data.

## On-demand tool discovery

Pi 1.5.2 supports native tool discovery. At session startup, the plugin checks for the native `tool_search` exposure metadata and active-tool APIs. When available, it defers its stock tools and activates search while preserving other active tools. Older hosts and SDK sessions without the native search extension keep all 73 tools directly exposed.

If search is registered but inactive, the plugin enables it and confirms activation before deferring stock tools. Missing, restricted, or unavailable search keeps the tools directly exposed. Disabling search during a session restores the stock tools before the next model request and removes the discovery guidance.

The plugin preserves Pi's restored active tools across reloads. If an SDK reload does not emit `session_start`, initialization runs before the next model request.

Search with exact tool names such as `get_quote`, `get_financials`, `screen_stocks`, and `run_backtest`, or with keywords: since Pi 1.5 the native ranker segments Chinese, so Chinese terms like `实时行情` match the tools' Chinese descriptions; older Pi only matches English terms. The plugin supplies this guidance to the model only when discovery is enabled. Loaded tools remain active for the session, so the active set can grow during long conversations.

## Optional configuration

Everything works without API keys. Environment variables for data sources, search, sentiment and SEC are listed in [README · 可选配置](../README.md#可选配置); set them in the shell that starts Pi:

```bash
export TAVILY_API_KEY="your-key"
pi
```

### Wisburg research data

The `wisburg-research` Skill searches research reports, earnings calls and filings through Wisburg's hosted MCP server. Without it, the Skill falls back to `search_comprehensive_intel`. Get an API key from [Wisburg](https://wisburg.com), then add the server:

```bash
export WISBURG_API_KEY="your-wisburg-key"
pi mcp add wisburg-mcp-server --url https://mcp.wisburg.com/mcp --bearer-token-env-var WISBURG_API_KEY
pi mcp list
```

Or write it to `~/.pi/agent/mcp.json`:

```json
{
  "mcpServers": {
    "wisburg-mcp-server": {
      "url": "https://mcp.wisburg.com/mcp",
      "headers": { "Authorization": "Bearer ${WISBURG_API_KEY}" }
    }
  }
}
```

Run `/reload` in the session afterwards and check the connection with `/mcp`.

## Troubleshooting

### Python dependency installation failed

If `npm install` could not build the Python environment, install it manually in the plugin directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements.txt          # macOS / Linux
# .venv\Scripts\python.exe -m pip install -r tools/requirements.txt  # Windows
```

### Tool call timeout

Tools such as full-market screening (`screen_stocks`) take longer. Lower `top` or check your network.

### Upgrading

```bash
# Installed via the Pi package manager
pi update --extension npm:stock-analysis-plugin   # add -a for a project-level install

# Installed from source
git pull && npm install
```

## npm package contents

```
stock-analysis-plugin/
├── pi/index.ts           # Pi extension entry: 73 tools and 21 Skills
├── tools/                # shared Python CLIs
├── skills/               # SKILL.md files
├── schemas/ templates/ strategies/
├── scripts/              # postinstall and subprocess helpers
├── hermes/ mcp_host/     # bundled Hermes adapter and MCP server
└── docs/mcp.md
```
