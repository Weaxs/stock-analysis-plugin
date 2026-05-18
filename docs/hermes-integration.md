# Hermes Agent 接入指南

本文介绍如何将 `stock-analysis-plugin` 作为 [Hermes Agent](https://github.com/NousResearch/hermes-agent) Plugin 使用。

## 前置条件

- Python >= 3.9
- Hermes Agent 已安装并可正常运行

## 安装方式

### 方式一：软链接到插件目录（推荐）

```bash
git clone git@github.com:Weaxs/stock-analysis-plugin.git /path/to/stock-analysis-plugin
cd /path/to/stock-analysis-plugin
pip install -r tools/requirements.txt

# 软链接 hermes/ 目录到 Hermes 插件目录
ln -s /path/to/stock-analysis-plugin/hermes ~/.hermes/plugins/stock-analysis
```

### 方式二：复制 hermes/ 目录

```bash
git clone git@github.com:Weaxs/stock-analysis-plugin.git /path/to/stock-analysis-plugin
cd /path/to/stock-analysis-plugin
pip install -r tools/requirements.txt

cp -r hermes ~/.hermes/plugins/stock-analysis
```

> 注意：复制方式需要确保 `hermes/__init__.py` 中的路径能正确定位到 `tools/` 和 `skills/` 目录。软链接方式自动保持正确的相对路径关系。

### 方式三：代码中直接注册

如果你在定制 Hermes Agent 的启动流程，可以直接在代码中注册：

```python
import sys
sys.path.insert(0, "/path/to/stock-analysis-plugin")

from hermes import register
register(ctx)
```

## 验证加载

启动 Hermes Agent 后验证插件已加载：

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
print(f"Tools: {len(ctx.tools)}")   # 应输出 27
print(f"Skills: {len(ctx.skills)}") # 应输出 15
```

## 工作原理

### Plugin 入口

`hermes/__init__.py` 导出 `register(ctx)` 函数，该函数接收 Hermes Agent 的上下文对象：

```python
from pathlib import Path
from . import schemas, tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"

def register(ctx):
    # 注册 27 个工具
    for schema in schemas.TOOL_SCHEMAS:
        name = schema["name"]
        handler = _HANDLER_MAP[name]
        ctx.register_tool(
            name=name,
            toolset="stock-analysis",
            schema=schema,
            handler=handler,
        )

    # 注册 15 个 Skill
    for child in sorted(SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, str(skill_md))
```

### 工具定义（Schema）

`hermes/schemas.py` 定义了 27 个工具的 JSON Schema，每个工具包含 `name`、`description` 和 `parameters`：

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
    # ... 其余 26 个
]
```

### 工具实现（Handler）

`hermes/tools.py` 实现了 27 个 handler 函数，每个函数通过 `subprocess` 调用对应的 Python CLI 脚本：

```python
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"

def _find_python():
    """优先使用 .venv，fallback 到系统 python3"""
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return "python3"

def _run(script, args):
    """执行 Python CLI 脚本并返回 stdout"""
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

# ... 其余 26 个 handler
```

### Plugin Manifest

`hermes/plugin.yaml` 声明插件元数据和依赖：

```yaml
name: stock-analysis
version: 0.1.0
description: "Stock analysis, screening, and strategy backtesting across A/HK/US markets"
provides_tools:
  - get_kline
  - get_quote
  # ... 27 个工具
requires_env:
  - name: TAVILY_API_KEY
    description: "Tavily search API key (optional)"
    secret: true
```

### 路径解析

插件通过 `Path(__file__).resolve().parent.parent` 定位项目根目录，从而找到共享的 `tools/` 和 `skills/` 目录。这意味着：

- 使用软链接安装时，`Path.resolve()` 会解析真实路径，自动找到正确的项目根
- 直接复制 `hermes/` 目录时，需确保完整的项目结构存在

## 环境变量配置

以下环境变量可选配置（配置任一搜索引擎即可启用新闻搜索功能）：

| 环境变量 | 用途 |
|---------|------|
| `TAVILY_API_KEY` | Tavily 搜索 |
| `BRAVE_API_KEY` | Brave 搜索 |
| `SERPAPI_KEY` | SerpAPI |
| `BOCHA_API_KEY` | Bocha AI 搜索 |
| `SENTIMENT_API_KEY` | 社交媒体情绪分析 |

在 Hermes Agent 中配置环境变量：

```bash
# 方式一：直接 export
export TAVILY_API_KEY="your-key-here"

# 方式二：在 .env 文件中配置
echo 'TAVILY_API_KEY=your-key-here' >> .env

# 方式三：在 plugin.yaml 所在目录创建 .env
echo 'TAVILY_API_KEY=your-key-here' >> ~/.hermes/plugins/stock-analysis/.env
```

## 常见问题

### ImportError: No module named 'hermes'

确保 `stock-analysis-plugin` 的根目录在 Python 路径中：

```python
import sys
sys.path.insert(0, "/path/to/stock-analysis-plugin")
from hermes import register
```

或者确保软链接/复制的目录名为 `hermes`，且其父目录在 `sys.path` 中。

### Python 依赖缺失

```bash
cd /path/to/stock-analysis-plugin
pip install -r tools/requirements.txt
```

如果使用 virtualenv，确保 Hermes Agent 启动时激活了同一环境。

### A 股数据获取失败

底层使用 [akshare](https://github.com/akfamily/akshare)，可能因网络问题或版本过低导致失败：

```bash
pip install --upgrade akshare
```

### Skill 未注册

检查 `skills/` 目录是否完整。软链接方式下，确认链接目标路径正确：

```bash
ls -la ~/.hermes/plugins/stock-analysis
# 应指向 /path/to/stock-analysis-plugin/hermes

ls /path/to/stock-analysis-plugin/skills/
# 应包含 15 个子目录
```

### 升级插件

```bash
cd /path/to/stock-analysis-plugin
git pull
pip install -r tools/requirements.txt  # 更新依赖
```

## 目录结构（Hermes 相关）

```
stock-analysis-plugin/
├── hermes/
│   ├── __init__.py       # register(ctx) 入口
│   ├── plugin.yaml       # 插件元数据清单
│   ├── schemas.py        # 27 个工具的 JSON Schema 定义
│   └── tools.py          # 27 个 handler，subprocess 调 CLI
├── tools/                # 共享 Python CLI 工具
├── skills/               # 共享 SKILL.md
└── .venv/                # Python 虚拟环境（可选）
```
