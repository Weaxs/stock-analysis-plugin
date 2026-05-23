# Hermes Agent 接入指南

本文介绍如何将 `stock-analysis-plugin` 作为 [Hermes Agent](https://hermes-agent.nousresearch.com) Plugin 使用。

## 前置条件

- Python >= 3.10
- Hermes Agent 已安装并可正常运行

## 安装方式

### 方式一：pip 安装（推荐）

```bash
pip install git+https://github.com/Weaxs/stock-analysis-plugin.git
```

安装后 Hermes 通过 `hermes_agent.plugins` entry point 自动发现插件，无需额外配置。

在 `~/.hermes/config.yaml` 中启用：

```yaml
plugins:
  enabled:
    - stock-analysis
```

### 方式二：Hermes CLI 安装

```bash
hermes plugins install Weaxs/stock-analysis-plugin --enable
```

### 方式三：项目级插件

```bash
# 需要设置 HERMES_ENABLE_PROJECT_PLUGINS=true
mkdir -p .hermes/plugins
git clone git@github.com:Weaxs/stock-analysis-plugin.git .hermes/plugins/stock-analysis
cd .hermes/plugins/stock-analysis
pip install -r tools/requirements.txt
```

### NixOS 声明式安装

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

## 验证加载

启动 Hermes Agent 后，使用 `/plugins` 查看已加载的插件列表，应能看到 `stock-analysis`。

也可以手动验证：

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
print(f"Tools: {len(ctx.tools)}")   # 应输出 31
print(f"Skills: {len(ctx.skills)}") # 应输出 20
```

## 工作原理

### Plugin 入口

Hermes 通过两种方式发现本插件：

1. **pip entry point**：`pyproject.toml` 声明了 `hermes_agent.plugins` entry point：

```toml
[project.entry-points."hermes_agent.plugins"]
stock-analysis = "hermes:register"
```

2. **目录发现**：`hermes/plugin.yaml` 作为插件清单，Hermes CLI 安装时使用。

### 工具注册

`hermes/__init__.py` 导出 `register(ctx)` 函数，接收 Hermes PluginContext：

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

### 工具定义（Schema）

`hermes/schemas.py` 定义了 31 个工具的 JSON Schema，每个工具包含 `name`、`description` 和 `parameters`：

```python
TOOL_SCHEMAS = [
    {
        "name": "get_kline",
        "description": "获取股票K线数据（OHLCV）...",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代码"},
                "period": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                "count": {"type": "number", "description": "返回条数"},
            },
            "required": ["symbol"],
        },
    },
    # ... 其余 30 个
]
```

### 工具实现（Handler）

`hermes/tools.py` 实现了 31 个 handler 函数，每个函数通过 `subprocess` 调用对应的 Python CLI 脚本：

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

### 路径解析

插件通过 `Path(__file__).resolve().parent.parent` 定位项目根目录，从而找到共享的 `tools/` 和 `skills/` 目录。pip 安装时，`hermes/` 和 `tools/` 作为顶层 Python 包一起安装到 site-packages。

## 环境变量配置

以下环境变量按功能分组，根据需要配置：

### 新闻 & 搜索（配置任一即可）

| 环境变量 | 用途 | 获取地址 |
|---------|------|---------|
| `TAVILY_API_KEY` | Tavily 搜索 | https://tavily.com |
| `BRAVE_API_KEY` | Brave 搜索 | https://brave.com/search/api/ |
| `SERPAPI_KEY` | SerpAPI | https://serpapi.com |
| `BOCHA_API_KEY` | Bocha AI 搜索 | — |

### 社交情绪

| 环境变量 | 用途 | 说明 |
|---------|------|------|
| `SENTIMENT_API_KEY` | 社交情绪分析 API 认证 | 东财股吧 + 雪球情绪 |
| `SENTIMENT_API_URL` | 情绪 API 地址 | 默认 `https://api.adanos.org` |

### 美股 / 港股数据源

| 环境变量 | 用途 | 说明 |
|---------|------|------|
| `FINNHUB_API_KEY` | Finnhub（美股行情、财报） | https://finnhub.io |
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage（美股K线） | https://www.alphavantage.co |
| `LONGBRIDGE_APP_KEY` | 长桥（港股行情） | https://open.longportapp.com |
| `LONGBRIDGE_APP_SECRET` | 长桥 App Secret | 同上 |
| `LONGBRIDGE_ACCESS_TOKEN` | 长桥 Access Token | 同上 |

### A 股增强数据源（可选）

| 环境变量 | 用途 | 说明 |
|---------|------|------|
| `TUSHARE_TOKEN` | Tushare（A股历史数据备用源） | https://tushare.pro |

> 注意：A 股基础数据通过 akshare 免费获取，无需配置。Tushare 仅作为备用/增强数据源。

### 智堡（Wisburg）投研数据

智堡通过 **MCP Server** 接入，不是环境变量。需要在 Hermes Agent 的 MCP 配置中添加智堡服务：

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

配置完成后，插件的 `wisburg-research` Skill 会自动调用智堡 MCP 工具（以 `mcp__wisburg-mcp-server__` 前缀），提供：
- 投行/券商研报、个股研究报告
- 电话会纪要、财报公告（A/港/美三市场）
- 投研资讯流、市场日报
- A股投资者问答（语义搜索）

在 Hermes Agent 中配置环境变量：

```bash
# 方式一：直接 export
export TAVILY_API_KEY="your-key-here"

# 方式二：在 .env 文件中配置
echo 'TAVILY_API_KEY=your-key-here' >> .env
```

## 常见问题

### ImportError: No module named 'hermes'

使用 pip 安装：

```bash
pip install git+https://github.com/Weaxs/stock-analysis-plugin.git
```

### Python 依赖缺失

pip 安装时核心依赖会自动安装。如果某些可选功能不可用，安装完整依赖：

```bash
pip install -r tools/requirements.txt
```

### A 股数据获取失败

底层使用 [akshare](https://github.com/akfamily/akshare)，可能因网络问题或版本过低导致失败：

```bash
pip install --upgrade akshare
```

### 升级插件

```bash
# pip 方式
pip install --upgrade git+https://github.com/Weaxs/stock-analysis-plugin.git

# Hermes CLI 方式
hermes plugins update stock-analysis

# Git clone 方式
cd /path/to/stock-analysis-plugin
git pull
pip install -r tools/requirements.txt
```

## pip 包内容

通过 pip 安装时，包内包含 Hermes Plugin 所需文件：

```
stock-analysis-plugin (wheel)
├── hermes/
│   ├── __init__.py       # register(ctx) 入口
│   ├── plugin.yaml       # 插件元数据清单
│   ├── schemas.py        # 31 个工具的 JSON Schema
│   └── tools.py          # 31 个 handler
└── tools/                # Python CLI 工具
```

> Pi 相关文件（`pi/`、`package.json`、`scripts/`）不会包含在 pip 包中。
