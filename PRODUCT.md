# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

使用 DeepSeek Harness 的个人投资者与开发者：他们已经在对话中使用股票分析插件，希望不离开 dsh 就能快速查看市场状态并维护自己的自选股。

## Product Purpose

同一套股票分析能力同时服务对话工作流与可视化盯盘。成功意味着用户打开 dsh 侧栏即可查看六个市场的主要指数、管理自选股并看到最新可得报价，不必让 Agent 重复执行一串工具调用。

## Positioning

一个 Python 核心向四个宿主提供相同的 73 个工具；dsh 前端直接复用现有 CLI，而不是另接一套行情供应商或复制分析逻辑。页面展示原始数据与来源时间，不把 Agent 推断包装成行情事实。

## Operating Context

DeepSeek Harness Web/Desktop 插件环境。首版是 dsh 侧栏中的「股票仪表盘」：自选股置顶并跨市场保存，A / HK / US / JP / KR / TW 六个市场独立切换。插件在 dsh 运行期间后台常驻预热行情（用户确认的方案），页面只读内存快照，打开即显示；交易时段有页面打开时约 30 秒更新，无人查看或收盘后自动放宽，用户也可手动刷新；页面不可见时不轮询。

## Capabilities and Constraints

- 首版聚焦个人盯盘与自选股，不做完整分析终端。
- 展示各市场主要指数、交易阶段及数据源提供的报价时间；A 股额外展示全市场涨跌统计与板块排行。
- 自选股支持按代码添加、删除和持久化，以紧凑行情表为主视图（用户主要用途：扫一眼自选涨跌），可排序、按市场筛选；不做 K 线、财务、新闻、风险、回测、告警或自动交易面板。
- 刷新节奏随交易阶段与是否有人查看变化，失败保留上次数据并退避重试。网络请求只由后端发起，浏览器不接触数据源凭据。
- 非 A 股缺少统一全市场宽度与板块统计时明确留空，不用指数表现冒充市场统计。
- 沿用现有 Node/TypeScript dsh 适配器、静态 Web 页面与 Python CLI，不引入新的前端框架或数据库。

## Evidence on Hand

- `tools/stock_data.py`：指数、报价、A 股市场统计与板块排行 CLI。
- `tools/trading_calendar.py`：六个市场的交易阶段。
- `dsh/index.ts`：73 个工具的 dsh 适配器与 Python 执行链。
- `dsh/package.json`：dsh bundle 清单。
- 本机 `dsh-guizang-design` 插件：侧栏、主区域 iframe 与 `webServer` 路由的已安装范例。

## Product Principles

1. 打开即有可读的市场概况，但首屏不触发完整研究管线。
2. 所有市场使用同一套页面骨架，数据能力差异必须诚实可见。
3. 自选股列表求轻、求清晰；深度分析继续交给现有 Agent 工具。
4. 延续 dsh 的产品 UI 与主题，不为插件另造独立视觉身份。
