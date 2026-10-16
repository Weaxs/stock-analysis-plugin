# 独立趋势温度：ema_atr_v1

用于用户要求计算趋势温度、寒凉温热状态或复核趋势强度时。调用现有 `get_technical_analysis`，读取 `trend_temperature`；skill 的采集脚本也会在 `technical.trend_temperature` 返回同一结果。无需配置外部温度接口，不保存个人持仓。

## 来源与定位

这是本项目可重复计算的研究基线，`experimental=true`。它不是趋势动物算法，不是塔勒布肥尾指标，也不是原有 100 分综合评分或复盘市场温度。连续分数描述均线相对真实波幅的分离；七档是未经收益校准的显示假设。MUST 同时报告模型、周期、分数、数据日期及实验性质，MUST NOT 将其称为上涨概率、已验证的交易优势或星球官方信号。

`validation_status=not_validated`：真实股票检验没有建立默认入场规则的优势；降低门槛、延长慢线和通道对照也未通过修订研究门槛。默认解释 `direction` 与连续分数，MUST NOT 自行把七档或零轴转换成交易建议。仅在用户明确要求七档时展示研究标签；用户自定条件的回测是研究，不代表推荐。


## 公式与数据

```text
TR_t = max(high_t-low_t, abs(high_t-close_(t-1)), abs(low_t-close_(t-1)))
ATR14_t = 最近14个有效TR的简单均值
score_t = (EMA20(close)_t - EMA60(close)_t) / ATR14_t
EMA_p,t = (2/(p+1))*close_t + (1-2/(p+1))*EMA_p,t-1
```

EMA 用输入首个收盘价初始化（adjust=False），前收缺失的首根 TR 不计算；ATR 使用简单平均，不是 Wilder 平滑。默认至少 60 根 K 线才有分数，至少 61 根才可能有前一期状态。周期按根数：周线即 20/60 周及 14 周波幅，不能与日线信号混称。

`direction/previous_direction` 仅按 EMA 差的正负返回 up/down/flat，未预热或无有效分数时为 null；不是预测方向。七档的“平”包含正负分数，不能代替方向；`previous_state` 和 `transition` 仍只表示显示档位变化。多周期摘要附 `trend_score`、`trend_temperature_state`、`trend_temperature_direction`、`trend_temperature_validation_status`、`trend_temperature_as_of`、`trend_temperature_price_basis` 和 `trend_temperature_unavailable_reason`。各周期日期和价格口径分别核验，不能用顶层日线日期代替周线或月线日期；原 `resonance` 仍基于既有均线方向，不能称为七档温度共振。

价格需有限且为正，满足 low ≤ close ≤ high；日期唯一且递增。技术分析入口会先按日期排序；明确混用复权标记时温度不可用。`price_basis=unknown` 仍需核实价格口径；没有标记不代表已确认复权。保留 `history_start`，输入历史起点不同会影响 EMA 初始化和当前分数。缺失交易日和停牌不能仅靠日期排序鉴别；`previous_as_of` 是输入的前一根 K 线日期，不保证是交易所的上一交易日。

零 ATR、不足预热或无效行情输出 `score/state=null` 和 `unavailable_reason`，不伪造为平。最新 K 线可能尚未收盘，MUST 先确认数据时点再按收盘纪律判断。分数不要求正态分布，但 ATR 和 EMA 都会受极端波动及观察窗口影响；波幅放大也可能使分数降档。

## 七档显示

| score | 状态 |
| --- | --- |
| 小于 -3 | 冻 |
| [-3, -1.5) | 寒 |
| [-1.5, -0.5) | 凉 |
| [-0.5, 0.5] | 平 |
| (0.5, 1.5] | 温 |
| (1.5, 3] | 热 |
| 大于 3 | 沸 |

`previous_score/previous_state` 只取前一根，不跳过不可用记录；`transition` 仅在本根和前根均可用且状态不同时返回。它们由本次行情计算，不需要跨日保存账户。

温/热/沸表示本基线的正向分离程度，不等于星球的同名状态。沸不证明已经过热或将反转；平不表示没有波动。MUST 将描述与用户的入场、退出规则分开，不照搬星球“温转热开仓、危险全平”等标签动作，也不由本字段自动覆盖原 `buy_signal`。

## 历史检验

回测 DSL 可使用 `indicator: trend_score`；默认参数与技术分析相同。既有 `fast_period/slow_period` 调整 EMA，`period` 调整 ATR；快线必须短于慢线且均为正整数。修改参数后七档阈值不自动校准。

不提供默认温度入场示例。用户指定研究规则后，仍须说明原引擎默认 stop_loss=-0.10、take_profit=0.50，省略不代表禁用。预热/零 ATR 为 NaN 时条件不成立，不能凭首个有效分数声称发生穿越。默认次日开盘成交，保留 signal_date/date；涨跌停、跳空及其他限制沿用原引擎。

用户要求检验时按 [策略回测](../../strategy-backtest/SKILL.md)先固定规则，再做时间样本外与跨标的比较，报告反复切换、成本、回撤和交易数量。滚动验证每段重新初始化，测试窗口必须足够覆盖预热及评估期。结合已有 [肥尾收益诊断](fat-tails.md)检查大赢家贡献，不把正偏或集中度当作正收益证明。

理论参考：[Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)提供历史收益趋势研究依据，不验证本模型的 20/60/14 参数和阈值；[Taleb κ](https://arxiv.org/html/1802.05495)衡量样本聚合稳定性，不输出趋势方向。

## 后续版本入口

当前趋势研究优先使用[多期限月度趋势](trend-horizons.md)的trend_horizons，daily、count=360。这里的ema_atr_v1继续保留供兼容和旧实验复现，不将七档作为默认趋势判断。新模型也尚未通过全部跨市场风险门槛，不是自动入场信号。
