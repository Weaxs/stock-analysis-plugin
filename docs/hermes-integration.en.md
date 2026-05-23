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

You can also verify programmatically:

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

### Plugin Discovery

Hermes discovers this plugin through two mechanisms:

1. **pip entry point**: `pyproject.toml` declares the `hermes_agent.plugins` entry point:

```toml
[project.entry-points."hermes_agent.plugins"]
stock-analysis = "hermes:register"
```

2. **Directory discovery**: `hermes/plugin.yaml` serves as the plugin manifest for Hermes CLI installs.

### Tool Registration

`hermes/__init__.py` exports a `register(ctx)` function that receives the Hermes PluginContext:

```python
from pathlib import Path
from . import schemas, tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"

def register(ctx):
    for schema in schemas.TOOL_SCHEMAS:
        name = schema["name"]
        handler = _HANDLER_MAP[name]
        ctx.register_tool(
            name=name,
            toolset="stock-analysis",
            schema=schema,
            handler=handler,
        )

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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"

def _find_python():
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return "python3"

def _run(script, args):
    python = _find_python()
    cmd = f"{python} {TOOLS_DIR / script} {args}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    return result.stdout
```

### Path Resolution

The plugin locates the project root via `Path(__file__).resolve().parent.parent`. When installed via pip, both `hermes/` and `tools/` are installed as top-level packages in site-packages, maintaining the expected relative path structure.

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
│   ├── schemas.py        # JSON Schema definitions for 31 tools
│   └── tools.py          # 31 handlers calling CLI via subprocess
└── tools/                # Python CLI tools
```

> Pi-related files (`pi/`, `package.json`, `scripts/`) are not included in the pip package.
