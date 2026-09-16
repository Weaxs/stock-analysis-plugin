---
name: stock-analysis
description: 综合股票分析：联合技术、基本面、资金、消息和风险数据研判单只股票。用于多维分析、持仓处理或买卖计划；单一策略问题用对应策略 skill。
---

# 综合股票分析

## 意图路由表

快速问答先判断意图，按下表走最短调用链；完整综合研判走下方执行流程（gather 采集管线），不受此表限制。单工具可答的问题按各工具描述中的适用场景选择，本表不再重复。

| 意图 | 调用序列 |
|------|---------|
| 个股买卖时机 | `get_technical_analysis` → `get_market_capabilities` → 当前市场支持的资金/风险工具 → `detect_market_regime`。A股可加 `get_capital_flow`(summary) 和 `screen_risk`；其他市场不得用缺失的资金或风险数据。veto_buy=true 则不入场；大盘下跌时个股看多信号降级为轻仓试错或等待；HOLD/WAIT 给出可观察触发条件 |
| 个股状态速览 | `get_quote` + `get_technical_analysis` |
| 异动归因 | `detect_anomaly` + `get_news` |
| 大盘择时 | `detect_market_regime`；A 股可补充 `get_market_stats` |

## 执行流程

### 第一步：采集数据

运行数据采集脚本（并行获取 quote/kline/technical/financials/capital_flow/news/risk/regime 共 8 项数据）：

```bash
python3 scripts/gather.py <symbol>
```

解析返回的 JSON 数据。如果某个字段为 `null`，表示该数据获取失败，基于可用数据继续分析。

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
- 风险评级（low/medium/high）和一票否决（veto_buy）
- 如果 veto_buy=true，必须在报告中醒目提示

### 第三步：渲染研报

1. 按 [报告 schema](references/report_schema.json) 构建结构化报告。
2. 每个结论只使用成功返回的数据；缺失维度在 `risk_warning` 中说明影响。
3. 调用 `render_stock_report`：快速问答用 `brief`，完整研判用 `full`。
4. 输出渲染后的 Markdown；只在用户要求机器可读数据时附加结构化 JSON。

## 注意事项
- 始终提供风险提示
- 不做绝对化的涨跌预测
- 建议用户结合自身风险偏好做决策
- 如果某些数据获取失败，说明情况并基于可用数据分析
- 如果风险筛查返回 veto_buy=true，必须醒目标注并建议谨慎

完成标准：每个买卖建议都对应可观察的触发条件和失效条件，且所有缺失数据及其影响已说明。
