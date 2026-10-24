# 无状态研究工具

这些工具在一次调用中计算，不维护自选股、账户、规则或历史。宿主/用户显式提供本次持仓、历史快照与正文。采集继续用已有行情、财务、公告工具。MUST 从原始结果取值、保留来源和时点；MUST NOT 补造缺失值、股票池或持仓。缺少必需输入时只索取该输入。

## 同行比较：compare_stocks

1. 确定用户提供或有明确行业依据的可比股票，调用 get_financials/get_quote/get_stock_info；必要时 resolve_stock_sectors。不要用全市场估值直接代替同行。
2. 传 `companies`（2..30项），每项 `{symbol, period, metrics}`。`metrics` 每项 `{name, value, unit, basis, source?}`，缺失 value=null。`period` 是指标的共同报告期或估值日；同一公司不同期间的指标应分批比较。
3. 核实单位。A 股 roe/net_profit_margin/gross_margin 可能是百分数，而 Yahoo roe/profit_margin 是小数；只有确定源口径时才换算并记录依据。金额单位必须含币种。不同年度、TTM 与单季、合并与母公司报表分开。
4. 工具只有 unit/basis/period 完全一致且至少两项有效数值时返回 ascending。它表示升序，MUST NOT 称为买入排名。亏损 PE、负 PB 和行业特有口径需另行说明，不按“越低越好”解释。

## 公告财报比较：analyze_disclosure_changes

传 `previous/current`，每项 `{text, source, period, metrics}`；正文上限200000字符，metrics 形状同上（可为空），`max_changes` 默认30、上限100。

先查询原始公告；HTML 可用 extract_article，但它默认截断正文，不能将其结果自动视为全文。PDF/OCR 由宿主文档读取能力或用户提供，MUST NOT 把标题、链接或搜索摘要冒充全文。缺少正文时仍可分析已核实的财务指标，但不要调用要求正文的比较工具。

工具输出精确到传入文本行号的变化片段、来源、截断标志和指标差异。文本差异本身不等于经营变化；Agent 要阅读上下文，说明发生了什么、与原预期的关系和可能反证，并引用原文。金额单位和 basis 一致才计算 delta；前期值大于0才计算普通增长率，亏损转盈另述。报告期可不同，但必须核实是可比同长期间（年度/同比单季等），累计季度不能直接当单季。

## 组合风险：analyze_portfolio_risk

输入 `base_currency`、基准币种 `cash>=0`、`positions`（1..100项）。持仓 `{symbol, quantity, price, currency, cost?, stop_price?, sector?, as_of?}`，现货多头；重复标的先汇总。价格/数量为正，单位由调用方确认。

可选 `fx_rates:[{currency,rate}]`，rate 表示一单位非基准币种兑换多少基准币种；基准币种隐含1，禁止重复。缺汇率不能部分汇总冒充总资产。相同币种现金先换成基准币种再输入；其他币种现金可按现金类头寸单独注明，不能当股票计算相关性。

输出总权益、现金权重、单股权重、行业/币种敞口和持仓 HHI（不含现金项）。`price_pnl_base` 是按当前汇率折算的本币价格盈亏，MUST NOT 称为包含汇兑损益的账户真实收益；缺任何成本时为null。`stop_loss_risk_base` 是从现价到止损的损失情景，缺任何止损时为null，不保证成交。

可选 `histories:[{symbol,prices:[{date,close}]}]`，工具按每对标的共同日期计算收益相关性；至少4个共同价格点、存在波动才有结果。不是组合收益率或汇率调整后的相关性；少样本、跨时区非同步收盘和缺交易日都应说明，不从相关性推断因果。缺历史时明确相关性未计算。

## 仓位预算：calculate_position_size

必需 `equity,cash,entry_price,stop_price,risk_fraction,lot_size`。风险比例0..1，止损正且低于入场价，现金0..equity，交易单位正整数。所有金额同币种。

可选 `max_position_fraction=1`（0..1）、`fee_rate=0` 与 `slippage_rate=0`（每边比例0..0.1）。买入现金按 entry×(1+slippage)×(1+fee)，止损回收按 stop×(1-slippage)×(1-fee)；每股差额计入风险预算。现金、风险和本次最大买入资金分别向下取整到lot，取最小数量。已有同股持仓时，先扣减用户允许的新增仓位和风险额度，不能把本次额度当成最终账户仓位限制。

