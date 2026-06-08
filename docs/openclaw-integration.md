# OpenClaw 接入指南

本文介绍如何将 `stock-analysis-plugin` 作为 [OpenClaw](https://docs.openclaw.ai/) Plugin 使用。

## 前置条件

- Node.js >= 22.19（OpenClaw 官方要求）
- Python >= 3.9
- OpenClaw Gateway 已安装并可正常运行
- 如需通过 ClawHub 安装：`npm i -g clawhub` 并完成 `clawhub login`

## 安装方式

### 方式一：通过 ClawHub 安装（推荐）

```bash
openclaw plugins install clawhub:weaxs/stock-analysis
```

`clawhub:` 前缀强制走 ClawHub registry。安装时 npm 的 `postinstall` 会自动执行 `scripts/setup-python.mjs`：

1. 查找系统中的 `python3`（需 >= 3.9）
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

应该看到 31 个 tool 全部出现在 `tools` 列表里。也可以直接在 OpenClaw 对话中调用：

```
帮我查一下贵州茅台的实时行情
```

OpenClaw 应会调用 `get_quote` 工具并返回行情数据。

## 工作原理

### 双 Manifest

OpenClaw 要求 plugin 同时提供 `package.json`（含 `openclaw` 块）和 `openclaw.plugin.json`（contracts manifest）。前者声明 SDK 兼容版本，后者声明 plugin 暴露的 contracts。

`package.json` 的 `openclaw` 块：

```json
{
  "openclaw": {
    "extensions": ["./index.ts"],
    "compat": {
      "pluginApi": ">=2026.3.24-beta.2",
      "minGatewayVersion": "2026.3.24-beta.2"
    },
    "build": {
      "openclawVersion": "2026.3.24-beta.2",
      "pluginSdkVersion": "2026.3.24-beta.2"
    }
  }
}
```

`openclaw.plugin.json`：

```json
{
  "id": "stock-analysis",
  "name": "Stock Analysis",
  "description": "...",
  "contracts": {
    "tools": ["get_kline", "get_quote", "..."]
  },
  "activation": { "onStartup": true },
  "configSchema": { "type": "object", "additionalProperties": false }
}
```

`contracts.tools` 必须列出所有 tool 名，OpenClaw 据此做 ownership 索引——Gateway 启动时无需加载 plugin runtime 就能知道某个 tool 由哪个 plugin 提供。代码里 `api.registerTool({ name })` 的 `name` 必须跟 manifest 里的字符串一一对齐。

### 工具注册

`openclaw/index.ts` 用 `definePluginEntry` 暴露 plugin，`register(api)` 里对每个 tool 调一次 `api.registerTool`：

```typescript
import { Type } from "typebox";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

export default definePluginEntry({
  id: "stock-analysis",
  name: "Stock Analysis",
  description: "...",
  register(api) {
    api.registerTool({
      name: "get_kline",
      description: "获取股票K线数据...",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        period: Type.Optional(Type.Union([
          Type.Literal("daily"),
          Type.Literal("weekly"),
          Type.Literal("monthly"),
        ])),
        count: Type.Optional(Type.Number()),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "kline", params.symbol,
          "--period", params.period ?? "daily",
          "--count", String(params.count ?? 60),
        ]);
        return { content: [{ type: "text", text: out }] };
      },
    });
    // ... 其余 30 个工具
  },
});
```

跟 Pi/Hermes 不同的两点：

1. **参数 schema 用 [TypeBox](https://github.com/sinclairzx81/typebox)**：`Type.Object`、`Type.String`、`Type.Optional`、`Type.Union` + `Type.Literal`。不是 JSON Schema 字面量。
2. **`execute` 必须返回 `{ content: [{ type: "text", text: string }] }`**：不是直接返回字符串。OpenClaw 用结构化 content 数组以便后续支持图像、附件等其它 part 类型。

### Python 环境

跟 Pi 完全一致——优先使用插件目录下的 `.venv/bin/python3`，不存在则 fallback 到系统 `python3`：

```typescript
const here = dirname(fileURLToPath(import.meta.url));
const toolsDir = existsSync(join(here, "tools"))
  ? join(here, "tools")     // 已发布形态
  : join(here, "..", "tools"); // 仓库内开发形态
const repoRoot = dirname(toolsDir);
const venvPython = join(repoRoot, ".venv", isWin ? "Scripts" : "bin", "python3");
```

每个工具的 `execute` 用 Node 的 `child_process.execFile` 调对应的 `tools/*.py`，参数走 argv，结果走 stdout JSON。

### 可选工具

OpenClaw 支持把工具标记为 optional——用户必须在 settings 里 `tools.allow` 显式开启才会装载。本插件目前所有 31 个 tool 都是默认可用的（required）。如果你想给某些重型工具（例如 `screen_stocks` 全市场筛选）加上 opt-in，方法是：

1. `openclaw.plugin.json` 加 `toolMetadata`：

```json
{
  "contracts": { "tools": ["screen_stocks"] },
  "toolMetadata": {
    "screen_stocks": { "optional": true }
  }
}
```

2. `index.ts` 注册时第二参传 `{ optional: true }`：

```typescript
api.registerTool({ name: "screen_stocks", ... }, { optional: true });
```

两处必须保持一致，否则 Gateway 会拒绝加载。

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
openclaw plugins install clawhub:weaxs/stock-analysis@latest

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
