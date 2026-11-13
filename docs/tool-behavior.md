# 工具行为与可靠性

本文说明各工具输出字段的语义、部分失败与缺失数据的处理方式，以及命令预算和分析质量验证。工具清单见 [README · 工具列表](../README.md#工具列表)。

## 趋势研究

`trend_temperature` 是独立趋势温度指标：`(EMA20-EMA60)/ATR14`，ATR 使用包含跳空的真实波幅简单均值。附分数、EMA差方向 `direction`、七档研究标签、前一期信息、数据日期和价格口径；`validation_status=not_validated`，真实股票试验未支持默认温度交易映射。方向按零轴计算，“平”标签不代表零方向；默认需 60 根预热，零 ATR 或坏行情返回不可用。±0.5/±1.5/±3 的分档尚未校准，不是趋势动物公式、肥尾指标、上涨概率或自动买卖信号，也不修改原综合评分。无需个人持仓存储；[公式、边界及回测说明](../skills/bull-trend/references/trend-temperature.md)。

月度右侧趋势策略，通过 `analyze_trend_strategy(symbols, ...)` 调用：月初200日线上行入场、真实旧仓优先、63日波动预算；[输入格式与完整规则](../skills/bull-trend/references/monthly-trend-strategy.md)。扩展及历史样本尚未通过完整效果门槛；直接提供股票代码即可自动取日K，输出信号与预算计划；实际成交/股数和共享资金回测另行核实，不自动保存持仓或下单。

趋势研究另返回 `trend_horizons`（daily、建议count=360）：21/63/252根日K过去对数收益，在月初首根有效日K收盘观察并保持；显示期限组成、方向及观察日期，不强凑七档。与回测 `monthly_momentum` 共用计算，仍为 `not_validated`；[方法、预热与验证局限](../skills/bull-trend/references/trend-horizons.md)。

市场只约束新开仓的三期限实验可运行 [market_trend.py](../skills/bull-trend/scripts/market_trend.py)，输入带预热的个股/同市场宽基daily OHLCV JSON，复用同一Python核心。月度入场、次日开盘、已有持仓按个股趋势退出；不会自动保存持仓或下单。[使用与当前验证边界](../skills/bull-trend/references/trend-horizons.md#市场环境入场实验)。

## 自选股批量分析、信号与筛选

批量自选股分析和摘要的 `success` 表示至少获取了有效行情价格、K 线收盘价或技术评分；全部关键数据失败时计入 `failed`，部分结果仍保留。采集脚本复用返回的 K 线计算技术指标，并按股票所属市场查询大盘状态；六个市场均按标的市场查询大盘状态；非 A 股板块榜单不可用，板块热点明确返回 false。

同一次自选股分析会共享报价、日 K 线和按市场获取的大盘状态（包含可选市场复盘）；风险与异动分析复用本次快照，资金流汇总和明细仍分开采集。采集任务使用共享的 6 线程 CLI 池，每支股票完成后即可接续异动分析，最终顺序保持调用方顺序。报价不跨调用保存。独立仓位/风控/筛选采集步骤并行执行。 两融历史明细按交易所/日期复用完整表，当日表和空表不缓存；CN 交易日历完整快照缓存一天。搜索按原优先级降级，单次查询的切换预算为 45 秒，综合搜索共用 90 秒预算；各请求使用剩余预算内的超时，严格总时限仍由宿主控制。同次综合搜索会跳过已确认不可用的引擎，下次独立调用重试。

信号记录支持同一存储文件的多进程读写；评估期间新增的信号会保留。价格必须为正的有限数值，持有期限为正整数，日期格式为 YYYY-MM-DD。存储文件含损坏 JSON 行或无效信号时，记录、评估和汇总返回错误，原文件保持原样。

信号评估仅在历史 K 线覆盖记录日时结算，缺少历史窗口则返回 `skipped_no_data` 并保持未结算。风险筛查返回 `data_complete` 和 `warnings`；数据不足时评级可为 `unknown`，`risk_score`、`veto_buy` 可为 null。告警的 `evaluated` 条目含 `error` 时表示无法检查，不能将未触发理解为已通过检查。

选股的 `filters_applied` 只列实际执行的条件，显式条件缺少数据时报错。回测的 `period` 是实际数据期间，`requested_period` 保留请求期间，`warnings` 提示覆盖差异；买入预算包含佣金，年化收益和回撤按实际数据及初始资金计算。

## 无状态研究与风险计算

这些工具不保存自选股、账户或分析历史，也不提供调度/通知。现有数据工具采集行情和财务，宿主传入本次持仓、正文或快照；[完整输入约定与解读工作流](../skills/stock-analysis/references/stateless-analysis.md)供四个宿主共用。

大输入经权限受限的临时 JSON 文件传给 Python，调用结束后清理，避免 Windows 命令行长度限制；CLI 也支持 stdin 和 `--input` 文件。公告 HTML/PDF 可用 `extract_article` 从公网提取并检查 `truncated`；无文本层 PDF 的 OCR 仍不包含，只有标题或链接不能声称已读全文。

例如仓位预算（所有金额同币种，lot_size 由用户或交易规则确认）：

```json
{
  "equity": 100000, "cash": 20000,
  "entry_price": 100, "stop_price": 95,
  "risk_fraction": 0.01, "lot_size": 100,
  "fee_rate": 0.001, "slippage_rate": 0.001
}
```

将上述 JSON 保存为 `input.json` 后运行：

```bash
python tools/stateless_analysis.py position_size --input input.json
```

对应宿主工具是 `calculate_position_size`。其余 CLI 子命令依次为 `peers/disclosure/portfolio/rebalance/event_study/robustness/snapshot`。事件研究可直接传 `symbol/benchmark/start_date/end_date/event_dates`；回测验证可传 `symbols/start_date/end_date/strategies`，由 Python 复用行情接口，避免模型重新抄写历史 K 线。显式价格/datasets 模式仍支持离线计算，两种模式互斥。

跨币种组合必须提供显式汇率；价格盈亏不含历史汇兑损益。组合压力测试是用户指定市场/行业/个股/汇率冲击的确定性 what-if，个股冲击覆盖行业、行业覆盖市场，汇率独立乘算，不是预测或最大亏损保证。调仓工具按目标权重和整手生成计划，先卖后买；现金不足时同比例缩减买单并以 `cash_limited/unfilled` 标记，计划数量不代表成交。稳健性工具仅支持 next_open、每窗口重新从现金开始，样本内敏感性不等于样本外表现。

## 命令预算

命令预算集中于 `tools/cli_budgets.json`：通用采集默认 120 秒、市场快照 240 秒、选股 top 须为正整数，预算按候选数及 6 个 worker 的批次计算（默认 20 个候选为 1830 秒）、13F 300 秒、名称解析与文本导入（首次拉取港美名录）360 秒；技术分析、市场状态、回测和风险筛查按实际嵌套调用另留预算；各宿主额外留 30 秒接收 CLI 输出。综合采集保留安全 JSON 错误及 error_code（timeout/empty_output/invalid_json/process_failed/tool_error），不会把失败当成有效空数据。

## 分析质量验证

最终报告的固定证据案例覆盖完整、缺失、过期、同口径冲突及样本不足：

```bash
.venv/bin/python -m pytest tests/test_analysis_quality.py -q
# 需要 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL；只调用模型，数据源使用固定夹具
.venv/bin/python -m pytest tests/e2e/test_analysis_quality.py -m integration_llm -v
```

真实模型评测同时要求结构化证据检查与独立上下文的结论审稿通过：审稿检查正文事实、限制说明是否诚实，以及是否过度外推。未运行审稿、结果缺失或模糊都不能算通过；报告自行声明通过不被采信。另有人工核对的正确报告及两类反例用于验证审稿器（正确引用但正文捏造现价、3笔交易却保证长期胜率）。空回答也失败。离线渲染和规则检查不代表真实模型通过，也不证明策略未来有效。信号建议可沿用 source 标识策略版本，按来源复盘胜率、平均收益和样本量；未扣成本、未匹配基准的观察记录不能当作策略回测。

信号结算仅在价格口径可核实时进行：原始价格可直接比较；自动记录的默认 `record_date` 与 `price_date` 均按标的市场时区确定（显式 `date` 不变），并保存原始 `price_as_of`；`price_date` 晚于 `record_date` 时拒绝记录与结算；多头 target 必须高于 entry、stop 必须低于 entry，空头相反，否则拒绝记录。开盘先触 stop 按 open 结算，先触 target 按 target 保守结算，日内顺序未知的双触发仍 stop 优先；无止损的旧 HLC 记录可继续用 high/low/close 判定目标和到期，但若提供 open 也必须是正有限数值，有止损但缺 open 则拒绝结算。重复日期返回 skipped_no_data，复权乘除使用 Decimal。复权日线需该报价日的 adjustment_factor（复权价/原始价），当前日线 Tushare、Alpha Vantage 可提供。缺失系数或口径未知时返回 skipped_price_basis，保持未结算，不猜测除权比例；记录日无 K 线时仅在前后因子一致（或已有明确报价日）时使用最近锚点，旧信号缺少报价时点且记录日因子变化时也保持未结算。换算后的 exit_price 是记录时价格单位的等价值，实际市场价可不同，evaluation_price_basis 保留依据。evaluate_signals 的 CLI 预算为 300 秒，内部提前结束采集并保存已完成结果，complete/deferred/warnings 指示需要重试的记录；预算耗尽后调整未结算记录的处理顺序，下轮优先处理尚未尝试的记录，避免慢请求长期阻塞后续信号。

市场复盘数据不足时温度/姿态为 null，信号 unknown，data_quality.limitations 说明限制。自选股异动采集失败时 anomaly_status=unavailable、anomaly_count=null，保留警告并计入 summary.partial，不能当成无异常。

L1 与 L2 综合分均在排序前按可用因子重新归一化；缺失或非有限因子不作零分处理，因子组内部也仅平均有效输入。factor_coverage.ratio 保留原始权重覆盖率，effective_weights 是实际归一化后的权重，高分仍需结合覆盖率判断。估值比较需同时读取 `pe_basis`；有限非正 PE/PB 的价值子分为 0，不能因数值更低而获得最高价值分。

L2 的 `vol20` 只使用恰好 20 个有效日收益，`factor_coverage.vol20_samples` 记录实际样本数，非正/非有限收盘价先剔除，空输入为 0；质量因子按 `report_date` 分组归一，避免年度累计与单季累计直接比较。资金流 5/10/20 日窗口不足返回 null，不按 0 或完整窗口处理。HK 快照统一带 `.HK`，US 快照移除东财交易所后缀并把类别股点号转为 Yahoo 连字符。主板 ST/*ST 按 5% 识别涨跌停，其他板块比例不变。
