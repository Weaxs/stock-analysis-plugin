---
name: strategy-backtest
description: 策略回测：定义或读取 AlphaEvo YAML 策略，回测收益、回撤和交易统计。仅在用户明确要求时进行参数优化。
---

# 策略回测与优化

生成股票池月度右侧趋势计划时，先读 [月度右侧趋势策略](../bull-trend/references/monthly-trend-strategy.md)，直接以股票代码调用 `analyze_trend_strategy(symbols, ...)` 自动取行情，无需用户准备文件。这是整池信号/预算分析；共享资金历史绩效仍依据冻结研究，MUST NOT 用单股YAML或信号计划冒充组合回测。报告尚未通过跨样本效果验证的边界。

开始前读取[短线交易决策框架](../stock-analysis/references/short-term-trading.md)的风险单位和回测约束。回测用于反证策略，不把样本内收益包装成预测。

解读回测时，`period` 是实际取得的数据期间，`requested_period` 保留用户请求，`warnings` 提示两者差异。年化收益按实际期间计算；覆盖不完整时明确说明，不能声称完成了完整请求期间的验证。买入预算包含佣金，`position.size` 为 0..1 的资金比例。

## 无状态分析扩展

滚动样本外、参数敏感性或跨标的验证时，MUST 读取[无状态研究工具](../stock-analysis/references/stateless-analysis.md)的 validate_backtest 分支。复用 get_kline，传完整OHLCV和事先确定的DSL候选；样本内sensitivity不等于样本外验证。

## 工作流程

### 第一步：策略定义

帮助用户将交易思路转化为 YAML 策略文件。策略 DSL 格式：

```yaml
name: "策略名称"
version: "1.0"
description: "策略描述"

parameters:
  param1: value1

entry:
  conditions:
    - indicator: "rsi"        # 指标名
      period: 14              # 指标周期
      operator: "<"           # 比较运算符
      value: 30               # 阈值
  logic: "all"                # all=AND, any=OR

exit:
  conditions:
    - indicator: "rsi"
      period: 14
      operator: ">"
      value: 70
  logic: "any"
  stop_loss: -0.05            # 止损比例
  take_profit: 0.15           # 止盈比例

simulation:
  execution: next_open        # 默认：前日收盘信号，次日开盘成交
  slippage: 0.001             # 滑点（买卖双边），默认 0.001
  commission: 0.00025         # 佣金（买卖双边，券商自定；A股常见万2.5），默认 0.0015
  stamp_tax: 0.0005           # 印花税（仅卖出收；A股 0.05%，其他市场按当下实际费率，无则省略），默认 0

position:
  size: 1.0                   # 仓位比例
  max_positions: 1            # 最大持仓数
```

**可用指标**：rsi, ma, ema, macd_dif, macd_dea, macd, volume_ratio, price_change, bollinger_position, close, volume

`monthly_momentum` 是共享核心的月度观察对数收益：`period` 默认63，支持21/63/252等正整数；月初首根正成交量日K收盘计算并保持到下次月度观察，次日开盘才可能成交。零成交量记录不计回溯根数。MUST 读取 [多期限月度趋势](../bull-trend/references/trend-horizons.md)，不能把单期限YAML等同三子策略组合。需要在观察日已有period+1根有效日K；默认回测起点会截断输入历史，不能据此声称提前完成预热。三期限同资金起点的完整研究复现使用参考里的研究脚本。

独立趋势强度可用 `trend_score`，默认 `(EMA20-EMA60)/ATR14`，ATR 为真实波幅简单平均。`fast_period/slow_period/period` 分别控制快 EMA、慢 EMA、ATR，要求正整数且快线小于慢线；默认 60 根预热，预热或零 ATR 不触发条件。解释七档显示、初始化与样本外验证前 MUST 读取 [独立趋势温度](../bull-trend/references/trend-temperature.md)。它不是星球温度或肥尾信号，原固定止损/止盈默认值仍生效。

该字段的 `validation_status=not_validated`。已有真实股票试验未支持默认温度入场规则；MUST NOT 自行补出“温转热买、降温卖”或零轴规则作为推荐策略。用户指定规则时可运行研究回测，但 MUST 同时报基准、成本、回撤及验证局限。

**可用运算符**：`>`, `<`, `>=`, `<=`, `==`, `cross_above`, `cross_below`

### 第二步：运行回测

A 股股票/ETF 回测统一使用前复权 K 线（每条记录 `adjusted="qfq"`），显式指数使用原始点位（`adjusted="none"`）。复权源不可用时停止本次回测并说明缺失，不混用未复权历史价格。

