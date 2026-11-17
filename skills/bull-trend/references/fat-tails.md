# 肥尾、非对称风险与趋势收益诊断

本参考供趋势 skill 和趋势策略回测按需读取。数值由本 skill 的 [计算脚本](../scripts/tail_analysis.py) 执行，无需独立工具注册。保留输入、运行参数和输出；没有执行环境或数据时只解释方法，数值标为未计算。

## 调用脚本

从插件根目录使用安装了现有 numpy 依赖的 Python 运行（POSIX 通常为 `.venv/bin/python3`，Windows 为 `.venv/Scripts/python.exe`）：

```bash
python skills/bull-trend/scripts/tail_analysis.py returns historical-returns.json --n 2 10 30 --draws 20000 --seed 42
python skills/bull-trend/scripts/tail_analysis.py returns historical-returns.json --current 0.05
python skills/bull-trend/scripts/tail_analysis.py returns historical-returns.json --tail-prob 0.05 --threshold 0
python skills/bull-trend/scripts/tail_analysis.py trades backtest-result.json
python skills/bull-trend/scripts/tail_analysis.py fragility scenarios.json --center 0
```

`returns` 输入是同周期历史对数收益的 JSON 数组，如 `[0.01, -0.02, 0.005]`，以小数表示；`--current` 的当前收益另传，必须从历史输入中排除。脚本不转换价格、不抓行情、不识别日期，因此先按下文校验并记录数据口径。`trades` 输入是含完整 `trades` 数组的回测 JSON。`fragility` 输入是情景损益表，格式见下文。脚本 stdout 输出 JSON，失败返回 `error` 并以非零状态退出。

κ 默认 n0 = 1，以固定种子做 iid 经验重抽样，输出每个 n 的 κ 和平均绝对偏差；换种子重复运行检查 Monte Carlo 波动，换历史窗口检查数据敏感性。输出没有总体置信区间。`sample_count` 是历史收益数量，`draws` 是模拟轮数，两者不能混称样本量。`current_event.percentile` 为 0..1，`upper_exceedances` / `lower_exceedances` 是历史严格超过 / 低于当前收益的次数。

## 先确定分析对象

| 用户问题 | 分析对象 | 可交付结论 |
|---|---|---|
| 收益分布有多肥尾 | 固定周期的历史收益 | 样本尾部特征、κ 的条件性估计 |
| 当前涨跌是否极端 | 当前收益与此前同周期收益 | 历史分位或阈值超越，不能推导后续方向 |
| 趋势系统是否靠大赢家 | 策略平仓盈亏 | 盈利集中度及移除大赢家后的敏感性 |
| 涨跌空间是否对称、赔率是否划算 | 历史收益或交易计划 | 尾部盈亏比、Omega、赔率与盈亏平衡胜率 |
| 头寸是否怕波动（脆弱性） | 冲击情景下的头寸损益 | 凸性 H、上下行盈亏比 |

市场收益的厚尾、策略收益的正偏和趋势延续是不同性质。塔勒布的度量回答统计稳定性问题；下文的分位和盈利贡献是本项目的诊断方法，不称为塔勒布交易信号。

肥尾和非对称风险是两层问题：肥尾描述**分布**（极端值多大、多常见，用 κ 和分位），非对称描述**暴露**（同样幅度的上下波动带来的损益是否对称，用凸性 H）。同一只股票，裸多头是线性暴露，买认购期权是凸的，卖认沽期权是凹的；分布相同，风险完全不同。

## 数据口径

使用 `get_kline` 或现有 `stock_data.py kline`，核对实际返回的日期范围、周期、数量和复权标记。股票历史价格保持一致复权口径，指数保持原始点位；先处理乱序、重复日期和无效价格，说明停牌或缺失导致的时间间隔，保留真实极端收益。

默认分析对数收益 `r_t = log(P_t / P_{t-1})`，仅使用正价格。日、周收益分别计算；周收益从周末价格或同一周日对数收益之和取得，避免把重叠多日收益当作独立样本。策略盈利贡献使用货币盈亏，不混用价格收益。

申请多年的同口径历史，报告实际样本量；120 根日K线只能给出短窗口描述。不存在通用的“够用样本数”；长历史也可能混合不同制度或波动阶段，按分段结果检查稳定性。

