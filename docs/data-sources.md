# 数据源与数据口径

本文汇总各市场数据链路、行情时间与复权口径，以及低频价投、公告和 SEC 数据的取数规则。路由总表见 [README · 支持的市场](../README.md#支持的市场)，CLI 示例见 [README · 独立 CLI 使用](../README.md#独立-cli-使用)。

## 市场路由细节

- 各数据源按优先级自动尝试，前一个失败后自动切换到下一个。未配置相关 env var 的数据源会被跳过。
- 腾讯是港股、美股无需 key 的备用源，报价和 K 线都覆盖。港股排在 yfinance 之后（finnhub 免费版不含港股）；美股排在已配置的 finnhub / longbridge 之后、alphavantage 之前。港股报价使用 `r_hk` 通道（`hk` 通道延迟约 15 分钟）。港美股 K 线走腾讯的 hk/us 前复权接口，`adjusted` 为 `"qfq"`（yfinance 为 `"total_return"`），美股类别股写作 `BRK-B`。港股报价支持 `00700.HK` / `0700.HK` / `700.hk`，成交量单位为股，成交额及市值为港元；PB、换手率、量比在此备用源中返回 null，不套用 A 股字段；美股同理，成交额及市值为美元，报价时间为美东时间。
- 裸 6 位代码一律按**个股**处理（`000001` 是平安银行）；指数需带交易所前缀（`sh000001` = 上证指数），显式前缀代码直接走腾讯/新浪（东财源只认裸代码）。
- **A 股 K 线口径**：股票/ETF 仅接受前复权数据，链路为 akshare → tushare（仅股票）→ efinance → 腾讯（仅 qfq 数据）→ baostock；带前缀的股票/ETF 先去前缀再走同一链路。新浪和 pytdx 的未复权 K 线不参与股票/ETF 容灾。显式指数 `sh000xxx` / `sz399xxx` 保留原始点位，走腾讯 → 新浪 → baostock。上述路由表的完整链路仍适用于实时行情。
- K 线响应保留原有 JSON 数组，每条 A 股记录新增 `adjusted`：股票/ETF 为 `"qfq"`，指数为 `"none"`。Tushare 日线通过 `adj_factor` 前复权，周/月线使用 `stk_week_month_adj` 的 qfq 字段；复权权限不足、因子缺失或源只返回原始价格时继续切换，所有可用复权源都失败时返回 JSON `error`，不降级到未复权股票价格。Tushare 复权接口需至少 2000 积分，见[复权因子](https://tushare.pro/document/2?doc_id=28)与[复权周/月线](https://tushare.pro/document/2?doc_id=365)。
- **粘性优选**：A 股链路（K 线 / 行情 / 市场快照 / 板块成分）会记住最近一次成功的数据源（缓存在系统临时目录，24h 有效），下次调用优先从它开始（单票报价例外：固定先试腾讯，粘性源只在其后的备用源中优先）；若它已失效则自动顺延并更新记录。港美股链路不做粘性——一次 yfinance 瞬时失败不应让后续全天的 quote 降质。
- 日/韩/台股仅 yfinance 一个数据源（免费数据延迟约 15 分钟）；港股 / 美股 / 日股 / 韩股 / 台股的 ETF 与其所在市场股票走相同链路。

## 行情时间与复权口径

行情 JSON 保留原有数组/字典形状，添加 `source`、UTC `fetched_at` 和 `data_as_of`。采集时间不是报价时间，提供方未给时间时为 null；仅提供交易日时保留 YYYY-MM-DD，不补造具体时刻；K 线 data_as_of 是该条的日期标签，周/月线标签不是实际末次成交时间；`as_of_basis=trading_calendar` 表示交易日历推断。A 股复权口径延续 qfq/none；其他市场按来源标为 total_return/qfq/unknown，不把 unknown 当已验证复权。

## 低频价投与增量数据

财报、基本信息、股票代码/名称映射及估值/股东/ETF/分红等慢变数据缓存到 `~/.stock-analysis/cache`（可通过 `STOCK_CACHE_DIR` 指定可写目录，适用于沙箱/只读用户目录），TTL 一天；板块成分/映射一周。行情报价不缓存。缓存写入失败时直接返回已获取数据；缓存文件原子替换，过期/损坏重拉，错误和显式部分失败不缓存；不以过期数据伪装最新数据。原有临时目录的粘性路由和反向成分缓存保留。 同键缓存缺失与 SEC ZIP 下载通过跨进程文件锁协调，等待最多 5 秒；普通缓存锁不可用或超时后仍可直接抓取，持久写入/ZIP 下载则返回错误，避免无界等待。有效缓存命中不要求目录可写。

估值使用可得数据的正值样本分位（`<=当前值`），返回 10/25/50/75/90 分位 Band 与实际起止日期；亏损 PE 分位为 null。当前 AKShare 已无 issue 中提到的 `stock_a_indicator_lg`，个股采用百度 PE-TTM/PB 历史，指数采用乐咕乐股；中证全指等通过中证官方历史接口取得多年滚动 PE，近期总股本 PE1 仍单独保留；PB 缺失时可配置 `LIXINGER_TOKEN` 补充理杏仁总市值加权（mcw）历史，返回每个指标的来源与口径，不拼接不同方法的同一指标。未配置或无权限时 PB 保持未知。A 股 `annual=true, periods=10` 请求十年年报；HK 按源实际可得年数返回，不补造缺失比率。

股东数据保留报告期、公告日或港交所披露日：A 股北向优先返回沪深交易所披露的季度合计数量，省略报告期时查最新已公布季度；十大流通股东中的 HKSCC 推断保留在 `northbound_nominee`，仅作明确标注的兜底，缺席不是零；HK 南向取自港交所 Stock Connect CCASS 记录，不等同完整实益股东名册。A 股财报预约/修订是计划，业绩预告是已公告预估，不是实际业绩。HK 单股优先使用 HKEX 主板/GEM 已公告业绩审批董事会排期，标记 `confirmation=official_board_meeting_notice`、`date_kind=scheduled_board_meeting`；这是计划会议日，不是实际披露日。可按已知财政期末过滤；找不到匹配公告时保留 Yahoo 供应商未来排期，`confirmation=provider_only`，未知报告期不归入指定期次，而另列 `unverified_schedules`。日期转为香港日期，公告表也不承诺覆盖所有港股。

ETF 使用披露资产规模历史（元）与披露持仓，保留持仓原始证券代码，不冒充实时完整申赎篮子；新增 `basket` 返回沪深交易所发布的完整当日 PCF（跨境代码原样保留），包含申赎单位、每篮子证券数量、现金替代规则与比例；校验基金代码、实际日期和全部组件数量，失败只提示不可用，不冒充完整。PCF 是申赎对价，不是实际持仓，证券数量也不是净值权重。`include_full_holdings=true` 时另取最近可得半年度/年度披露股票表，显式指定年份/月并请求较高行数上限，包含股票数量（股）、市值（人民币元）和实际报告期；返回全部收到的表格行，但不声称覆盖债券/现金等全资产或实时持仓，也不以 PCF 数量验证实际持仓。默认不取数百行明细。提供 `benchmark` 可另查基准指数成分。净值折溢价仅在行情/净值日期相同才计算，IOPV 折溢价另列。跟踪误差使用相同连续交易日区间的基金日净值增长与基准收盘收益差，取样本标准差乘 `sqrt(252)`，不填补缺失日期、不猜测基准。两融占比只匹配同一天的人民币元成交额，缺失则 null。

股息历史将预案与已实施分红分开，TTM 股息率以过去365天已实施名义每股现金/最新价计算（税前，未重算送转股）；空历史、已实施记录缺失日期/金额或分红币种与报价币种不一致时，受影响的总额/股息率返回未知，不猜测汇率；历史不完整时不推断连续分红年数。港股日历、现金历史与报价独立降级，保留可用部分并提示失败来源。日历独立返回已公告未来除息日期。A 股排行/选股使用指定报告期已实施方案股息率；HK 指定 `market=HK` 时使用 HKEX/LSEG 公布的年度股息率（百分数），不是当前价 TTM；HK `report_date` 仅支持数据源可得年度的年末。港股股息选股因子也使用同一年度口径。筛选 YAML 可增加 `filters.dividend_yield_min: 3`、`sort_by: dividend_score` 与可选 `dividend_report_date`；缺失为未知，不按0填充。

数据源参考：[AKShare股票接口](https://akshare.akfamily.xyz/data/stock/stock.html)、[指数接口](https://github.com/akfamily/akshare/blob/main/docs/data/index/index.md)、[基金接口](https://github.com/akfamily/akshare/blob/main/docs/data/fund/fund_public.md)、[港交所南向持股](https://www.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk)、[HKEX 年度股息率](https://www.hkex.com.hk/Listing/Sustainability/Shareholder-Value-in-Focus/Dividend-Yield-Ratio?sc_lang=en)、[理杏仁指数基本面接口](https://www.lixinger.com/api/open-api/html-doc/cn/index/fundamental)。

## 公告与港美股研究数据

公告类别为 `all/forecast/express/annual/interim/quarterly/reduction/issuance/inquiry/dividend`。默认最近 90 天，最多 500 条；通过巨潮官方股票目录取得 orgId，沪深京统一使用官方 `szse` 查询栏目。业绩快报和问询使用标题关键词查询。A 股返回 PDF 原文链接，可用 `extract_article`（pypdf）继续提取正文；美股返回 SEC `document_url/filing_url`。`total/truncated` 表示供应商匹配数量及截断，查询无结果与请求失败分开。

港股使用既有 `LIXINGER_TOKEN`，需具备港股接口权限。新增来源覆盖理杏仁非金融公司财务和每日估值，最多十年；金融公司或无权限时回退现有 Yahoo/东财财务和百度估值，返回实际期数和不足提示。财务按 `annual_report` 识别年报，保留财年末、披露日和币种，金额为报告币种，历史比率为百分数；顶层 `roe/profit_margin` 延续 Yahoo 小数比例。理杏仁财报用于增强历史，同时按整组保留可用的 Yahoo 顶层快照，`currency` 是 Yahoo 的财报币种，`market_cap_currency` 是市值币种，`snapshot_source` 标明来源，缺失值不混入其他报告期或币种的历史数字；历史币种和报告日期在 `periods` 每行保留，Yahoo 未提供明确报告日期时顶层 `report_date=null`。快照失败时 `snapshot_available=false`，财报与历史仍返回。会计口径不强行转换为 A 股，估值分位沿用正值样本算法，不拼接不同来源。Yahoo 顶层快照的 `snapshot_units` 明确：`pe=trailing`，`roe/profit_margin/dividend_yield=ratio`（小数），`debt_to_equity=percent`（百分数）；不得把 D/E 百分数当作小数倍数。

SEC 配置 `SEC_USER_AGENT`，包含应用名称与真实联系邮箱，仅通过环境变量提供。单进程最多每秒 2 次请求（多个并行进程需控制账户/IP 总量不超过 SEC 的每秒 10 次），并缓存 ticker/CIK、申报元数据和 XML 解析结果。13F 返回机构最近季度的原始申报，不是实时完整组合；通常在季度后 45 天内披露（节假日顺延），实际数据年龄以季度末为准，2023-01-03 起金额单位为美元，更早申报为千美元。修订文件另列原文链接，避免错误合并补充与重述；发行人 CIK 反查现已通过 `mode=issuer` 支持：从结构化 Schedule 13D/G 校验发行人 CIK、提取 CUSIP，再扫描官方最新已发布 13F 批量窗口。`auto` 默认有当前或已检索历史 13F 时查机构，否则查发行人；Berkshire 等兼具两种身份时显式选择 `issuer`。修订按 `RESTATEMENT` 替换与 `NEW HOLDINGS` 补充重建；无法判断的修订或缺少基础版时总量为 null，并给出 `unresolved_manager_count`。

Form 4 的 `days` 是申报日期窗口，`top=1..20` 限制文件数，返回全部已解析交易。保留多人联报、衍生品、交易代码、脚注、直接/间接持有和修订信息；仅 P/S 标为买/卖，其余行为不推断方向，价格未披露为 null。读取 `truncated/warnings`，原件与修订不要重复计数。风险组合工具 `screen_risk` 仍为 A 股专用。

接口依据：[巨潮公告查询](https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search)、[理杏仁港股财报](https://www.lixinger.com/api/open-api/html-doc/hk/company/fs/non_financial)、[理杏仁港股估值](https://www.lixinger.com/api/open-api/html-doc/hk/company/fundamental/non_financial)、[SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)。

首次下载当前 SEC 包约 100 MB；ZIP 按周缓存，CUSIP 反查结果按天缓存，流式扫描不解压整个持仓表、不增加数据库依赖。返回 `report_date`（持仓日期）、`filing_window_end`（公开申报窗口截止）、`cusip_evidence`（原文映射依据），不把文件披露窗口当作持仓日期。`mapping_complete=false` 表示最近十份结构化 13D/G 可能未覆盖所有证券类别或 CUSIP 变更；缺映射直接报错，不做名称模糊匹配。`complete_for_mapped_cusips` 仅在该报告期没有未解决申报链时为 true，不代表实时全部股东。期权与本金持仓在 `positions` 保留，`shares` 合计只计无 PUT/CALL 的 SH 股票。不能将跨机构合计当作已去重的全市场持股。

13F CLI 预算为 300 秒，宿主另留 30 秒接收输出；其他命令按共享预算表执行。机构查询优先寻找最新已知报告期的原始 13F：较新修订对应的原始版未找到时明确报错，历史分片仍未完全检索时返回 `history_truncated` 与警告。发行人 CUSIP 查询会跳过不可访问/无效 XML，继续核对后续原文；没有任何可验证映射时才报缺失。
