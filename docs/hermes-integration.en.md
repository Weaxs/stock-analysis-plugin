# Hermes Agent Integration Guide

This guide explains how to use `stock-analysis-plugin` as a [Hermes Agent](https://hermes-agent.nousresearch.com) Plugin.

## Prerequisites

- Python >= 3.10
- Hermes Agent installed and running

## Installation

### Option 1: pip Install (Recommended)

```bash
pip install git+https://github.com/Weaxs/stock-analysis-plugin.git
```

After installation, Hermes auto-discovers the plugin via the `hermes_agent.plugins` entry point. No manual config needed.

Enable it in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - stock-analysis
```

### Option 2: Hermes CLI

```bash
hermes plugins install Weaxs/stock-analysis-plugin --enable
```

### Option 3: Project-local Plugin

```bash
# Requires HERMES_ENABLE_PROJECT_PLUGINS=true
mkdir -p .hermes/plugins
git clone git@github.com:Weaxs/stock-analysis-plugin.git .hermes/plugins/stock-analysis
cd .hermes/plugins/stock-analysis
pip install -r tools/requirements.txt
```

### NixOS Declarative Installation

```nix
services.hermes-agent = {
  extraPlugins = [
    (pkgs.fetchFromGitHub {
      owner = "Weaxs";
      repo = "stock-analysis-plugin";
      rev = "v0.1.0";
      sha256 = "...";
    })
  ];
  settings.plugins.enabled = [ "stock-analysis" ];
};
```

## Verifying the Installation

After starting Hermes Agent, use `/plugins` to view loaded plugins — you should see `stock-analysis`.

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

Wisburg is integrated via **MCP Server**, not environment variables. Add the Wisburg MCP service to your Hermes Agent MCP configuration:

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

Configure in Hermes Agent:

```bash
# Option 1: Direct export
export TAVILY_API_KEY="your-key-here"

# Option 2: .env file in project root
echo 'TAVILY_API_KEY=your-key-here' >> .env
```

## Troubleshooting

### ImportError: No module named 'hermes'

Install via pip:

```bash
pip install git+https://github.com/Weaxs/stock-analysis-plugin.git
```

### Missing Python dependencies

Core dependencies are installed automatically with pip. For optional features, install the full requirements:

```bash
pip install -r tools/requirements.txt
```

### A-share data fetch failures

The underlying data source is [akshare](https://github.com/akfamily/akshare). Failures may occur due to network issues or outdated versions:

```bash
pip install --upgrade akshare
```

### Upgrading the plugin

```bash
# pip install
pip install --upgrade git+https://github.com/Weaxs/stock-analysis-plugin.git

# Hermes CLI
hermes plugins update stock-analysis

# Git clone
cd /path/to/stock-analysis-plugin
git pull
pip install -r tools/requirements.txt
```

## pip Package Contents

When installed via pip, the package contains only files needed for the Hermes Plugin:

```
stock-analysis-plugin (wheel)
├── hermes/
│   ├── __init__.py       # register(ctx) entry point
│   ├── plugin.yaml       # Plugin manifest
│   ├── schemas.py        # JSON Schema definitions for 71 tools
│   └── tools.py          # 71 handlers calling CLI via subprocess
└── tools/                # Python CLI tools
```

> Pi-related files (`pi/`, `package.json`, `scripts/`) are not included in the pip package.