## 当前事件的历史分位

以当前事件之前的同周期收益 `r_1,...,r_N` 为参考，对当前收益 `r_*` 计算经验分位：

```text
F_hat(r_*) = count(r_i <= r_*) / N
```

报告单侧方向、参考区间、N 和超越次数。例如当前上涨高于历史样本的 99% 分位，仅说明它相对该样本少见。样本之外的事件频率不可由零超越次数认定为零；回测每个时点只使用此前数据计算阈值。

## 塔勒布的 κ：有限样本聚合速度

来源：[Taleb, How Much Data Do You Need? A Pre-asymptotic Metric for Fat-tailedness](https://arxiv.org/html/1802.05495)，Definition 1、Equation (1) 和 III-B。

设 `X_i` 独立同分布且具有有限绝对一阶矩：

```text
S_n = sum(X_i, i=1..n)
M(n) = E[abs(S_n - E[S_n])]
kappa(n0, n) = 2 - log(n/n0) / log(M(n)/M(n0)), n > n0 >= 1
```

`M(n)` 是 n 项之和相对其均值的平均绝对偏差，不是中位数绝对偏差，也不是 n 项均值的偏差。高斯分布满足 `M(n)/M(n0) = sqrt(n/n0)`，因此 κ = 0。在论文适用的分布中，较高 κ 表示增加样本带来的均值稳定性改善较慢；κ 与 n0、n 有关。

脚本计算的是**基于历史经验分布的条件性估计**：用历史均值中心化后计算 `M_hat(1)`，有放回独立抽样 n 项相加，以绝对和值的模拟均值估计 `M_hat(n)`，代入 n0 = 1 的公式。

零离散度或零对数分母时，脚本返回 `kappa: null` 和 `unavailable_reason`。估计超出预期范围时报告原值并检查假设和数值稳定性，不强行裁剪到 [0,1]。

股价收益可能具有自相关、波动聚集和制度变化，独立抽样会抹去时间结构。按真实连续时间聚合或使用块抽样时，应将结果标为包含时间依赖的描述性统计，与原论文的 iid κ 区分。仅重抽历史不能创造尚未出现的极端事件，也不能证明总体尾部已被充分观测。

κ 不能区分获利方向，不能预测趋势启动。论文中 κ > 0.15 是对正态近似可靠性的启发式提醒，不是入场、止盈或“已到肥尾”的通用阈值。

## 趋势策略的盈利集中度

来源：[CFM, Making fat right tails fatter with trend following… most of the time](https://www.cfm.com/making-fat-right-tails-fatter-with-trend-following-most-of-the-time/)。趋势追踪的凸性和正偏依赖策略与时间尺度；单次跳空或快速反转未必能被系统捕获。

使用 `run_backtest` 返回的 `trades`，仅取 `type == "sell"` 的平仓记录，以 `pnl` 的货币金额计算；`pnl_pct` 是小数比例，不能当百分数或与货币盈亏相加。先确认返回结果是否完整、是否存在未平仓头寸以及费用覆盖口径。

```text
G = sum(max(pnl_i, 0))
L = sum(min(pnl_i, 0))
T = G + L
C_k = 最大 k 笔正盈利之和 / G
T_without_k = T - 最大 k 笔正盈利之和
```

默认报告最大 1 笔和最大 3 笔，正盈利笔数不足时使用实际数量并注明。G = 0 时集中度不可计算；同时报告交易笔数、G、L、最差单笔及回测已有的最大回撤。使用 G 作分母，避免总净利润接近零时比率失真。

`T_without_k` 是已实现盈亏的删除敏感性分析，保留其余交易金额不变；它不是删除交易后重新模拟的资金曲线或收益率。未平仓盈亏另列，不能把 T 当作账户总收益。少数赢家贡献高符合某些趋势系统的结构，但不能单凭这一点证明策略有效或失效；结合成本、回撤、不同区间和样本外表现评价。

现有回测有固定止盈默认值；省略 `take_profit` 不会关闭止盈。验证保留右尾的退出规则前，检查实际引擎和 DSL 的支持情况，将无法表达的规则明确列为未验证。

## 非对称风险：公式模板

以下全部用经验数据或用户给定的情景，不拟合分布。不要用偏度、峰度做核心指标：肥尾下高阶矩样本估计极不稳定，一个极端值就能改写结果。

### 收益分布的尾部不对称（`returns` 的 `asymmetry`）

```text
q_lo = 分位(r, p)，q_hi = 分位(r, 1-p)，p 由 --tail-prob 设定，默认 0.05
ETL = mean(r_i | r_i <= q_lo)      左尾平均亏损（负数）
ETG = mean(r_i | r_i >= q_hi)      右尾平均盈利
尾部盈亏比 = ETG / |ETL|           ETG <= 0 或 ETL >= 0 时为 null
Omega(τ) = sum(max(r_i-τ, 0)) / sum(max(τ-r_i, 0))   τ 由 --threshold 设定
```

尾部盈亏比 > 1 说明样本里大涨的幅度大于大跌；Omega > 1 说明高于 τ 的收益总量超过低于 τ 的总量。没有低于 τ 的收益时 Omega 为 null。尾部样本数 `lower_tail_count` / `upper_tail_count` 很少时只作描述；N = 500、p = 0.05 时每侧约 25 个点。

### 交易赔率（`trades` 与交易计划）

```text
胜率 p = 盈利笔数 / 平仓笔数
赔率 b = 平均盈利 / |平均亏损|                     脚本字段 payoff_ratio
盈亏平衡胜率 p* = 1 / (1 + b)                       脚本字段 breakeven_win_rate
单笔期望（以 1 单位风险计）E = p*b - (1-p)
```

单笔计划用 R 倍数，无需脚本：

```text
R = (目标价 - 入场价) / (入场价 - 止损价)
p* = 1 / (1 + R)
```

例：入场 10、止损 9、目标 13，R = 3，p* = 25%；胜率估计高于 25% 才有正期望。止损可能跳空击穿，实际亏损按历史跳空幅度或最坏情景补充说明。

### 暴露的凸性与脆弱性（`fragility`）

来源：[Taleb, Canetti et al., A New Heuristic Measure of Fragility and Tail Risks, IMF WP/12/216](https://www.imf.org/en/publications/wp/issues/2016/12/31/a-new-heuristic-measure-of-fragility-and-tail-risks-application-to-stress-testing-26222)（[arXiv 1208.1189](https://arxiv.org/abs/1208.1189)）。

设 f(x) 是冲击幅度 x（如标的涨跌幅、利率变动）下的头寸损益，c 为中心情景：

```text
H(Δ) = (f(c-Δ) + f(c+Δ)) / 2 - f(c)
H < 0  凹，脆弱：波动越大越亏，伤害随冲击加速
H > 0  凸，反脆弱：波动越大越有利
H ≈ 0  线性
上下行盈亏比 = (f(c+Δ) - f(c)) / (f(c) - f(c-Δ))
```

输入模板（`capital` 可选，用于输出 `h_over_capital`）：

```json
{
  "capital": 100000,
  "scenarios": [
    {"shock": -0.2, "pnl": -2000},
    {"shock": -0.1, "pnl": -2000},
    {"shock": 0, "pnl": -1500},
    {"shock": 0.1, "pnl": 3500},
    {"shock": 0.2, "pnl": 8500}
  ]
}
```

这是买入认购期权的形状，H 为正。卖出认沽、加杠杆被强平、高负债公司的股权在下跌端加速亏损，H 为负。必须包含中心情景和对称的 ±Δ；多组 Δ 时看 H 是否随 Δ 增大而加速变负，这是脆弱性的核心信号。

`analyze_portfolio_risk` 的压力测试按 beta 线性推算，其结果作为 f(x) 时 H 恒为 0；非线性只来自期权、止损、杠杆与保证金、可转债，或公司层面的负债与经营杠杆。公司层面可用不同营收或利率冲击下的净利润或权益价值作为 f(x)，情景须由用户或财报数据给出并注明假设。

## 完成标准

报告指出分析对象、数据来源及区间、周期、复权口径、样本量、公式与估计方法、实际数值或未计算原因。κ 结果还需注明 n0、n、抽样假设和敏感性；盈利集中度需注明平仓与未平仓口径；非对称结果需注明 tail-prob、阈值或情景来源。交易建议依据趋势和风险规则，统计结果仅提供其能支持的证据。
