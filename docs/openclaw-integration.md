# OpenClaw 接入指南

本文介绍如何将 `stock-analysis-plugin` 作为 [OpenClaw](https://docs.openclaw.ai/) Plugin 使用。

## 前置条件

- Node.js >= 22.19（OpenClaw 官方要求）
- Python >= 3.10
- OpenClaw Gateway 已安装并可正常运行
- 如需通过 ClawHub 安装：`npm i -g clawhub` 并完成 `clawhub login`

## 安装方式

### 方式一：通过 ClawHub 安装（推荐）

```bash
openclaw plugins install clawhub:@weaxs/openclaw-stock-analysis
```

`clawhub:` 前缀强制走 ClawHub registry。安装时 npm 的 `postinstall` 会自动执行 `scripts/setup-python.mjs`：

1. 查找系统中的 `python3`（需 >= 3.10）
2. 在插件目录下创建 `.venv` 虚拟环境
3. 安装 `tools/requirements.txt` 中的 Python 依赖

### 方式二：通过 npm 直接安装

```bash
openclaw plugins install @weaxs/openclaw-stock-analysis
```

OpenClaw 文档说明 bare specs（不带 `clawhub:` 前缀）会回落到 npm registry。在 ClawHub 还未完全收敛之前，这是更稳的路径。

### 方式三：源码 / 内置 plugin

如果你在 OpenClaw 的 monorepo 内开发：

```bash
git clone git@github.com:Weaxs/stock-analysis-plugin.git extensions/stock-analysis
cd extensions/stock-analysis/openclaw
npm install
```

OpenClaw Gateway 启动时会扫描 `extensions/*` 工作区包并加载。

## 验证加载

```bash
openclaw plugins inspect stock-analysis --runtime --json
```

应该看到 71 个 tool 全部出现在 `tools` 列表里。也可以直接在 OpenClaw 对话中调用：

```
帮我查一下贵州茅台的实时行情
```

OpenClaw 应会调用 `get_quote` 工具并返回行情数据。

## 环境变量配置

跟 Pi/Hermes 共用同一套数据源凭据，环境变量在 OpenClaw 启动时注入即可。

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
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage（美股 K 线） | https://www.alphavantage.co |
| `LONGBRIDGE_APP_KEY` | 长桥（港股行情） | https://open.longportapp.com |
| `LONGBRIDGE_APP_SECRET` | 长桥 App Secret | 同上 |
| `LONGBRIDGE_ACCESS_TOKEN` | 长桥 Access Token | 同上 |

### A 股增强数据源（可选）

| 环境变量 | 用途 | 说明 |
|---------|------|------|
| `TUSHARE_TOKEN` | Tushare（A 股历史数据备用源） | https://tushare.pro |

> A 股基础数据通过 akshare 免费获取，无需配置。Tushare 仅作为备用/增强数据源。

## 常见问题

### `openclaw plugins inspect` 报 plugin not found

检查两点：

1. `openclaw.plugin.json` 的 `id` 字段是否就是你 `inspect` 时传的名字（本插件是 `stock-analysis`）
2. 包名（`@weaxs/openclaw-stock-analysis`）跟 plugin id 是两回事；`inspect` 用的是 plugin id

### Python 依赖安装失败

如果 `postinstall` 时 Python 环境创建失败（常见于 CI 镜像缺 `python3`），手动恢复：

```bash
cd <plugin-install-dir>
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
```

OpenClaw plugin 装在哪里取决于 Gateway 的安装策略——通常是 `~/.openclaw/plugins/<id>/` 或工作区下，可用 `openclaw plugins inspect stock-analysis --runtime --json` 查 `installPath` 字段。

### `pluginApi` 版本不兼容

如果 Gateway 报 `pluginApi` 版本不满足，对比 Gateway 自身版本和插件 `package.json` 的 `openclaw.compat.pluginApi`。本插件 pin 在 `>=2026.3.24-beta.2`——如果你的 Gateway 比这老，要么升级 Gateway，要么 fork 后下调 pin。

### 工具调用超时

部分工具（如 `screen_stocks` 全市场筛选）可能需要较长时间。如遇超时，可尝试缩小筛选范围（减少 `top` 参数）或检查网络连接。Gateway 默认的 tool timeout 也可能需要调整，参考 OpenClaw 的 `tools.timeout` 配置。

### 升级插件

```bash
# ClawHub 安装的：
openclaw plugins install clawhub:@weaxs/openclaw-stock-analysis@latest

# npm 安装的：
openclaw plugins install @weaxs/openclaw-stock-analysis@latest
```

## npm 包内容

通过 npm / ClawHub 安装时，包内仅包含 OpenClaw plugin 所需文件：

```
@weaxs/openclaw-stock-analysis/
├── index.ts                # plugin entry
├── openclaw.plugin.json    # contracts manifest
├── package.json            # 含 openclaw 块
├── tools/                  # Python CLI 工具
├── skills/                 # SKILL.md
├── schemas/                # 策略 Schema
├── strategies/             # 策略示例
├── scripts/
│   └── setup-python.mjs    # postinstall 自动安装 Python 依赖
└── README.md
```

> Pi（`pi/`）和 Hermes（`hermes/`、`pyproject.toml`）相关文件不会包含在 OpenClaw 包中——三个 Host 各自独立分发。
