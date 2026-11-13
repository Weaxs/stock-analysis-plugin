# Hermes Agent 接入指南

把 `stock-analysis-plugin` 作为 [Hermes Agent](https://hermes-agent.nousresearch.com) Plugin 使用。

## 前置条件

- Python >= 3.10
- Hermes Agent 已安装并可正常运行

## 安装

### 推荐：pip 安装

在 Hermes 所用的 Python 环境中安装：

```bash
pip install stock-analysis-plugin
```

Hermes 通过 `hermes_agent.plugins` entry point 自动发现插件。在 `~/.hermes/config.yaml` 中启用：

```yaml
plugins:
  enabled:
    - stock-analysis
```

安装开发版：`pip install git+https://github.com/Weaxs/stock-analysis-plugin.git`。

### Hermes CLI

```bash
hermes plugins install Weaxs/stock-analysis-plugin --enable
```

### 项目级插件

```bash
# 需要设置 HERMES_ENABLE_PROJECT_PLUGINS=true
mkdir -p .hermes/plugins
git clone https://github.com/Weaxs/stock-analysis-plugin.git .hermes/plugins/stock-analysis
pip install -r .hermes/plugins/stock-analysis/tools/requirements.txt
```

### NixOS 声明式安装

```nix
services.hermes-agent = {
  extraPlugins = [
    (pkgs.fetchFromGitHub {
      owner = "Weaxs";
      repo = "stock-analysis-plugin";
      rev = "v<版本号>";
      sha256 = "...";
    })
  ];
  settings.plugins.enabled = [ "stock-analysis" ];
};
```

## 验证

启动 Hermes 后用 `/plugins` 查看，应能看到 `stock-analysis`。再问一句：

```
帮我查一下贵州茅台的实时行情
```

Agent 应调用 `get_quote` 并返回行情。

## 可选配置

不配置任何 API Key 也能使用。数据源、搜索、舆情和 SEC 相关的环境变量见 [README · 可选配置](../README.md#可选配置)。可以在启动前 `export`，或写入 `~/.hermes/.env`：

```bash
echo 'TAVILY_API_KEY=your-key' >> ~/.hermes/.env
```

### 智堡（Wisburg）投研数据

`wisburg-research` Skill 使用智堡官方的远程 MCP 服务检索研报、电话会纪要和财报公告；没配置时 Skill 会改用 `search_comprehensive_intel`。在 [智堡](https://wisburg.com) 获取 API Key，写入 `~/.hermes/.env` 后，在 `~/.hermes/config.yaml` 中添加：

```yaml
mcp_servers:
  wisburg-mcp-server:
    url: "https://mcp.wisburg.com/mcp"
    headers:
      Authorization: "Bearer ${WISBURG_API_KEY}"
```

修改后重启 Hermes 或运行 `/reload-mcp`。

## 常见问题

### ImportError: No module named 'hermes'

插件没有装进 Hermes 所用的 Python 环境。用该环境的 Python 重新安装：

```bash
python -m pip install stock-analysis-plugin
```

### A 股数据获取失败

底层使用 [akshare](https://github.com/akfamily/akshare)，可能因网络问题或版本过低失败：

```bash
python -m pip install --upgrade akshare
```

### 升级

```bash
# pip 安装的
python -m pip install --upgrade stock-analysis-plugin

# Hermes CLI 安装的
hermes plugins update stock-analysis

# 源码安装的
git pull && pip install -r tools/requirements.txt
```

## pip 包内容

```
stock-analysis-plugin (wheel)
├── hermes/
│   ├── __init__.py       # register(ctx) 入口
│   ├── plugin.yaml       # 插件清单
│   ├── schemas.py        # 73 个工具的 JSON Schema
│   ├── tools.py          # 73 个 handler，以子进程调用 CLI
│   └── _payload/         # tools/、skills/、schemas/、templates/
└── mcp_host/             # MCP stdio 服务器（stock-analysis-mcp）
```
