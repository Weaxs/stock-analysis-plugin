---
name: stock-screener
description: 全市场股票筛选：按市场、估值、成长、趋势或自定义条件生成候选列表并排序。已知个股的深度分析用 stock-analysis。
---

# 全市场股票筛选

批量研究、子任务分工或报告文件交付时，必须读取[宿主研究与报告交付](../stock-analysis/references/host-workflows.md)，使用当前宿主实际提供的能力。

显式筛选条件缺少数据时工具返回 `error`，不能把该条件当作已通过。`filters_applied` 只包含实际执行的条件，基于返回的覆盖情况说明候选范围。

## 执行流程

### 第一步：确定筛选条件

优先从用户请求提取：
- **市场**：A股 / 港股 / 美股（默认A股）
- **筛选偏好**：价值型、成长型、趋势型、还是自定义条件
- **返回数量**：默认 Top 20

未指定的项直接使用默认值并在结果中说明；只有自定义条件彼此冲突时才请用户补充。

### 第二步：运行筛选

运行数据采集脚本进行 L1 量化筛选：

```bash
python3 tools/screener.py screen --market A --top 20
```

可选参数：`--market`（A/HK/US）、`--top`（返回数量）、`--config`（自定义 YAML 配置路径）、`--l2`（L2 量化增强开关，默认关闭）。

**何时开启 `--l2`**：需要对候选股做量化因子精排时开启。开启后逐股补算质量/成长/真实动量/波动率/资金流五类因子，结果更精细，但逐股抓取数据较慢，日常快速筛选保持关闭即可。开启 `--l2` 后候选按 `scores.final` 重新排序，config 里的 `sort_by`/`sort_order` 不生效；且 l2 模式下 `scores.momentum` 仍是 L1 参考值，不参与 final 综合分（final 用的是真实动量组）。

**`market_sentiment` 输出字段**：无论是否开 `--l2`，输出始终带 `market_sentiment`（市场情绪温度 0-100、level、multiplier 0.8-1.2 的 regime 调节器）。`multiplier < 1` 表示市场情绪偏弱，排序和推荐应更保守（收紧入选数量、降低激进度）；`multiplier > 1` 表示情绪偏暖，可适度放宽。

### 第三步：L2 智能排序

对 L1 筛选出的候选股，基于返回的各项数据进行 LLM 智能排序。

注意区分两层"增强"：`--l2` 是工具内的量化因子增强（在 L1 结果上补算因子，发生在排序之前）；本步的 LLM 排序是 skill 流程的最后一环，在量化结果之上综合形态/量价/安全边际做最终排序。两者可叠加使用：先 `--l2` 拿精细因子，再做 LLM 排序。

排序考虑因素：
1. **技术形态质量**：趋势明确 > 震荡 > 趋势不明
2. **量价配合度**：放量上涨 > 缩量上涨 > 放量下跌
3. **指标共振**：多个指标同时看多/看空 > 指标分歧
4. **安全边际**：接近支撑 > 中间位置 > 接近压力

### 第四步：输出推荐列表

```
## 选股结果 — [市场] Top [N]

### 筛选条件
- 市场：...
- 过滤条件：...
- 全市场 → L1 筛选 → L2 排序

| 排名 | 代码 | 名称 | 价格 | 涨跌幅 | PE | PB | 综合评分 | 推荐理由 |
|------|------|------|------|--------|----|----|----------|----------|
| 1    | ...  | ...  | ...  | ...    | .. | .. | ...      | ...      |

### 重点关注
对排名前3的股票给出简要分析

### 风险提示
- 筛选基于历史数据，不代表未来表现
- 建议结合个股深度分析后再做决策
```

每个候选必须读取 `factor_coverage.available/missing/ratio/effective_weights`。ratio 是已覆盖因子的原始权重之和；L1 与 L2 的 effective_weights 均按可用因子重归一化，缺失或非有限值不按零分计，排序前先按实际可用因子计算综合分，effective_scores 保留实际参与计算的各组分数；用 sum(effective_scores[g] × effective_weights[g]) × market_sentiment.multiplier 核对 scores.final（四舍五入至一位）。scores.momentum 仍保留 L1 参考值，L2 贡献以 effective_scores.momentum 为准。相同分数但缺失因子不同的候选不能视为同等证据，报告缺失项和覆盖度，不以 `enriched=true` 代替覆盖判断。`scores.final=null` 表示没有足够因子计算，不能作为高分候选推荐。

完成标准：列出实际生效的市场、过滤条件、排序口径和候选数量，且推荐理由只使用返回数据，并说明因子覆盖。

## 策略模板

如果用户没有明确条件，提供预设策略：
- **价值型**：低PE + 低PB + 高ROE + 稳定分红
- **成长型**：中等PE + 高营收增长 + 高换手率
- **趋势型**：放量突破 + 均线多头 + 高量比
- **低估反转**：超跌 + 低PB + 资金净流入

## 股息因子（A股，可选）

配置 `filters.dividend_yield_min` / `dividend_yield_max` 或 `sort_by: dividend_score` / `dividend_yield` 时才拉取股息数据。`dividend_report_date` 可指定报告期，默认上一年年末。这里使用已实施方案的**报告期股息率**（百分数），不是滚动十二个月股息率；缺失为未知，使用股息阈值时排除。启用后 value 分项加入股息横截面评分，未启用时不增加股息请求。

```yaml
filters:
  dividend_yield_min: 3
sort_by: dividend_score
sort_order: desc
dividend_report_date: "2025-12-31"
```

筛选结果仍需用 `get_dividends(mode="history", symbol=...)` 核查已实施现金分红、连续支付年数与除息时间，不能把高股息直接解释为低风险。

### 补充数据覆盖

股息筛选支持 A/HK：A 股为报告期已实施方案股息率，HK 为 HKEX/LSEG 年度股息率。两者都不是当前价 TTM；使用结果的 yield_basis 和 report_date，缺失不当零。

选股的 top 必须为正整数；超时预算按候选数量与采集批次计算。
