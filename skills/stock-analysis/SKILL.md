---
name: stock-analysis
description: 综合股票分析：联合技术、基本面、资金、消息和风险数据研判单只股票。用于多维分析、持仓处理或买卖计划；单一策略问题用对应策略 skill。
---

# 综合股票分析

批量研究、子任务分工、网页原文补证或报告文件交付时，必须读取[宿主研究与报告交付](references/host-workflows.md)。最终回复用 renderer 的 `final=true`；仍需保存附件、发送或继续分析时保持默认 false。

在启用原生工具发现的 Pi 中，所需工具尚未暴露时，先用 `tool_search` 搜索确切工具名或英文关键词，再按本文流程调用；其搜索器目前不对中文分词。旧 Pi 或其他宿主按当前实际提供的工具直接执行。

在 dsh 提供 `stock_tool_search` 且所需工具尚未暴露时，先搜索中文功能关键词或确切工具名，加载后按原名调用；搜索不执行分析。没有发现入口时直接使用当前提供的工具。

美股机构研究调用 `get_us_holdings(cik, mode=auto)`：有当前或已检索历史 13F 时返回机构组合，否则按发行人 CIK 反查机构；也可显式选 `manager/issuer`，如 Berkshire `0001067983` 查组合、Apple `0000320193` 用 `issuer` 查机构。发行人映射仅使用核对 CIK 的官方 13D/G CUSIP，不做名称猜测；无映射直接报缺失。首次下载约 100 MB，后续缓存，共享 CLI 查询预算 300 秒（宿主另留退出余量）。读取 `report_date/filing_window_end/cusip_evidence`，区分持仓日期与披露窗口；修订重述替换、新增补充，缺基础版或未知修订时总量 null，并计入 `unresolved_manager_count`。`mapping_complete=false` 表示不能保证涵盖所有类别或变更，`complete_for_mapped_cusips` 只表示已映射证券在公开数据窗口的申报链完整性，不能称实时全部股东。期权/本金在 `positions` 单列，不能把跨机构持股简单合计成去重比例。机构组合查询仍返回最新已知报告期的原始 13F 与修订链接，缺少对应原始版时明确报错；`history_truncated=true` 时不能将旧记录当作已完整检索。存在修订先读原文，不能自动推断变化。

美股内部人研究调用 `get_insider_trades(symbol, days=90, top=10)`，需设置含真实联系邮箱的 `SEC_USER_AGENT`。`days` 按申报日期过滤，`top` 限制文件数；只将代码 P/S 视作买卖信号，授予、行权、赠与等保留原始代码和脚注，衍生品单列。Form 4/A 与原申报勿重复计算，检查截断和失败提示；`screen_risk` 仍仅支持 A 股。

## 无状态分析扩展

当用户要求同行比较、组合风险、仓位预算或新旧分析比较时，MUST 读取[无状态研究工具](references/stateless-analysis.md)，分别使用 compare_stocks、analyze_portfolio_risk、calculate_position_size、compare_analysis_snapshots。所有用户状态均由本次输入提供。

## 意图路由表

快速问答先判断意图，按下表走最短调用链；完整综合研判走下方执行流程（gather 采集管线），不受此表限制。单工具可答的问题按各工具描述中的适用场景选择，本表不再重复。

用户要求短线、波段、交易计划或买卖时机时，读取[短线交易决策框架](references/short-term-trading.md)；普通基本面或长期价值分析不加载该参考。

| 意图 | 调用序列 |
|------|---------|
| 个股买卖时机 | `get_technical_analysis` → `get_market_capabilities` → 当前市场支持的资金/风险工具 → `detect_market_regime`。A股可加 `get_capital_flow`(summary) 和 `screen_risk`；其他市场不得用缺失的资金或风险数据。veto_buy=true 则不入场；大盘下跌时个股看多信号降级为轻仓试错或等待；HOLD/WAIT 给出可观察触发条件 |
| 个股状态速览 | `get_quote` + `get_technical_analysis` |
| 异动归因 | `detect_anomaly` + `get_news` |
| 大盘择时 | `detect_market_regime`；A 股可补充 `get_market_stats` |

## 执行流程

### 第一步：采集数据

运行数据采集脚本（并行采集行情、K 线、财务、资金、筹码、新闻、舆情、风险和大盘状态；技术指标复用本次 K 线计算）：

```bash
python3 scripts/gather.py <symbol>
```

解析返回的 JSON 数据。如果某个字段为 `null` 或包含 `error`，表示该数据不可用，基于可用数据继续分析。大盘状态按股票所属市场查询；日/韩/台市场暂不支持，标明缺失，不能套用 A 股状态。

解读实时行情前先调 `get_trading_phase` 确认该市场当前盘中阶段（盘前/早盘/午间休市/午盘/交易中/盘后/休市），避免把休市时的静态快照当作盘中信号解读。

