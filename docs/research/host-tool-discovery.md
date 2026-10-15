# 四宿主按需工具发现调研

核验日期：2026-10-15。范围为官方源码、文档、本仓库注册方式；未运行四宿主的真实模型对比。目标是减少每次请求携带的完整工具定义，保留现有 67 项能力。

## 接入前的仓库状态

- `pi/index.ts`、`openclaw/index.ts`、`dsh/index.ts` 各有 67 次注册；Pi 未声明 `exposure`，因此沿用默认直接暴露。Hermes 遍历全部 schema，以一个 `stock-analysis` toolset 注册。
- 注册目录大小不等于模型请求中的完整 schema 数量；宿主可能在注册之后再做延迟暴露。
- CI 当前固定 Pi `0.99.0`、OpenClaw `2026.9.6`、dsh `0.2.0-rc.2`；真实宿主 smoke 安装 Hermes `0.19.0`。

## Pi：已有原生搜索，本插件需要显式接入

官方仓库现为 `earendil-works/pi`。核验 main `4a5d37291531ad48b8e536c892557877d219463c`，并单独核验本仓库 CI 的 `v0.99.0`。

`exposure: "deferred"` 的工具注册后不直接声明给模型；内置 `tool_search` 搜索尚未启用的 `deferred` / `codemode` 工具，并通过 `setActiveTools()` 将命中项加入下一次请求。默认最多返回 8 项，已加载项保留在会话分支上。`tool_search` 默认关闭，需通过 `defaultTools: ["+tool_search"]` 或工具选择 API 启用；只开启搜索而不修改目前的直接暴露工具，不能减少这 67 份定义。SDK 使用者还需加载对应内置扩展。

