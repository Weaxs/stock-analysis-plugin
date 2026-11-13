# Pi Agent 接入指南

把 `stock-analysis-plugin` 作为 [Pi Agent](https://pi.dev) Extension 使用。

## 前置条件

- Node.js >= 22.19.0
- Python >= 3.10
- Pi Agent 已安装并可正常运行

## 安装

### 推荐：Pi 包管理

```bash
pi install npm:stock-analysis-plugin
```

默认装到全局（`~/.pi/agent/npm/`），加 `-l` 只装到当前项目（写入 `.pi/settings.json`）。同一份包也以 `@weaxs/stock-analysis-plugin` 发布，内容一致。

安装时 `postinstall` 会找到本机的 `python3`（Windows 为 `python`，需 >= 3.10），在插件目录下创建 `.venv` 并安装 `tools/requirements.txt` 中的依赖。

也可以在 `settings.json` 中声明，Pi 启动时自动安装：

```json
{
  "packages": ["npm:stock-analysis-plugin"]
}
```

### 从源码安装

```bash
# 全局：所有会话可用
cd ~/.pi/agent/extensions
# 或项目级：只在当前项目的会话中可用
# mkdir -p .pi/extensions && cd .pi/extensions

git clone https://github.com/Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

## 验证

启动 Pi 后输入 `/skills`，应能看到 21 个 Skill（如 `stock-analysis`、`stock-screener`、`strategy-backtest`）。再问一句：

```
帮我查一下贵州茅台的实时行情
```

Agent 应调用 `get_quote` 并返回行情。

## 按需工具发现

最新稳定版 Pi 1.5.2 支持原生按需工具发现。插件在 `session_start` 检查 `tool_search` 的 exposure 元数据及活动工具 API：具备这些能力时，将股票工具设为 `deferred` 并启用原生搜索，保留其他插件的活动工具；旧宿主保持原来的 73 工具全量暴露，无需升级才能继续使用。

新版中搜索已注册但未激活时，插件会尝试启用并确认成功后再隐藏股票工具；未加载搜索扩展、被宿主限制或无法启用时保持全量暴露。会话中途关闭搜索时，下次模型请求前恢复全部股票工具，并停止发送搜索指引。

插件保留 Pi 已恢复的活动工具集合，重载后不清除已发现工具；SDK 重载未触发 `session_start` 时，会在下次模型请求前完成初始化。

Pi 1.5 起搜索器支持中文分词，可直接用中文关键词（如 `实时行情`、`财务`）或确切工具名搜索，例如 `get_quote`、`get_financials`、`screen_stocks`、`run_backtest`；更早的 Pi 只能匹配英文关键词。插件会在启用发现时向模型提供这条说明。SDK 若未加载原生工具搜索扩展，也会保留全量暴露。命中工具加载后留在当前会话活动集合中，长会话的集合会逐渐扩大。

## 可选配置

不配置任何 API Key 也能使用。数据源、搜索、舆情和 SEC 相关的环境变量见 [README · 可选配置](../README.md#可选配置)，在启动 Pi 的 shell 中设置即可：

```bash
export TAVILY_API_KEY="your-key"
pi
```

### 智堡（Wisburg）投研数据

`wisburg-research` Skill 使用智堡官方的远程 MCP 服务检索研报、电话会纪要和财报公告；没配置时 Skill 会改用 `search_comprehensive_intel`。在 [智堡](https://wisburg.com) 获取 API Key 后添加：

```bash
export WISBURG_API_KEY="your-wisburg-key"
pi mcp add wisburg-mcp-server --url https://mcp.wisburg.com/mcp --bearer-token-env-var WISBURG_API_KEY
pi mcp list
```

也可以直接写入 `~/.pi/agent/mcp.json`：

```json
{
  "mcpServers": {
    "wisburg-mcp-server": {
      "url": "https://mcp.wisburg.com/mcp",
      "headers": { "Authorization": "Bearer ${WISBURG_API_KEY}" }
    }
  }
}
```

修改后在会话中运行 `/reload`，用 `/mcp` 查看连接状态。

## 常见问题

### Python 依赖安装失败

`npm install` 时 Python 环境没建起来，可以在插件目录下手动安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements.txt          # macOS / Linux
# .venv\Scripts\python.exe -m pip install -r tools/requirements.txt  # Windows
```

### 工具调用超时

全市场筛选（`screen_stocks`）等工具耗时较长。可以减少 `top` 参数，或检查网络连接。

### 升级

```bash
# Pi 包管理安装的
pi update --extension npm:stock-analysis-plugin   # 项目级安装再加 -a

# 源码安装的
git pull && npm install
```

## npm 包内容

```
stock-analysis-plugin/
├── pi/index.ts           # Pi Extension 入口，注册 73 个工具和 21 个 Skill
├── tools/                # 共享 Python CLI
├── skills/               # SKILL.md
├── schemas/ templates/ strategies/
├── scripts/              # postinstall 与子进程辅助脚本
├── hermes/ mcp_host/     # 同包附带的 Hermes 适配器与 MCP 服务器
└── docs/mcp.md
```