港股 `get_quote` 自动按 yfinance → 腾讯 → finnhub → longbridge 容灾，腾讯无需 API key，使用 `r_hk` 实时报价通道（`hk` 通道延迟约 15 分钟）。腾讯港股成交量为股，成交额及市值为港元；PB、换手率、量比为 null 时按缺失处理。该备用源只覆盖报价，K 线仍走原链路。

### 第二步：综合研判

根据获取的数据进行多维度分析：

**市场环境**：
- 当前市场处于什么阶段（上涨/下跌/震荡/高波动）
- 该阶段下哪些策略更适合，哪些应回避

**技术面**：
- 趋势判断：均线排列、MACD方向、布林带位置
- 买卖信号：金叉/死叉、超买/超卖、支撑/压力位
- 成交量：量价配合、放量/缩量

**基本面**：
- 估值水平：PE/PB 相对行业和历史分位
- 盈利能力：ROE、净利润率
- 财务健康：负债率、流动比率

**资金面**（A股）：
- 主力资金流向
- 大单/中单/小单净流入趋势

**消息面**：
- 近期重要新闻和公告
- 可能影响股价的事件

**风险筛查**：
- 7维度风险标记（估值极端/技术预警/解禁到期/内部人减持/业绩预警/监管处罚/行业政策）
- 风险评级（low/medium/high/unknown）和一票否决（veto_buy）；查看 data_complete、warnings，unknown 或 null 表示缺少数据，不能据此判定低风险或已通过检查
- 如果 veto_buy=true，必须在报告中醒目提示

### 第三步：渲染研报

1. 按 [报告 schema](../../schemas/report_schema.json) 构建结构化报告。
2. 每个结论只使用成功返回的数据；缺失维度在 `risk_warning` 中说明影响。必须填写 `evidence`，每项使用原始工具结果路径（如 `get_quote.price`）、读者可理解的 `label`、原始 `value`、实际 `source` 和数据时间 `as_of`；未知来源或时间填 null，不用采集时间替代。历史财报与顶层快照分别引用日期、币种和单位，不把不同期间的数值拼成同一结论。 来源与时间沿证据 path 继承最近父对象的 source（该键缺失时用 snapshot_source）和 data_as_of，子对象的对应值（包括显式 null）覆盖祖先；数组项及 warnings 字符串同样继承，不能仅因叶子字段没有来源就填 null。整条路径均未提供对应元数据时才填 null，不能用工具名或策略方法名代替来源；as_of 保留完整数据时间及其时区。report_date 是财报报告期，应单独引用 value，不能当成披露日期或 data_as_of。证据 path 不含调用参数，例如 get_financials.periods.0.roe；股票名称未返回时 stock_name 填“未知”，不能凭常识补名；已知返回对象中未提供的末级字段可用 value=null 表示未知，其 source/as_of 可填 null，若填写则必须来自已知父对象；不能编成空数组、字符串或数值，不能跨缺失对象或数组编造路径；错误 value 保留原始字符串，不加 error: 前缀。请求的期间或文件路径不证明实际数据覆盖期或文件内容，需与实际返回数据区分。
   同时填写 `data_quality.flags/limitations`：关键数据缺失用 `partial_data`，明确过期行情用 `stale_quote`，同期间同口径来源冲突用 `conflicting_data`，回测/信号样本不足用 `insufficient_sample`。出现这些限制时降低置信度，不能给出 strong_buy；没有有效现价时不编造数值交易计划。数据完整时 flags 为空数组，限制仍按实际模拟条件说明。
3. 渲染前核对证据清单：行情已返回的 `price` 和 `data_as_of` 必须分别作为 `get_quote.price`、`get_quote.data_as_of` 的独立 evidence 项，保留原始 value；其他证据项的 `as_of` 元数据不能代替时间字段自身的 path/value 引用。报价过期时仍保留原始价格与时间作为历史证据，说明时效限制，等待新报价后再制定当前数值交易计划。
4. 调用 `render_stock_report`：快速问答用 `brief`，完整研判用 `full`。
5. 输出渲染后的 Markdown；只在用户要求机器可读数据时附加结构化 JSON。

## 信号跟踪闭环

- 给出明确买卖建议（方向 + 入场价/目标价/止损价）时，调 `record_signal` 存档，`source` 填触发该建议的 skill 名（如 `stock-analysis`、`dragon-head`）。
- 后续会话用 `evaluate_signals` 结算到期信号（target_hit/stop_hit/timeout 并回写收益），用 `get_signal_summary` 复盘胜率与平均收益（可按 source/symbol/status 过滤），同时报告 `return_samples/sample_warning`，缺失收益不按零计；这些是观察性建议记录，不是扣除成本的策略回测。
- 信号持久化在本地 JSONL（`$STOCK_SIGNAL_STORE` 或 `~/.stock-analysis/signals.jsonl`）；是否记录由宿主 Agent 决定，不阻塞报告输出。