lot_size 来自用户或可核实交易规则，尤其港股不能统一猜100股。工具不覆盖券商最低收费、未提供税费、跳空和流动性风险。quantity=0 表示当前约束下不足一个交易单位，MUST NOT 向上凑整。

## 事件研究：analyze_event_study

通常直接传 `symbol,benchmark,start_date,end_date,event_dates`，由共享 Python 核心取数，避免模型复述整段K线。股票与基准应属于同一市场；代码模式输出实际覆盖区间和提供方复权标记，没有标记时明确未指定，不用调用方猜测覆盖。开始日期须覆盖事件前一交易日、结束日期须覆盖最长事件窗口。也可使用下面的显式价格模式，两种模式互斥。

传 `prices,benchmark_prices`（各2..20000条 `{date:"YYYY-MM-DD",close}`）、`event_dates`（1..100个唯一日期）、`price_basis`（明确股票与基准各自复权口径）。可选 `horizons`（1..20个0..250整数，默认0/1/5/10/20）。复用 get_kline，先核实数据覆盖事件前一交易日至最大窗口结束。

事件日映射到股票序列中不早于指定日期的首日；盘后事件应显式使用下一交易日。基点是该日前一个股票交易日收盘；horizon=0表示事件当日收盘。基准必须同市场，并在两个端点都有数据，缺失时不移动事件日期。收益差是股票与基准各自买入持有收益的差（百分点），不等于因果影响。

MUST 核实完整交易日、复权、币种和股息处理。工具无法从缺行判断是停牌、节假日还是数据故障。重叠事件标识意味着样本不独立；完全没有有效超额收益样本（包括股票窗口不足或基准端点缺失）的事件不参与重叠计算，不应误标相邻有效事件。少样本均值不能作为稳定预测。

## 回测稳健性：validate_backtest

通常传 `symbols:[代码],start_date,end_date,strategies`，Python 直接复用现有 K 线获取；不要让模型抄写数千条行情。也可使用下面的显式 datasets 模式，两种模式互斥。读取每个标的 data_coverage，实际覆盖不足时缩小结论范围；提供方未标记价格口径时须说明。代码模式共享CLI预算660秒、事件研究300秒，宿主另留退出余量。

传 `datasets`（1..5项 `{symbol,price_basis,bars}`，bars 是按日期升序的 date/open/high/low/close/volume，每组最多2000条）、`strategies`（1..5项 `{name,strategy}`，完整既有DSL的JSON对象）。可选 `train_bars=126,test_bars=63`（各30..1000）、`capital=1000000`。每组至少训练+测试长度。

通过现有 get_kline 取完整数据，读取用户策略后转换为JSON；parameters 引用复用现有引擎解析。候选应事先确定，只在用户要求优化时扩展候选，MUST NOT 看测试窗口收益后反复改参数并仍称样本外。

每个标的独立滚动训练，按训练总收益选候选（并列按输入顺序），在其后的非重叠完整测试窗口评估。返回每个候选的训练结果、选中者及测试结果；全区间 sensitivity 是样本内比较。尾部不足完整测试窗口会报告unused_tail_bars。

选择使用相同初始资金下的实际 `final_equity`，不使用展示精度舍入后的收益。所有入场/退出条件先校验，未触发的条件也不能绕过检查；坏候选单独返回 error，不中断合法候选。无法定义的 `profit_loss_ratio` 为null，不能当作0；其余数值溢出或无效Unicode输入返回JSON错误。

每个窗口从现金和重新初始化的指标开始，不拼成连续组合；MUST NOT 把各窗口年化收益平均当成实际年化。只允许next_open。关注候选错误、交易数和未平仓；收益含未平仓市值，交易统计仅含已平仓。不同标的和不同参数的收益分散程度是稳健性证据，不是自动策略认证。缺少历史股票池、退市样本、公司行为和限价数据时保留原回测限制。

## 快照比较：compare_analysis_snapshots

传 `previous/current` JSON对象；嵌套字典按JSON Pointer输出变化，数组按整体比较。批量股票应先以symbol构造字典，时间戳/采集耗时等机械字段先按需求排除，避免无意义变化。

状态为added/removed/changed/unavailable/recovered。null 或包含error的对象表示不可用；removed只表示键从输入消失，MUST NOT 自动解释为退市、卖出或业务事件。缺少历史输入时仅做当前分析；持久化、调度及通知由宿主负责。
