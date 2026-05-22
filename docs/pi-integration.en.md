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

`pi/index.ts` exports a function that receives the `ExtensionAPI` object and registers 30 tools via `pi.registerTool()`:

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
  // ... 29 more tools
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

The following environment variables are optional (configure at least one search engine to enable news search):

| Variable | Purpose |
|----------|---------|
| `TAVILY_API_KEY` | Tavily search |
| `BRAVE_API_KEY` | Brave search |
| `SERPAPI_KEY` | SerpAPI |
| `BOCHA_API_KEY` | Bocha AI search |
| `SENTIMENT_API_KEY` | Social media sentiment analysis |

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