运行回测脚本：

```bash
python3 tools/backtest.py run <path.yaml> <symbol> --start <date> --end <date>
```

参数说明：
- 前两个为位置参数，依次为：策略 YAML 文件路径、股票代码
- `--start` / `--end`：回测时间范围（可选，默认近一年）
- 初始资金默认 100 万，可用 `--capital` 调整

默认 `simulation.execution=next_open`：信号在前一交易日收盘确定，下一有效交易日按开盘价加滑点成交；交易同时保留 `signal_date` 与实际 `date`。最后一天新信号没有后续开盘时不能成交。需要复现旧结果时显式使用 `same_close`，并说明它按信号日收盘成交，可能高估可执行收益。零/缺失成交量（已有 volume 字段时）阻止成交；一字涨跌停仅在输入提供同口径历史 `limit_up/limit_down` 时保守阻止，不猜 ST、IPO 或 ETF 限价。

读取 `execution.model/blocked_orders/unfilled_order/limitations` 并说明未成交情况。止损、止盈和移动止损均是收盘信号，不承诺盘中按阈值成交；次日跳空按实际开盘价计算。没有历史限价数据时，不能声称已验证涨跌停可成交性。

### 第三步：分析结果

回测完成后，按共享框架的风险单位和回测约束解释原始结果。针对本工具：

- 从 `metrics` 和 `trades` 计算期望值并与盈亏比一起报告。
- 报告最大回撤、连续亏损、交易次数和平均持有期；结果未携带初始风险时，将 1R 分布标为不可计算。
- 使用用户指定的互斥日期区间分别运行样本内和样本外回测。
- 成本假设引用结果中的 `costs` 字段（实际生效的佣金/滑点/印花税费率与实付费用合计），未模拟的费用与成交约束列入限制。

趋势策略或用户要求肥尾收益诊断时，读取 [肥尾度量与趋势收益诊断](../bull-trend/references/fat-tails.md)的盈利集中度部分，基于平仓 `pnl` 计算大赢家贡献和删除敏感性。只有用户询问市场收益分布时才进入 κ 分支。

未模拟的结算、涨跌停、复权或特定市场费用列入限制，结论限定为该模拟条件下的历史表现。

### 第四步：诊断

根据回测结果诊断问题并提出可选调整。诊断提示只是待检验假设，不自动接受参数建议：

**常见问题 → 优化方向**：
- 胜率低 → 收紧入场条件、增加过滤因子
- 盈亏比低 → 提高止盈、收紧止损
- 回撤大 → 减小仓位、增加止损保护
- 交易次数少 → 放宽条件、缩短信号周期
- 交易次数多 → 增加冷却期、提高信号门槛

### 第五步：可选优化

仅当用户明确要求优化时：

1. 每轮只修改一类参数或条件
2. 重新回测并与基线对比
3. 按共享框架保留独立样本外区间
4. 最多迭代 3 轮；期望值、回撤或样本量没有实质改善时停止
5. 保留基线结果，并把样本内改善标记为待样本外验证

## 输出格式

```
## 回测报告 — [策略名称] on [股票代码]

### 策略概要
- 名称：...
- 入场条件：...
- 出场条件：...
- 止损/止盈：...

### 核心指标
| 指标 | 值 | 解释 |
|------|----|------|
| 总收益率 | ... | 相对基准与回测区间解释 |
| 期望值 | ... | 结合胜率、盈亏比和样本量解释 |
| ... | ... | ... |

### 交易明细
最近 N 笔交易列表

### 诊断
- 优势：...
- 问题：...
- 优化建议：...

### 下一步
建议的参数调整方案
```

完成标准：报告包含回测区间、样本内/外边界、初始资金、交易次数、持有期、期望值、收益/回撤指标、成本假设及未模拟的市场约束；优化分支还需提供基线对比。

## 示例策略

参考 `{baseDir}/strategies/examples/` 目录中的示例：
- `rsi_oversold.yaml` — RSI 超卖反弹
- `ma_crossover.yaml` — 均线金叉

市场环境只限制新开仓的三期限月度实验，使用 [bull-trend参考](../bull-trend/references/trend-horizons.md#市场环境入场实验) 与 `../bull-trend/scripts/market_trend.py`，输入两份带预热的daily OHLCV JSON。普通单期限YAML不会自动提供该组合或市场数据；未知市场历史禁止新入，市场转弱不冒充个股退出。初始投资时不能把预热虚拟信号当实际持仓。
