# Hermes Agent Integration Guide

Use `stock-analysis-plugin` as a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin.

## Prerequisites

- Python >= 3.10
- Hermes Agent installed and working

## Installation

### Recommended: pip

Install into the Python environment Hermes runs in:

```bash
pip install stock-analysis-plugin
```

Hermes discovers the plugin through the `hermes_agent.plugins` entry point. Enable it in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - stock-analysis
```

For the development version: `pip install git+https://github.com/Weaxs/stock-analysis-plugin.git`.

### Hermes CLI

```bash
hermes plugins install Weaxs/stock-analysis-plugin --enable
```

### Project-local plugin

```bash
# Requires HERMES_ENABLE_PROJECT_PLUGINS=true
mkdir -p .hermes/plugins
git clone https://github.com/Weaxs/stock-analysis-plugin.git .hermes/plugins/stock-analysis
pip install -r .hermes/plugins/stock-analysis/tools/requirements.txt
```

### NixOS declarative installation

```nix
services.hermes-agent = {
  extraPlugins = [
    (pkgs.fetchFromGitHub {
      owner = "Weaxs";
      repo = "stock-analysis-plugin";
      rev = "v<version>";
      sha256 = "...";
    })
  ];
  settings.plugins.enabled = [ "stock-analysis" ];
};
```

## Verify

Start Hermes and run `/plugins`; `stock-analysis` should be listed. Then ask:

```
Get the real-time quote for AAPL
```

The agent should call `get_quote` and return market data.

## Optional configuration

Everything works without API keys. Environment variables for data sources, search, sentiment and SEC are listed in [README · 可选配置](../README.md#可选配置). Export them before starting Hermes, or add them to `~/.hermes/.env`:

```bash
echo 'TAVILY_API_KEY=your-key' >> ~/.hermes/.env
```

### Wisburg research data

The `wisburg-research` Skill searches research reports, earnings calls and filings through Wisburg's hosted MCP server. Without it, the Skill falls back to `search_comprehensive_intel`. Get an API key from [Wisburg](https://wisburg.com), add it to `~/.hermes/.env`, then add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  wisburg-mcp-server:
    url: "https://mcp.wisburg.com/mcp"
    headers:
      Authorization: "Bearer ${WISBURG_API_KEY}"
```

Restart Hermes or run `/reload-mcp` afterwards.

## Troubleshooting

### ImportError: No module named 'hermes'

The plugin is not installed in the Python environment Hermes uses. Reinstall with that environment's Python:

```bash
python -m pip install stock-analysis-plugin
```

### A-share data fetch failures

The A-share data comes from [akshare](https://github.com/akfamily/akshare); failures are usually network issues or an outdated version:

```bash
python -m pip install --upgrade akshare
```

### Upgrading

```bash
# Installed via pip
python -m pip install --upgrade stock-analysis-plugin

# Installed via the Hermes CLI
hermes plugins update stock-analysis

# Installed from source
git pull && pip install -r tools/requirements.txt
```

## pip package contents

```
stock-analysis-plugin (wheel)
├── hermes/
│   ├── __init__.py       # register(ctx) entry point
│   ├── plugin.yaml       # plugin manifest
│   ├── schemas.py        # JSON Schemas for 73 tools
│   ├── tools.py          # 73 handlers that run the CLIs as subprocesses
│   └── _payload/         # tools/, skills/, schemas/, templates/
└── mcp_host/             # MCP stdio server (stock-analysis-mcp)
```
