# Pi Agent Integration Guide

This guide explains how to use `stock-analysis-plugin` as a [Pi Agent](https://pi.dev) Extension.

## Prerequisites

- Node.js >= 22.19.0
- Python >= 3.10
- Pi Agent installed and running

## Installation

### Option 1: Pi Package Manager (Recommended)

```bash
pi install npm:@weaxs/stock-analysis-plugin
```

The installation automatically runs the `postinstall` script which:

1. Locates `python3` on your system (requires >= 3.10)
2. Creates a `.venv` virtual environment in the plugin directory
3. Installs Python dependencies from `tools/requirements.txt`

### Option 2: Global Extension (Git)

```bash
cd ~/.pi/agent/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

### Option 3: Project-level Extension

```bash
mkdir -p .pi/extensions && cd .pi/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

Project-level extensions are only available within that project's Pi Agent sessions.

### Option 4: settings.json Configuration

```json
{
  "packages": [
    "npm:@weaxs/stock-analysis-plugin@0.1.0"
  ]
}
```

## Verifying the Installation

Pi 1.4.0 supports native tool discovery. At session startup, the plugin checks for the native `tool_search` exposure metadata and active-tool APIs. When available, it defers its stock tools and activates search while preserving other active tools. Older hosts and SDK sessions without the native search extension keep all 71 tools directly exposed.

If search is registered but inactive, the plugin enables it and confirms activation before deferring stock tools. Missing, restricted, or unavailable search keeps the tools directly exposed. Disabling search during a session restores the stock tools before the next model request and removes the discovery guidance.

The plugin preserves Pi's restored active tools across reloads. If an SDK reload does not emit `session_start`, initialization runs before the next model request.

Use English keywords or exact tool names such as `get_quote`, `get_financials`, `screen_stocks`, and `run_backtest`: the current native ranker does not tokenize Chinese. The plugin supplies this guidance to the model only when discovery is enabled. Loaded tools remain active for the session, so the active set can grow during long conversations.

After starting Pi Agent, type:

```
/skills
```

You should see 21 registered Skills (e.g., `stock-analysis`, `stock-screener`, `strategy-backtest`, etc.).

You can also test by invoking a tool directly:

```
Get the real-time quote for AAPL
```

The agent should call the `get_quote` tool and return market data.

## Environment Variables

Environment variables are grouped by function — configure as needed:

### News & Search (configure at least one)

| Variable | Purpose | URL |
|----------|---------|-----|
| `TAVILY_API_KEY` | Tavily search | https://tavily.com |
| `BRAVE_API_KEY` | Brave search | https://brave.com/search/api/ |
| `SERPAPI_KEY` | SerpAPI | https://serpapi.com |
| `BOCHA_API_KEY` | Bocha AI search | — |

### Social Sentiment

| Variable | Purpose | Notes |
|----------|---------|-------|
| `SENTIMENT_API_KEY` | Social sentiment API auth | Eastmoney + Xueqiu sentiment |
| `SENTIMENT_API_URL` | Sentiment API endpoint | Default: `https://api.adanos.org` |

### US / HK Market Data Sources

| Variable | Purpose | Notes |
|----------|---------|-------|
| `FINNHUB_API_KEY` | Finnhub (US quotes, financials) | https://finnhub.io |
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage (US klines) | https://www.alphavantage.co |
| `LONGBRIDGE_APP_KEY` | Longbridge (HK quotes) | https://open.longportapp.com |
| `LONGBRIDGE_APP_SECRET` | Longbridge App Secret | Same as above |
| `LONGBRIDGE_ACCESS_TOKEN` | Longbridge Access Token | Same as above |

### A-share Enhanced Data Source (Optional)

| Variable | Purpose | Notes |
|----------|---------|-------|
| `TUSHARE_TOKEN` | Tushare (A-share fallback source) | https://tushare.pro |

> Note: A-share basic data is fetched via akshare (free, no key required). Tushare is only used as a fallback/enhanced source.

### Wisburg Research Data

Wisburg is integrated via **MCP Server**, not environment variables. Add the Wisburg MCP service to your Pi Agent MCP configuration:

```json
{
  "mcpServers": {
    "wisburg-mcp-server": {
      "command": "npx",
      "args": ["-y", "@anthropic/wisburg-mcp-server"],
      "env": {
        "WISBURG_API_KEY": "your-wisburg-key"
      }
    }
  }
}
```

Once configured, the `wisburg-research` Skill automatically calls Wisburg MCP tools (prefixed with `mcp__wisburg-mcp-server__`), providing:
- Institutional research reports (Goldman Sachs, Morgan Stanley, CICC, etc.)
- Earnings call transcripts, financial filings (A/HK/US markets)
- Research feed, market daily digest
- A-share investor Q&A (semantic search)

Set them before starting Pi Agent:

```bash
# Direct export
export TAVILY_API_KEY="your-key-here"

# Or via .env file
echo 'TAVILY_API_KEY=your-key-here' >> ~/.pi/.env
```

## Troubleshooting

### Python dependency installation failed

If `npm install` fails to set up the Python environment, install manually:

```bash
cd ~/.pi/agent/extensions/stock-analysis-plugin
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
```

### Tool call timeout

Some tools (e.g., `screen_stocks` for full-market screening) may take longer to execute. If you encounter timeouts, try reducing the `top` parameter or check your network connection.

### Upgrading the plugin

```bash
# Installed via Pi package manager:
pi install npm:@weaxs/stock-analysis-plugin@latest

# Installed via Git:
cd ~/.pi/agent/extensions/stock-analysis-plugin
git pull
npm install
```

## npm Package Contents

When installed via npm, the package only contains files needed for the Pi Extension:

```
@weaxs/stock-analysis-plugin/
├── pi/
│   └── index.ts          # Extension entry — registers tools and skills
├── tools/                # Python CLI tools
├── skills/               # SKILL.md files
├── schemas/              # Strategy schemas
├── strategies/           # Strategy examples
├── scripts/
│   └── setup-python.mjs  # postinstall script for Python setup
└── package.json          # pi.extensions + pi.skills config
```

> Hermes-related files (`hermes/`, `pyproject.toml`) are not included in the npm package.
