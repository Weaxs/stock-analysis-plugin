# 回测 DSL 参考

策略用 YAML 定义，示例见 [README · 策略回测](../README.md#策略回测) 与 `strategies/examples/`。本文列出其余可用键、指标和运算符。

## 风控、基准与成交

- `exit.trailing_stop`：移动止损，价格自持仓期间最高收盘价回撤达到该比例即卖出（如 `0.08` = 峰值回撤 8% 触发）；与 `stop_loss` 同时设置时先判固定止损。`stop_loss` 必须为负数、`take_profit` 必须为正数、`trailing_stop` 须在 (0, 1) 内；`position.max_positions` 当前仅支持 1，其他配置明确报错。
- `benchmark`（顶层）：基准对比标的代码（如 `"sh000300"` 沪深300；指数代码必须带 sh/sz 交易所前缀，裸 6 位数字会被当作个股），设置后回测结果附带 `benchmark` 块（基准买入持有收益与 `excess_return` 超额收益）。基准与策略采用同一首日执行口径（next_open 用 open、same_close 用 close），无预热时以策略首条权益日期为起点；实际覆盖不符或首末价格非法时返回 error，不输出超额。
- `simulation.execution`：默认 `next_open`，前日收盘信号、次日开盘加滑点成交；显式 `same_close` 复现旧收盘成交假设。交易同时返回 signal_date/date。延迟的入场信号在新收盘失效时撤单，风险退出 pending 保留至成交。`start` 前可得历史仅用于指标预热，取数长度随策略最大 lookback 增加，交易与权益仍按请求区间统计；`warnings` 出现 `Entry indicator is unavailable` 时必须披露指标不可用及数据质量，不能声称完成预热。零成交量阻止成交；一字板限制只在输入提供同口径历史 limit_up/limit_down 时执行。`execution` 列出阻止成交次数、未成交订单及限制；止损是收盘信号，未模拟盘口排队、部分成交、结算和市场冲击。回测仅接受单一 `qfq`/`total_return`/`none` 复权口径，unknown 或混合口径明确报错。
- `simulation`（顶层）：交易成本。`slippage`（滑点，买卖双边，默认 0.001）、`commission`（佣金，买卖双边，默认 0.0015）、`stamp_tax`（印花税，仅卖出收取，默认 0；A股为 0.0005，各市场税率不同且会调整，按当下实际费率填写）。费率均须在 [0, 1) 区间，越界报错。回测结果附带 `costs` 块（实际生效费率与实付费用合计 `commission_paid`/`stamp_tax_paid`）。

## 可用指标

| 指标 | 说明 | 需要 period |
|------|------|-------------|
| `rsi` | RSI 相对强弱指数；预热不足不可用，全涨窗口为 100，全平为 50 | 是（默认 14） |
| `ma` / `ema` | 简单 / 指数移动平均 | 是（默认 20） |
| `macd_dif` / `macd_dea` / `macd` | MACD 三线 | 否 |
| `volume_ratio` | 当根成交量 / 前 N 根平均成交量（不含当根） | 是（默认 5） |
| `price_change` | 涨跌幅（%） | 否 |
| `bollinger_position` | 布林带位置（0=下轨, 1=上轨） | 是（默认 20） |
| `trend_score` | 独立实验趋势强度，默认 (EMA20−EMA60)/ATR14；fast_period/slow_period 控制 EMA | period 为 ATR 根数（默认 14） |
| `monthly_momentum` | 月初首根有效日K收盘观察过去 period 根对数收益，保持到下一观察；零成交量不计根数 | 是（默认63，要求正整数） |
| `close` / `volume` | 收盘价 / 成交量 | 否 |

## 可用运算符

`>` `<` `>=` `<=` `==` `cross_above`（上穿） `cross_below`（下穿）