来源：[v0.99.0 exposure 文档](https://github.com/earendil-works/pi/blob/v0.99.0/packages/coding-agent/docs/extensions.md#tool-exposure)、[v0.99.0 搜索实现](https://github.com/earendil-works/pi/blob/v0.99.0/packages/coding-agent/src/extensions/tool-search/tool.ts)、[v0.99.0 CLI](https://github.com/earendil-works/pi/blob/v0.99.0/packages/coding-agent/docs/cli.md#tools)、[main SDK 接入](https://github.com/earendil-works/pi/blob/4a5d37291531ad48b8e536c892557877d219463c/packages/coding-agent/docs/sdk.md#codemode-mcp)。

重要限制：v0.99.0 与核验的 main 都使用 BM25，分词器按 `/[^a-z0-9]+/` 切分。提取 v0.99.0 的实际 `tokenize()` 函数执行后，`查询港股财务报表` 得到 `[]`。当前插件描述以中文为主，不能假定中文自然语言查询可召回；需要英文检索词/元数据及中文任务端到端验证。动态加载也会逐渐扩大活动工具集，不能保证长会话始终只暴露 8 项。

后续接入：2026-10-15 核对 npm latest 为 `@earendil-works/pi-coding-agent@1.2.0`。Pi 适配器在 `session_start` 检查原生 `tool_search` 的 `model-only` exposure 和工具列表/活动集合 API，具备能力时将本插件工具重新注册为 `deferred` 并启用搜索；否则保持全量暴露。不读取宿主安装路径或解析版本号。仅在启用发现时附上英文关键词/工具名检索指引。实际安装的 Pi 1.2.0 SDK 已验证：启动后股票工具延迟暴露，原生搜索可加载 `get_market_capabilities`，随后真实 Python CLI 返回 HK 能力结果；SDK 不加载搜索扩展时保留 67 个直接暴露工具。此次验证未调用模型 API。

旧版兼容另以实际安装的 Pi 0.83.0 SDK 核验：扩展加载无错误，股票工具保持全量活动，活动集合共 71 项（4 个默认工具加 67 个股票工具），未启用 `tool_search`。本地四适配器注册测试、Pi 到 Python 往返测试、TypeScript 检查通过。

持续审查修复：初始注册使用 `defaultActive: false`，初始化直接保留宿主活动集合，避免 `/reload` 把所有工具重新激活后再误删已发现项。SDK 重载没有发出 `session_start` 时，下次 `before_agent_start` 完成初始化。真实 Pi 1.2.0 SDK 的重载及 `navigateTree()` 分支切换均验证已发现集合正确保留/恢复，Pi 0.83.0 全量回退仍通过。

## dsh：有动态可见性底座，未发现内置通用搜索入口

核验上游 master `571902028a75c64c1d767f4ae5fd099485f0ddf0`。工具包上游为 `0.2.1-alpha.3`，本仓库安装的工具包为 `0.2.0-rc.2`。

`ctx.tools.register()` 默认使工具进入模型可见集合；`agent.ctx` 作用域上的 `ctx.tools.restrict({ allow, deny })` 可缩小单个 Agent 继承的工具集合，解除限制通过 disposer 完成。`schemas(scope)` 按当前可见集合生成 schema。这可以支持按需启用，但还需要额外的发现/选择入口。本次对官方 packages 源码和工具目录搜索，未找到内置通用 `tool_search` / `search_tools` 实现；这是本次核验范围的结论，不是对所有社区插件的否定。

`deferLoading: true` 是工具定义和 provider 协议已有的标记，不能单独替代发现、加载和会话历史。受支持路由通过 `toolHistory`、`tool-addition` 等激活定义；不支持更新的路由会回退为当前活动工具的完整声明。PTC 的 `run_code` 虽能缩小原生函数数量，仍会提供生成的 SDK 定义文本，不能据此承诺节省上下文。

来源：[工具注册、限制和 PTC](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/packages/core/tools/README.md)、[工具服务源码](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/packages/core/tools/src/index.ts)、[LLM 工具更新边界](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/packages/llm/llm/README.md#known-limitations-and-deferred-work)。

后续接入：dsh 适配器增加 `stock_tool_search` 宿主目录入口，使用官方 `system-prompt/assemble` waterfall 缩小每次请求的 schema 集合，避免通过反复替换权限限制来加载工具。67 项分析能力始终保留原注册；搜索仅从当前 Agent 的已授权请求目录选候选，并在返回前再次检查注册表权限。加载集合以 Agent 对象为键隔离，匹配支持中英文关键词和确切工具名，下一次请求发送命中工具完整定义。`toolDiscovery: false`、旧接口、无作用域、入口被禁止/移除时保留宿主当前授权目录；带 `run_code` 的 PTC/both 请求沿用宿主 SDK。加载集合不写入会话历史，进程恢复或插件重载后重新发现。

实际 SDK 验证覆盖 `0.2.0-rc.2` 的请求组装、两 Agent 隔离、中文查询、权限过滤、启动时入口禁用、中途禁用/重新启用及真实 Python CLI 调用；发布 bundle 在 `0.1.7-rc.1` 和 `0.0.1-rc.1` 工具 SDK 中也已核验发现入口和延迟目录。三个已声明兼容的 SDK 都具备所需接口，均可启用发现；真正缺少接口的宿主以接口检测保留全量行为。官方组装扩展点见[SystemPrompt 文档](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/packages/core/system-prompt/README.md)。

持续审查修复：原始等权关键词计数使 `stock quote` 默认前五项漏掉 `get_quote`。改为按关键词在当前已授权候选中的出现频率降权，并提高工具名匹配权重；`stock quote` 首项为 `get_quote`、`股票回测` 返回 `run_backtest` 的确定性回归已通过。该检索仍是词法匹配，未声称覆盖任意自然语言改写。

## Hermes：官方已有延迟工具桥接

核验 main `3de62feaecc3d582ebc45a1c5d7c2605a3ff4967`。`tools.tool_search.enabled` 默认 `auto`；存在可延迟工具即启用。源码将 MCP 工具与非核心 toolset 的插件工具识别为可延迟，模型通过 `tool_search`、`tool_describe`、`tool_call` 查找、查看参数并调用。请求装配会用常驻工具加桥接入口替换完整的延迟工具 schemas。

本仓库 `stock-analysis` 属于插件 toolset，因此在具备该机制且未关闭的宿主上自然适用，不需要再造一套插件专用搜索。仍须查看实际请求：目录 listing 默认 `auto`，预算 `4000` tokens，延迟完整 schema 不等于完全不携带工具目录。官方文档的 opt-in 措辞与 main 的默认 `auto` 有差异，此处以源码为准。

来源：[实现](https://github.com/NousResearch/hermes-agent/blob/3de62feaecc3d582ebc45a1c5d7c2605a3ff4967/tools/tool_search.py)、[默认配置](https://github.com/NousResearch/hermes-agent/blob/3de62feaecc3d582ebc45a1c5d7c2605a3ff4967/hermes_cli/config_defaults.py)、[使用文档](https://github.com/NousResearch/hermes-agent/blob/3de62feaecc3d582ebc45a1c5d7c2605a3ff4967/website/docs/user-guide/features/tool-search.md)。

本仓库固定的 Hermes `0.19.0` 也有此机制。另行下载 PyPI 官方 wheel 后，确认其中 `tools/tool_search.py` 与官方 `v2026.7.20` tag 字节一致。该旧版 `auto` 使用阈值：可延迟 schemas 达上下文的 10% 才启用；未知上下文时回落到 20K tokens。需要稳定减少暴露时显式设置 `tools.tool_search.enabled: "on"`。旧版尚无上述 listing 逻辑，不能把 main 默认行为直接套用到旧版。

旧版来源：[插件识别](https://github.com/NousResearch/hermes-agent/blob/v2026.7.20/tools/tool_search.py#L163-L185)、[auto 阈值](https://github.com/NousResearch/hermes-agent/blob/v2026.7.20/tools/tool_search.py#L234-L261)、[默认配置](https://github.com/NousResearch/hermes-agent/blob/v2026.7.20/hermes_cli/config.py#L2924-L2946)。

## OpenClaw：官方已有原生工具搜索，固定 CI 版本也包含

核验 main `5266c8d148624d2510692c0c92437418f9ce3897` 及本仓库 CI 使用的 `v2026.9.6`。embedded/Copilot 路径具备工具搜索，普通插件工具属于其适用范围。`tools.toolSearch` 未配置时默认启用 `tools` 模式；`false` 关闭后恢复完整 schema 暴露。

版本间控制工具名称不同：`v2026.9.6` 使用 `tool_search` / `tool_describe` / `tool_call`，main 的调用入口已改名为 `dispatch_action`。另外 `v2026.9.6` 显式布尔 `true` 会选旧 `code` 模式；如需明确配置，使用 `{ "enabled": true, "mode": "tools" }`，不要把布尔值的行为与未配置混为一谈。

`v2026.9.6` 的 catalog 纳入普通已授权插件工具，无需 `optional: true`，也没有工具数量或 token 占比启动阈值。本插件没有设置 `catalogMode: "direct-only"`，因此符合纳入条件；optional 注册控制授权，不能代替延迟暴露。

来源：[v2026.9.6 搜索配置](https://github.com/openclaw/openclaw/blob/v2026.9.6/src/agents/tool-search-config.ts)、[v2026.9.6 catalog 实现](https://github.com/openclaw/openclaw/blob/v2026.9.6/src/agents/tool-search-catalog.ts#L449-L519)、[v2026.9.6 工具搜索文档](https://github.com/openclaw/openclaw/blob/v2026.9.6/docs/tools/tool-search.md)、[main 工具搜索文档](https://github.com/openclaw/openclaw/blob/5266c8d148624d2510692c0c92437418f9ce3897/docs/tools/tool-search.md)。

## 建议

保留 67 项独立能力及统一 Python CLI 合同，优先利用宿主已有的发现机制。Pi 改 exposure 并启用内置搜索；Hermes/OpenClaw 先验证现有运行时最终请求是否已经延迟了插件工具；dsh 单独补齐按 Agent 的按需发现入口。统一的是能力、名称、参数和输出，不必为四个宿主重复实现同一种搜索器。

验收以行情、财报、筛选、回测、复盘等真实中文任务为准，同时检查首轮与加载后的实际工具 schema、检索漏召回和多余调用。减少 schema 数量只是支持证据，不能代替任务完成质量。
