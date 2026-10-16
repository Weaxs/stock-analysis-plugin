# 月度右侧趋势策略：上行SMA200与波动预算

开发样本曾通过效果门槛，扩展及历史样本仍未通过；`validation_status=not_validated`。采用它表示使用这套确定的规则，不表示已经验证未来收益。它不是趋势动物的私有温度公式。

## 调用

直接告诉Agent股票池即可，例如“用月度右侧趋势策略分析招商银行、工商银行和中国神华”。Agent解析代码后，调用四宿主共同的 `analyze_trend_strategy`：

```json
{
  "symbols": ["600036", "601398", "601088"],
  "held_symbols": ["600036"]
}
```

`symbols`为必填，其他字段均可选。插件自动获取整个股票池的日K，不要求用户准备文件或手工提供行情。混合市场由Agent按A/HK/US分组调用，各市场资金/币种独立。

|字段|要求|
|---|---|
|`symbols`|同一个市场的完整股票池，1–100只；A用六位代码，HK如`0700.HK`/`00700.HK`，US如`AAPL`；大小写与HK补零统一识别，同股不能重复|
|`as_of`|可选已收盘截止日期`YYYY-MM-DD`，默认交易所最近已收盘交易日；盘中排除当天未收盘K线，按实际日历处理夏令时与半日市|
|`held_symbols`|可选，as_of时真实持股代码，须在股票池中；省略表示初始空仓，Agent明确这个假设，挂买单不算持仓|
|`pending_exit_symbols`|可选，已发出但未成交的退出锁，必须在真实持仓内，价格恢复不撤销|
|`slots`|可选固定容量K，默认ceil(N/3)，不得小于当前真实持股数量|

默认取至少360根daily；历史截止日期会增加取数长度以保留预热，接口有历史总量上限。有效价格不足221根时不填零，也不发明入场条件。所有数据沿现有行情源故障切换；任一成员取数失败，返回`incomplete_pool`和失败代码，不生成缩小股票池的计划。已取数据不合格则返回`invalid_market_data`。核对返回的`as_of/quote_as_of/price_basis`，停牌或报价未更新的成员仍显式列出。持仓只作为本次输入，不保存账户。

### 离线复核

开发者需要复现冻结研究时，CLI仍支持 `python3 tools/trend_strategy.py --input snapshot.json`。离线快照包含market/as_of/histories及可选held_symbols/pending_exit_symbols/slots，每条历史需要date/close/volume，可保留adjusted；日期递增唯一，价格为正且有限，量非负，最大32 MiB普通文件。该入口只用于已有行情的离线复核，正常插件工具使用上面的股票代码参数。

## 固定规则

1. 以股票池正成交量日期并集中的每月第一根收盘为观察点。当天不能按该收盘立即成交，最早是后续有效开盘。月中返回`waiting_for_monthly_observation`，只显示当前条件，不用今天持仓倒推月初交易。
2. 新入同时要求：close>SMA200，且SMA200高于21根有效价格以前的SMA200。按`log(close)-log(SMA200)`降序，代码破同分。
3. 真实旧仓优先占K槽。个股仍在200线上就保持原股份，排名下降或均线斜率转负不触发换仓；无有效报价时保持并占槽。已有200失效则给出`exit_next_valid_open`。已锁定退出始终返回`exit_pending`。
4. 每股sigma63为过去63段对数收益的样本标准差，需64根有效价格。参考中位数取观察日整个固定池有当日报价、sigma有限且>0的股票，包括尚不满足入场资格者。
5. 新入预算系数=`0.8704/K × min(1, median_sigma63 / stock_sigma63)`。低波动不扩大原槽，高波动折减；真实旧仓不按这个比例重新等权，不补仓。0.8704沿用已拟合的开发参数。
6. 没有合格新股可保持现金；空槽不必填满。选择新股不代表已经有可用现金或腾出槽位，须先确认退出真实成交。

## 输出和执行边界

报告`model/validation_status/status/as_of/observation_date/selected/reference_volatility/stocks`。每股有价格口径、报价日期、SMA200及21根前均线、distance、sigma63、资格、action、reason及new_budget_fraction。无充分旧仓历史时返回`insufficient_held_history`，不生成新的交易计划；先补齐历史。

`new_budget_fraction` 是下一执行批次开盘净值乘数，买入费用包含在预算内，**不是目标持仓权重，也不是买入股数**。真实成交还要核对可用现金、已完成卖出、实际整手、停牌/涨跌停/禁售、费用与结算；此工具没有订单簿、现金账本或成交模拟，不会自动下单。不使用复权close直接计算真实股数，不因一笔新仓没成交就当作已持有。

单股`get_technical_analysis`的`trend_temperature`与`trend_horizons`仍可辅助解释，但不是这套组合规则。普通单股YAML回测也不能代替共享资金、旧仓保留和整池风险中位数。本工具不计算历史组合收益，不把信号计划冒充组合回测。
