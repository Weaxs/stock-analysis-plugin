---
name: strategy-backtest
description: 策略回测：定义或读取 AlphaEvo YAML 策略，回测收益、回撤和交易统计。仅在用户明确要求时进行参数优化。
---

# 策略回测与优化

开始前读取[短线交易决策框架](../stock-analysis/references/short-term-trading.md)的风险单位和回测约束。回测用于反证策略，不把样本内收益包装成预测。

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

position:
  size: 1.0                   # 仓位比例
  max_positions: 1            # 最大持仓数
```

**可用指标**：rsi, ma, ema, macd_dif, macd_dea, macd, volume_ratio, price_change, bollinger_position, close, volume

**可用运算符**：`>`, `<`, `>=`, `<=`, `==`, `cross_above`, `cross_below`

### 第二步：运行回测

运行回测脚本：

```bash
python3 scripts/gather.py --strategy <path.yaml> --symbol <symbol> --start <date> --end <date>
```

参数说明：
- `--strategy`：策略 YAML 文件路径
- `--symbol`：股票代码
- `--start` / `--end`：回测时间范围（可选，默认近一年）
- 初始资金默认 100 万

### 第三步：分析结果

回测完成后，按共享框架的风险单位和回测约束解释原始结果。针对本工具：

- 从 `metrics` 和 `trades` 计算期望值并与盈亏比一起报告。
- 报告最大回撤、连续亏损、交易次数和平均持有期；结果未携带初始风险时，将 1R 分布标为不可计算。
- 使用用户指定的互斥日期区间分别运行样本内和样本外回测。
- 只陈述策略配置或结果中可确认的手续费、滑点与成交约束，其余列入限制。

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
