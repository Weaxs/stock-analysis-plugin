# OpenClaw 接入指南

本文介绍如何将 `stock-analysis-plugin` 作为 [OpenClaw](https://docs.openclaw.ai/) Plugin 使用。

## 前置条件

- Node.js >= 24.16.0 < 25（或 >= 26.1.0；OpenClaw 官方要求）
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

应该看到 73 个 tool 全部出现在 `tools` 列表里。也可以直接在 OpenClaw 对话中调用：

```
帮我查一下贵州茅台的实时行情
```

OpenClaw 应会调用 `get_quote` 工具并返回行情数据。

## 可选配置

不配置任何 API Key 也能使用。数据源、搜索、舆情和 SEC 相关的环境变量与 Pi/Hermes 共用，见 [README · 可选配置](../README.md#可选配置)，在 OpenClaw Gateway 启动时注入即可。

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
.venv/bin/python -m pip install -r tools/requirements.txt          # macOS / Linux
# .venv\Scripts\python.exe -m pip install -r tools/requirements.txt  # Windows
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
├── index.ts                # plugin 源码入口
├── dist/                   # esbuild 打包产物
├── openclaw.plugin.json    # contracts manifest
├── package.json            # 含 openclaw 块
├── tools/                  # Python CLI 工具
├── skills/                 # SKILL.md
├── schemas/ templates/     # 结构定义与报告模板
├── strategies/             # 策略示例
├── scripts/                # postinstall 与子进程辅助脚本
└── README.md
```

> Pi（`pi/`）、Hermes（`hermes/`）和 MCP（`mcp_host/`）的适配器不包含在 OpenClaw 包中。
