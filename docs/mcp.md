# MCP 接入指南（WorkBuddy / Claude Desktop / Cursor）

`mcp_host/server.py` 是本插件的 MCP stdio 服务器：一行一条 JSON-RPC 2.0 消息，只用 Python 标准库。它复用 Hermes 适配器的 JSON Schema（`hermes/schemas.py`）和参数映射（`hermes/tools.py`），所以 73 个工具的名称、参数和输出与其他宿主一致。

## 前置条件

- Python >= 3.10
- 克隆仓库并执行 `npm install`，postinstall 会建好仓库内的 `.venv` 并安装 `tools/requirements.txt`
- 用 `.venv` 里的解释器启动服务器（下面的示例都用绝对路径）。通过 PyPI 安装时也可以直接运行 `stock-analysis-mcp` 或 `python -m mcp_host`：
  - macOS / Linux：`/path/to/stock-analysis-plugin/.venv/bin/python3`
  - Windows：`C:\\path\\to\\stock-analysis-plugin\\.venv\\Scripts\\python.exe`

数据源和搜索凭据（`TUSHARE_TOKEN`、`TAVILY_API_KEY`、`SEC_USER_AGENT` 等）只通过环境变量传入，写在配置的 `env` 字段里。没配置的数据源会自动跳过。

## 配置

三个客户端都用同样的 `mcpServers` 格式：

```json
{
  "mcpServers": {
    "stock-analysis": {
      "command": "/path/to/stock-analysis-plugin/.venv/bin/python3",
      "args": ["/path/to/stock-analysis-plugin/mcp_host/server.py"],
      "env": {
        "SEC_USER_AGENT": "your-name you@example.com"
      }
    }
  }
}
```

| 客户端 | 配置位置 |
| --- | --- |
| WorkBuddy | 侧边栏「插件」→ 右上角「MCP 服务器」→「配置 MCP」；或直接编辑 `~/.workbuddy/mcp.json`（用户级）、`<项目目录>/.workbuddy/mcp.json`（项目级）。参见[官方 MCP 指南](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide) |
| Claude Desktop | `claude_desktop_config.json`（设置 → Developer → Edit Config） |
| Cursor | `~/.cursor/mcp.json`（全局）或 `<项目目录>/.cursor/mcp.json` |

保存后，客户端里出现 73 个工具（如 `get_quote`、`get_technical_analysis`）即接入成功。

## 行为说明

- **协议**：支持 `initialize`、`ping`、`tools/list`、`tools/call`，以及 `notifications/initialized` 和 `notifications/cancelled`。协商版本为 `2025-11-25`、`2025-06-18`、`2025-03-26`、`2024-11-05`；客户端请求其他版本时，回复 `2025-11-25`。
- **stdout 只有 JSON-RPC**：日志和误打印都会转到 stderr。
- **错误**：未知工具、参数不是对象、未知方法、无法解析的 JSON 作为 JSON-RPC 错误返回（`-32602` / `-32601` / `-32700` / `-32600`）。缺少必填参数、数据源失败、Python CLI 超时则作为工具结果返回：`isError: true`，正文为 `{"error": "..."}`。
- **并发与取消**：每次 `tools/call` 在独立线程里运行。收到 `notifications/cancelled` 后，服务器会结束对应的 Python 子进程，并且不再回复该请求。客户端关闭 stdin 时，服务器结束所有进行中的调用后退出。
- **超时**：沿用 `tools/cli_budgets.json` 的命令预算，与其他宿主一致。
- **Skills**：MCP 只提供工具。`skills/*/SKILL.md` 里的 21 个工作流需要客户端自己的 Skills 功能单独导入。

## 本地验证

```bash
.venv/bin/python -m pytest tests/test_mcp_integration.py -v
```

也可以手动发一条请求：

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/python mcp_host/server.py
```
