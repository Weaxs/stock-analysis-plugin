# Hermes Agent Integration Guide

This guide explains how to use `stock-analysis-plugin` as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) Plugin.

## Prerequisites

- Python >= 3.9
- Hermes Agent installed and running

## Installation

### Option 1: Symlink to Plugin Directory (Recommended)

```bash
git clone git@github.com:Weaxs/stock-analysis-plugin.git /path/to/stock-analysis-plugin
cd /path/to/stock-analysis-plugin
pip install -r tools/requirements.txt

# Symlink the hermes/ directory into Hermes plugin directory
ln -s /path/to/stock-analysis-plugin/hermes ~/.hermes/plugins/stock-analysis
```

### Option 2: Copy the hermes/ Directory

```bash
git clone git@github.com:Weaxs/stock-analysis-plugin.git /path/to/stock-analysis-plugin
cd /path/to/stock-analysis-plugin
pip install -r tools/requirements.txt

cp -r hermes ~/.hermes/plugins/stock-analysis
```

> Note: When copying, ensure that `hermes/__init__.py` can still resolve paths to the `tools/` and `skills/` directories. The symlink approach automatically maintains correct relative paths.

### Option 3: Register Directly in Code

If you're customizing the Hermes Agent startup flow, you can register directly:

```python
import sys
sys.path.insert(0, "/path/to/stock-analysis-plugin")

from hermes import register
register(ctx)
```

## Verifying the Installation

Test that the plugin loads correctly:

```python
from hermes import register

class MockCtx:
    def __init__(self):
        self.tools = []
        self.skills = []

    def register_tool(self, name, toolset, schema, handler):
        self.tools.append(name)

    def register_skill(self, name, path):
        self.skills.append(name)

ctx = MockCtx()
register(ctx)
print(f"Tools: {len(ctx.tools)}")   # Should print 31
print(f"Skills: {len(ctx.skills)}") # Should print 20
```

## How It Works

### Plugin Entry Point

`hermes/__init__.py` exports a `register(ctx)` function that receives the Hermes Agent context object:

```python
from pathlib import Path
from . import schemas, tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"

def register(ctx):
    # Register 31 tools
    for schema in schemas.TOOL_SCHEMAS:
        name = schema["name"]
        handler = _HANDLER_MAP[name]
        ctx.register_tool(
            name=name,
            toolset="stock-analysis",
            schema=schema,
            handler=handler,
        )

    # Register 20 skills
    for child in sorted(SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, str(skill_md))
```

### Tool Definitions (Schema)

`hermes/schemas.py` defines the JSON Schema for all 31 tools, each containing `name`, `description`, and `parameters`:

```python
TOOL_SCHEMAS = [
    {
        "name": "get_kline",
        "description": "Fetch stock OHLCV candlestick data...",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock symbol"},
                "period": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                "count": {"type": "number", "description": "Number of data points"},
            },
            "required": ["symbol"],
        },
    },
    # ... 30 more
]
```

### Tool Handlers

`hermes/tools.py` implements 31 handler functions, each calling the corresponding Python CLI script via `subprocess`:

```python
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"

def _find_python():
    """Prefer .venv, fall back to system python3"""
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return "python3"

def _run(script, args):
    """Execute a Python CLI script and return stdout"""
    python = _find_python()
    cmd = [python, str(TOOLS_DIR / script)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return result.stderr or f"Error: exit code {result.returncode}"
    return result.stdout

def get_kline(args, **kwargs):
    cmd = ["kline", args["symbol"]]
    if "period" in args:
        cmd += ["--period", args["period"]]
    if "count" in args:
        cmd += ["--count", str(args["count"])]
    return _run("stock_data.py", cmd)

# ... 30 more handlers
```

### Plugin Manifest

`hermes/plugin.yaml` declares plugin metadata and dependencies:

```yaml
name: stock-analysis
version: 0.1.0
description: "Stock analysis, screening, and strategy backtesting across A/HK/US markets"
provides_tools:
  - get_kline
  - get_quote
  # ... 31 tools total
requires_env:
  - name: TAVILY_API_KEY
    description: "Tavily search API key (optional)"
    secret: true
```

### Path Resolution

The plugin locates the project root via `Path(__file__).resolve().parent.parent`, which gives access to the shared `tools/` and `skills/` directories. This means:

- **Symlink installs**: `Path.resolve()` follows the symlink to the real path, automatically finding the correct project root
- **Copy installs**: The full project structure must exist at the expected relative location

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

# Option 3: .env in plugin directory
echo 'TAVILY_API_KEY=your-key-here' >> ~/.hermes/plugins/stock-analysis/.env
```

## Troubleshooting

### ImportError: No module named 'hermes'

Ensure the `stock-analysis-plugin` root directory is in your Python path:

```python
import sys
sys.path.insert(0, "/path/to/stock-analysis-plugin")
from hermes import register
```

Or ensure the symlinked/copied directory is named `hermes` and its parent is on `sys.path`.

### Missing Python dependencies

```bash
cd /path/to/stock-analysis-plugin
pip install -r tools/requirements.txt
```

If using a virtualenv, make sure Hermes Agent starts with the same environment activated.

### A-share data fetch failures

The underlying data source is [akshare](https://github.com/akfamily/akshare). Failures may occur due to network issues or outdated versions:

```bash
pip install --upgrade akshare
```

### Skills not registered

Check that the `skills/` directory is intact. With symlink installs, verify the link target is correct:

```bash
ls -la ~/.hermes/plugins/stock-analysis
# Should point to /path/to/stock-analysis-plugin/hermes

ls /path/to/stock-analysis-plugin/skills/
# Should contain 20 subdirectories
```

### Upgrading the plugin

```bash
cd /path/to/stock-analysis-plugin
git pull
pip install -r tools/requirements.txt  # Update dependencies
```

## Directory Structure (Hermes-related)

```
stock-analysis-plugin/
├── hermes/
│   ├── __init__.py       # register(ctx) entry point
│   ├── plugin.yaml       # Plugin manifest
│   ├── schemas.py        # JSON Schema definitions for 31 tools
│   └── tools.py          # 31 handlers calling CLI via subprocess
├── tools/                # Shared Python CLI tools
├── skills/               # Shared SKILL.md files
└── .venv/                # Python virtual environment (optional)
```