## 注意事项
- `get_kline` 的 A 股股票/ETF 数据为前复权，每条记录 `adjusted="qfq"`；显式指数 `sh000xxx` / `sz399xxx` 为原始点位，`adjusted="none"`。复权数据源全部失败时说明数据缺失，不用未复权价格补算指标或信号。
- `get_technical_analysis` 的 RSI 全涨窗口为 100、全平为 50；预热不足的 RSI/量比不得解释为中性。量比是当根成交量除以前 5 根均量，不包含当根本身；不足时返回 unavailable/insufficient_data，不称为正常量价。
- 行情读取 `source/fetched_at/data_as_of`，`fetched_at` 是 UTC 采集时间；`as_of_basis=trading_calendar` 是日历推断，不能声称是提供方报价时间。提供方时间未知时明确说明时效性未核实。
- 始终提供风险提示
- 不做绝对化的涨跌预测
- 建议用户结合自身风险偏好做决策
- 如果某些数据获取失败，说明情况并基于可用数据分析
- 如果风险筛查返回 veto_buy=true，必须醒目标注并建议谨慎

完成标准：每个买卖建议都对应可观察的触发条件和失效条件，且所有缺失数据及其影响已说明。

## 低频价投数据

按用户问题选择数据，完整说明口径、采样覆盖和缺失项：

- 判断估值贵贱：`get_valuation_history(symbol, years=5)` 的 PE-TTM/PB 历史正值分位与 Band；亏损 PE 不计算分位，实际采样不足五年必须说明。
- 读多年财报：`get_financials(symbol, periods=10, annual=true)`；A 股按年报筛选，港股按源实际可得年度数返回。财报期不等于公告日，年报历史不能用于假定当时已知信息的回测。
- 财报季排期：`get_earnings_calendar(report_date="YYYY-MM-DD", symbol=...)`，区分最新预约日期、实际披露日期和已公告业绩预告，不把预告当业绩实绩。
- 股东变化：`get_shareholder_structure`。A 股北向值是财报十大流通股东中的 HKSCC 名义持股，缺席不等于零，不能当成日度北向买卖；港股南向值按港交所披露日期使用。
- 大盘估值：`get_index_valuation(symbol="000300", years=5)`；中证近期 PE1 不冒充 PE-TTM、PB 或多年分位。
- ETF：`get_etf_details(symbol="510300", benchmark="sh000300")`。折溢价须看净值/行情日期；披露持仓不是完整实时申赎篮子，指数成分与基金持仓分别呈现。跟踪误差必须给出用户认可的基准。
- 股息：`get_dividends` 区分历史、未来已公布除息日历和 A 股报告期排行；预案不计入已实现 TTM 股息。名义每股现金未重算送转股口径，遇送转股时不得据此精确比较持有收益。
- 两融：`get_margin_trading(include_ratio=true)` 的 `margin_buy_ratio_pct` 只使用同日成交额；null 表示不可计算。

慢变数据存于 `~/.stock-analysis/cache`：财报、基本信息及新历史数据按天，板块映射按周；行情仍实时查询。输出中的报告期/数据日期决定能否用于判断，缓存命中不代表数据已更新。

### 补充数据覆盖

get_shareholder_structure 的 northbound 优先使用沪深官方季度总量；northbound_nominee 只是十大股东推断，不能混同。get_etf_details 的 basket 是完整已发布 PCF，对价数量/现金替代规则不是基金实际持仓或NAV权重。HK 股息排行使用 get_dividends(mode=ranking, market=HK)，年度口径不能称 TTM。

实际披露股票持仓可用 get_etf_details(include_full_holdings=true) 获取半年度/年度表；按 as_of 解释，数量为股、市值为人民币元。默认不取大量明细；不是实时全资产组合，不与 PCF 对价数量混同。

信号价格使用原始报价口径，自动报价记录同时保存原始 `price_as_of` 和按标的市场时区规范化的 `price_date`；`price_date` 晚于 `record_date` 时拒绝记录与结算。复权 K 线需有该报价日的可核实 adjustment_factor，才能换算入场/目标/止损及收益；缺失时 skipped_price_basis 并保持未结算，不得猜测拆股或除权比例。记录日无 K 线时仅在前后因子一致（或已有明确报价日）时使用最近锚点；旧信号缺少报价时点且记录日因子发生变化时必须保持未结算。evaluate_signals 返回 complete/deferred/warnings；预算耗尽时已完成结果保存，未处理记录下次重试。当前仅日线 Tushare、Alpha Vantage 提供可验证换算系数，其余复权来源可能跳过结算。

自选股上下文异动检查失败时 anomaly_status=unavailable、anomaly_count=null，并提供 anomaly_error/warnings；summary.partial 不得写成无异常或完整成功。
