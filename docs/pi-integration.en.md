# Pi Agent Integration Guide

This guide explains how to use `stock-analysis-plugin` as a [Pi Agent](https://github.com/anthropics/pi-agent) Extension.

## Prerequisites

- Node.js >= 18
- Python >= 3.9
- Pi Agent installed and running

## Installation

### Option 1: Global Extension (Recommended)

```bash
cd ~/.pi/agent/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

`npm install` automatically runs the `postinstall` script which:

1. Locates `python3` on your system (requires >= 3.9)
2. Creates a `.venv` virtual environment in the plugin directory
3. Installs Python dependencies from `tools/requirements.txt`

### Option 2: Project-level Extension

```bash
mkdir -p .pi/extensions && cd .pi/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

Project-level extensions are only available within that project's Pi Agent sessions.

## Verifying the Installation

After starting Pi Agent, type:

```
/skills
```

You should see 20 registered Skills (e.g., `stock-analysis`, `stock-screener`, `strategy-backtest`, etc.).

You can also test by invoking a tool directly:

```
Get the real-time quote for AAPL
```

The agent should call the `get_quote` tool and return market data.

## How It Works

### Extension Entry Point

Pi Agent locates the entry file via the `pi.extensions` field in `package.json`:

```json
{
  "pi": {
    "extensions": ["./pi/index.ts"]
  }
}
```

Pi Agent loads TypeScript extensions using [jiti](https://github.com/nicolo-ribaudo/jiti) — no build step required.

### Tool Registration

`pi/index.ts` exports a function that receives the `ExtensionAPI` object and registers 31 tools via `pi.registerTool()`:

```typescript
import type { ExtensionAPI } from "pi-agent";

export default (pi: ExtensionAPI) => {
  pi.registerTool({
    name: "get_kline",
    description: "Fetch stock OHLCV candlestick data...",
    parameters: {
      type: "object",
      properties: { /* ... */ },
      required: ["symbol"],
    },
    async execute({ symbol, period = "daily", count = 60 }) {
      const result = await pi.exec(`${python} ${toolsDir}/stock_data.py kline ${symbol} --period ${period} --count ${count}`);
      return result.stdout;
    },
  });
  // ... 30 more tools
};
```

Each tool's `execute` method calls the corresponding Python CLI script via `pi.exec()`, passing arguments on the command line and returning JSON from stdout.

### Skill Registration

Skills are registered via the `resources_discover` event:

```typescript
pi.on("resources_discover", () => ({
  skillPaths: [
    `${__dirname}/../skills/stock-analysis/SKILL.md`,
    `${__dirname}/../skills/stock-screener/SKILL.md`,
    // ...
  ],
}));
```

The `pi.skills` field in `package.json` also declares the skill list for static discovery:

```json
{
  "pi": {
    "skills": [
      "./skills/stock-analysis/SKILL.md",
      "./skills/stock-screener/SKILL.md"
    ]
  }
}
```

### Python Environment

At runtime, the extension prefers the local `.venv/bin/python3`, falling back to system `python3`:

```typescript
const venvPython = `${__dirname}/../.venv/bin/python3`;
const python = existsSync(venvPython) ? venvPython : "python3";
```

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
cd ~/.pi/agent/extensions/stock-analysis-plugin
git pull
npm install  # Re-installs Python dependencies
```

## Directory Structure (Pi-related)

```
stock-analysis-plugin/
├── pi/
│   └── index.ts          # Extension entry — registers tools and skills
├── tools/                # Shared Python CLI tools
├── skills/               # Shared SKILL.md files
├── scripts/
│   └── setup-python.mjs  # postinstall script for Python setup
├── package.json          # pi.extensions + pi.skills config
└── .venv/                # Auto-created Python virtual environment
```
