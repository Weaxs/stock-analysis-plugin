# 确定性计算与配权分项回测

共享核心为 `tools/sector_rotation.py`。Pi、Hermes、OpenClaw、dsh 暴露相同的两个工具；Agent负责输入来源与结论，计算和资金递推均由Python完成。它们不包含尚未披露的神经网络训练或预测。

## calculate_rotation_metrics

`prediction_samples` 与 `trade_counts` 至少提供一项。每组样本2..100000个有限数，全请求最多100000个；最多100组唯一标识。同一组必须属于同一模型、标的和决策时间，来源由调用方核实。

```json
{
  "prediction_samples": [{"symbol":"XLK","samples":[0.1,0.2,0.1,0.15]}],
  "trade_counts": [{"symbol":"XLK","buys":4,"wins":3,"streak":2}]
}
```

返回逐标的 `confidence` 和/或 `allocation`，其中原始配权分数为2.25；`model_status=not_implemented_missing_specification`、`is_buy_signal=false`、`paper_reproduction=partial_calculations_only`。零历史和零方差边界见 [规则参考](strategy_rules.md)。不验证预测来源的真实性，不把集中度当作盈利概率。

## backtest_rotation_allocation

正常调用直接传股票/ETF代码，无须用户提供行情文件：

```json
{
  "symbols":["XLK","XLE","512000","512010","1306.T"],
  "start_date":"2021-01-01",
  "end_date":"2026-10-16",
  "history_start_date":"2020-01-01",
  "warmup_weeks":26,
  "capital":1000000,
  "cost_per_side":0.001
}
```

| 字段 | 约束 |
|---|---|
| `symbols` | 1..20个插件格式代码；与datasets互斥，重复别名拒绝；任一成员取数失败整池停止 |
| `start_date/end_date` | 必填、有序的YYYY-MM-DD评估区间；截止日不得晚于当前UTC日期，不能使用未收盘bar；完整交易周才成交 |
| `history_start_date` | 仅代码模式，默认评估前一年；早于start_date，取数长度包括这段预热 |
| `warmup_weeks` | 评估前完整全池交易周，整数1..104，默认26；不足则停止，不延后起点冒充完整评估 |
| `capital` | 每个市场/报价币种分组的独立初始资金，默认1000000，0.01..1e12；不是混合池总资金 |
| `cost_per_side` | 假设的每边比例成本0..0.1，默认0.001；包含预算内买入成本及卖出成本，非交易所费率声明 |

A/HK/US/JP/KR/TW均按真实交易所日历取周内首末交易日。币种采用已核实的报价币种，不能依据市场猜测港股ETF一定报价HKD。跨市场或同市场多币种分成独立资金组，MUST NOT 把各组净值相加或称为完成了外汇/结算模拟。

复核已有行情时，用 `datasets` 代替symbols，省略history_start_date。每项包含：

```json
{
  "symbol":"XLK",
  "currency":"USD",
  "price_basis":"total_return",
  "bars":[
    {"date":"2020-01-02","open":100,"high":102,"low":99,"close":101,"volume":1000}
  ]
}
```

上例只展示单条格式，不能单独运行：每个数据集需2..20000行，并覆盖完整预热及评估交易周；最多20组、总行数不超过100000，JSON输入最大8 MiB。日期递增唯一，OHLC为有限正数且范围一致、volume非负；currency为调用方核实的三字母币种。price_basis限 `qfq/total_return/split_adjusted`，不能把unknown或原始价改标签冒充已复权。额外逐bar `adjusted` 若存在，须与数据集口径一致。

### 固定分项实验

1. 每周全池均买入，没有预测挑选；相同周内首个交易日开盘买入、最后交易日收盘卖出。
2. 首次评估前必须有足够完整观察周。历史计数仅使用决策前已结束的周内交易，毛收益>0算赢，持平重置连胜；计数累计，不做未经披露的年度重置。
3. 等权组均分预算；历史组对 `1+wins/buys+streak/(wins+1)` 分数归一化。预热、全池买入、归一化是此独立实验的约定，不宣称为作者的完整策略。
4. 每笔预算含买入比例成本，按合成复权价格计算小数名义份额；逐日收盘盯市、末日扣卖出成本。费用、最终净值和收盘净值最大回撤由核心计算。
5. 评估期完整交易周中任一成员缺bar或零成交量，整组回测停止；不能用周末才知道的数据完整性决定周初是否买入。预热期的不完整周不计观察次数、不删除成员。周跨越评估边界时跳过；首周缺前几天不算完整预热周。

返回 `groups`：market/currency/symbols、实际period、price_basis/data_coverage、evaluated_weeks/skipped_weeks、决策日期和weights、最终trade_counts、equal_weight/historical_weight的净值与成本和指标、年化收益差。指标沿用共享回测函数的四位小数精度。历史计数包括预热，closed_trades仅包含评估期。

`paper_reproduction=allocation_ablation_only`。这不是ROC/MC/神经网络挑选后的组合，更不是现有月度策略的直接前后对照。无外汇、结算、整手、价格限制、盘口、税务或成交量容量模型；比例成本与成交时点只是该模拟假设。

### 数据验收

价格序列跳变大于1.5倍（或低于其倒数）、单日high/low大于1.5时，需要独立核查公司行动后再研究；这是一道保守审查阈值，不是认定真实市场不可能发生该涨跌。复权未知、日期/交易时段不符、未收盘或可疑价格会返回安全JSON错误，`market_data_requires_review` 表示整池未生成回测。检查通过也不证明来源无误。

## CLI 与兼容入口

```bash
python3 tools/sector_rotation.py analyze --input request.json
python3 tools/sector_rotation.py backtest --input request.json
```

skill中的 `scripts/rotation_metrics.py confidence predictions.json` 与 `allocation counts.json` 保留兼容，转调同一核心。所有入口stdout为JSON，错误非零退出，不保存账户或提交订单。
