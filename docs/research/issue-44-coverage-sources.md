# Issue #44 剩余覆盖的一手来源核验

核验日期：2026-10-12；只记录公开 HTTP 实测与官方口径，不改业务代码。探针和原始响应放在 `/tmp/stock-coverage-probes/`，不提交。接口返回 HTTP 200 不等于有数据，须检查业务错误、日期和完整性。

## 1. 北向季度个股持股：沪深官方接口可用

[沪股通官方页面](https://www.sse.com.cn/services/hkexsc/hgtscsj/hgtzqcysl/)明示每季度第 5 个沪股通交易日披露上季度单只证券合计持有数量；其[页面 JS](https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_southboundStock_2021.js)提供当前接口。公开 GET：

- URL：`https://query.sse.com.cn/commonSoaQuery.do`
- 参数：`sqlId=FW_HGTZL_HGTSCSJ_HGTZQCYSL_L&tradeDate=20260630&secCodeName=&isPagination=true&pageHelp.pageSize=2000&pageHelp.pageNo=1`；请求头 `Referer: https://www.sse.com.cn/`。
- `tradeDate` 必须 `YYYYMMDD`，`2026-06-30` 实测返回空。`secCodeName=600519` 实测返回一条，`totalHoldings=53,711,656.00`；日期留空时服务端返回最新已公布的 `20260630`，不能用最近季末代替最新公布期。
- 全市场实测 `pageHelp.total=2039`、`pageCount=2`，第一页 2000，第二页 39；`result` 字段为 `secCode/secAbbr/tradeDate/totalHoldings`。含 ETF，完整遍历后仍须辨别证券类型。数量为原始证券单位，不能除以/乘以一万；官方表头仅称“合计持有数量”，没有“万股”标记。股票按股、ETF按份解读，单位由证券类型明确标注。

[深股通官方页面](https://www.szse.cn/szhk/szhkshareholding/szholdamount/index.html)源代码指定 `SGT_SGTCGSL`。公开 GET：

- URL：`https://www.szse.cn/api/report/ShowReport/data`
- 参数：`SHOWTYPE=JSON&CATALOGID=SGT_SGTCGSL&txtDate=2026Q2&txtZqdm=&tab1PAGENO=1`。单股查询 `txtZqdm=000001` 实测 `cysl=736,878,410`。
- `txtDate` 必须 `YYYYQn`；`2026-06-30/20260630/2026-2`均未返回数据。响应是数组，第一元素 `metadata/data/error`；行字段 `zqdm/zqjc/cysl`，代码需去尾空格。
- 全市场实测 2271 条、114 页、每页 20。`tab1PAGESIZE=2000`被忽略；`tab1PAGENO=114`实测 11 条。含配债记录，如最后一行 `381499 维科配债`，不能全部当股票。
- 空季度返回 `error=请指定查询周期后，再进行查询`，metadata 没有最新公布期。可从沪市服务端最新公布期获得候选季度，再验证深市该季度；不能从空结果声称持股为零。深市完整 114 页尚未全部下载校验去重，第一页/末页及单股已验证。

旧东方财富/AKShare每日北向持股端点没有用于证明新季度数据；上市公司前十大股东中的 HKSCC 名义账户不等价于完整沪深股通持股库。

## 2. 中证全指 000985 长期估值

本地 AKShare `stock_index_pe_lg/stock_index_pb_lg` 的名称映射不含“中证全指”，不能宣称已覆盖。官方 [000985indicator.xls](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/indicator/000985indicator.xls)实测 20 行（2026-09-14 至 2026-10-09），只含 PE1（总股本）、PE2（计算用股本）、股息率1/2，没有 PB；并非长期 PE-TTM/PB。PE是倍数、股息率是百分数，日期整数`YYYYMMDD`。

[官方 factsheet](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000985factsheet.pdf)本次网页检索到 2025-10-31快照，滚动市盈率与市净率均为`--`，不能据此填补长期 PB。主任务另行实测 `stock_zh_index_hist_csindex` 的官方 `csindex-home/perf/index-perf` 多年滚动 PE 返回 1211 个五年样本；本记录未重复该探针。长期 PB 仍缺一条已核验、无凭据的官方或已装 provider 数据路径；不得用全市场 PB或股票`000985.SZ`（大庆华科）冒充指数。

## 3. ETF完整每日 PCF：沪深及跨境样本可用

### 沪市

[上交所 PCF页面](https://www.sse.com.cn/disclosure/fund/etflist/)及[官方 JS](https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_fundInformation_2021.js)提供下载：

`GET https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=510300`，请求头 `Referer: https://www.sse.com.cn/`。实测200 XML，`TradingDay=20261012`、`RecordNumber=300`，`ComponentList/Component`实际300条，`CreationRedemptionUnit=900000`。

同接口`fundCode=513100`（跨境纳指ETF）实测 XML，101个组件，最小申赎单位500000。组件有`InstrumentID/InstrumentName/Quantity/SubstitutionFlag/CreationPremiumRate/RedemptionDiscountRate/SubstitutionCashAmount/UnderlyingSecurityID`，美国股票代码如 AAPL；不要要求六位中国证券代码，或把`UnderlyingSecurityID=9999`当无效证券。日期`YYYYMMDD`；Quantity是每一个最小申赎单位的证券数量，NAV/现金金额为人民币元，溢价比例0.1表示10%。下载入口只有最新期参数，历史同接口的有效日期参数未验证。

### 深市

[深交所PCF列表](https://www.szse.cn/disclosure/fund/currency/)公开 GET：

`https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=sgshqd&txtJCorDH=159915&txtStart=2026-10-09&txtEnd=2026-10-12`

实测2条。`data[i].jjdm`是HTML，`encode-open`给`/files/text/etf/ETF15991520261012.txt`，下载href的`filename`给`pcf_159915_20261012;159915ETF20261012`。文件host必须由官方[下载页](https://www.szse.cn/modules/report/views/eft_download_new.html?path=%2Ffiles%2Ftext%2FETFDown%2F&filename=pcf_159915_20261012%3B159915ETF20261012&opencode=ETF15991520261012.txt)确定为`reportdocs.static.szse.cn`，`www.szse.cn`下相同文件路径实测404。

- [159915 XML](https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_159915_20261012.xml)：200；namespace=`http://ts.szse.cn/Fund`；`RecordNum=TotalRecordNum=100`，实际100组件，申赎单位1000000。
- [159915展示文本](https://reportdocs.static.szse.cn/files/text/etf/ETF15991520261012.txt)：200，GB18030/中文固定排版，XML优先。
- [159941跨境纳指ETF XML](https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_159941_20261012.xml)：200；`RecordNum=1`，`TotalRecordNum=102`，包含`159900 申赎现金`及海外证券，不能只拿RecordNum=1认定全部篮子只有现金。
- 深市组件字段为`UnderlyingSecurityID/UnderlyingSecurityIDSource/UnderlyingSymbol/ComponentShare/SubstituteFlag/PremiumRatio/CreationCashSubstitute/RedemptionCashSubstitute`。`Components/Component`与沪市schema不同；日期、单位及基金级现金部分须同时保留。

PCF是当日申购赎回对价篮子，含现金替代规则、现金差额与申赎单位；证券数量不能直接叫实际基金持仓，也不能当NAV权重。完整篮子 != 完整真实资产组合，尤其现金替代、跨境现金申赎。实际基金组合仍需定期报告的资产组合/所有股票投资明细，并标报告期，不能把PCF+前十大持仓拼成“完整实时持仓”。本次没有验证所有ETF品类、历史PCF保存年限或半年度报告完整资产解析，因此上述样本通过仅证明该公开路径可实施。

## 4. 次要项：HK股息排行榜与披露日历

[HKEX官方股息率网页](https://www.hkex.com.hk/Listing/Sustainability/Shareholder-Value-in-Focus/Dividend-Yield-Ratio?sc_lang=en)已公开全体发行人表格（检索可见按比率排序），供应商LSEG，latest annual及2025/2024/2023/2022口径；这不是今日TTM股息率。页面[官方JS](https://www.hkex.com.hk/lhkexw/js/capitalefficiency.js)调用`getcapitalefficiencylist`，带`LabCI.getToken()`和页参数；未验证该服务URL/公开token获取链，不能宣称当前已有稳定无凭据批量API。另一[证券报价页](https://www.hkex.com.hk/Market-Data/Securities-Prices/Equities?sc_lang=en)公开Dividend Yield(%)及筛选，但同样未核验全市场API完整分页及计算口径。

[HKEXnews董事会通知列表](https://www1.hkexnews.hk/search/predefineddoc.xhtml?predefineddocuments=5)公开可读通知标题、公告时间和代码；[HKEX官方FAQ](https://www.hkex.com.hk/Global/Exchange/FAQ/Getting-Started?sc_lang=en)要求相关董事会会议至少提前7个完整营业日公告。可用来构建证据化日历，但必须解析董事会通知正文的会议日期和目的/改期，公告发布时间不是业绩披露日期。仅列表有数据不足以证明完整、准确的未来业绩日历。`hkexgroup.com`Financial Calendar仅HKEX自身发行人，不是所有港股。

### 后续实测更新：HK两项均有可实现的公开来源

以下结果补足上述第4节的接口未验证状态，核验仍为2026-10-12。

**HKEX全市场年度股息率榜**：已实际取到完整2808条、2808个唯一证券代码。官方[配置JS](https://www.hkex.com.hk/lhkexw/js/lib/ssdl/ssdlconfig.js)指定服务根`https://www1.hkex.com.hk/hkexwidget/data/`，[页面业务JS](https://www.hkex.com.hk/lhkexw/js/capitalefficiency.js)指定`getcapitalefficiencylist`及参数。请求步骤：

1. GET上述官方Dividend-Yield-Ratio页面，读取HTML内`LabCI.getToken = function () { ... return "..."; };`的公开访问值。函数同时有注释掉的sample return，必须先删除函数块的`//`行注释再提取实际return。只动态使用，不硬编码、不日志、不保存该值。字面`anonymoustoken`实测403。
2. GET `https://www1.hkex.com.hk/hkexwidget/data/getcapitalefficiencylist`，参数`token=<页面公开值>&type=DYR&sector=null&hsi=0&page=1&limit=3000&sort=1&dir=1&lang=eng&callback=probe&qid=1`；headers `Referer: https://www.hkex.com.hk/`、`User-Agent: Mozilla/5.0`。完整参数实测200，JSONP固定wrapper `probe(...)`，应解析JSON不可执行JS。第一次省略若干参数/UA的请求403，原因未单变量定位，因此保留官网调用形状。
3. 结果`data.responsecode=000`、`totalCount=2808`、`datalist`2808条、`year=[2025,2024,2023,2022]`。`limit=500`实测500条；`limit=100`时page1/2/28各100、page29为8。全量响应和这四个分页逐行相同、totalCount与year相同，未发现首次失败导致的快照不一致。
4. 行结构`{sym,nm,period,data}`，例如`169/WANDA HOTEL DEV/2025 Annual`，`data=[408.85,408.85,0.00,0.00,0.00]`（实际为字符串）。`data[0]`为latest annual，比率百分数，`data[1..4]`与year列表逐项对应。空字符串/`N/A`为未知，不能变成0。`period`是其数据所属年度，不能套用A股传入的季度report_date。可能有`mcounter`其他货币交易柜台字段，应明确按发行人/主交易代码口径，不重复排名。
5. 一请求足够当前规模，但运行时必须检查`len(datalist)==totalCount`和证券代码唯一，未来超过3000时分页补齐；失败不得返回部分结果并称完整。此榜是年度公开财报口径，并非当日TTM，不应与A股股息数据混写同一计算口径。

安全原始响应：`/tmp/stock-coverage-probes/hk-dividend-limit3000.json`及page1/2/28/29 JSON，均不含访问值。

**HK单证券财报日历**：已装yfinance1.3.0实际无需用户凭据取得0700.HK、0388.HK、0005.HK的未来及历史业绩日期。

- `Ticker.calendar['Earnings Date']`实测分别为2026-11-18、2026-11-06、2026-10-27。`Ticker.get_earnings_dates(limit=6)`同时返回未来行（Reported EPS空）和历史行（Reported EPS有值），底层当前走[源码中的Yahoo earnings-calendar HTML](https://github.com/ranaroussi/yfinance/blob/main/yfinance/base.py)，不是财报报表期末日期。
- Yahoo官方[0700.HK报价页](https://finance.yahoo.com/quote/0700.HK/)也明示Earnings Date=Nov18,2026。[HSBC自身日历](https://www.hsbc.com/investors/investing-in-hsbc/financial-calendar)证实0005的2026-10-27。HKEX自身[IR页面](https://www.hkexgroup.com/investor-relations?sc_lang=en)本次检索仍标Q3 TBC，Financial Calendar只November2026；Yahoo的0388精确11/6不能仅凭供应商升格为官方确认。
- `get_earnings_dates`实测返回America/New_York时间，必须先`tz_convert('Asia/Hong_Kong')`再取日期。例如0388历史`2026-02-25 23:00-05:00`实际香港日期2/26，与发行人官网一致。直接取datetime的date会早一天。
- `calendar`公开接口由源码`datetime.fromtimestamp(...).date()`产生本机日期，且丢弃供应商确认标志，单独依赖它有主机时区风险。内部原始`calendarEvents.earnings`有`earningsDate`Unix秒和`isEarningsDateEstimate`（三个样本均false），但内部方法不是稳定公开接口，且false仅是供应商判断。原始非敏感`calendarEvents`存于`/tmp/stock-coverage-probes/hk-calendar-{0700.HK,0388.HK,0005.HK}.json`。
- 可交付范围：HK单股provider_estimate/provider_scheduled日期、历史provider_reported日期，来源与确认级别显式标注，报告期未知时保持unknown，不能将requestedquarter作为实际report_date；没有日期则unknown，不能预测填充。未验证全港股或全市场按季度完整排期；实际公布日期若只以历史EPS事件判断仍属供应商记录，官方确认需要对应业绩公告证据。

### HKEX访问值编码故障复现与修复依据

主任务实现首次实时请求403后，追加只读差异验证：同一旧来源HTML的访问值，callback分别`probe/stockrank`均200；fresh HTML的访问值，两个callback均403；requests.Session保留来源页面cookies也403，因此不能通过换callback/Session修复。本次fresh来源函数返回值含URL百分号编码，直接交给`requests.get(params=...)`会把`%`再次编码为`%25`。官网SSDL源码将参数直接拼进querystring，未发生此二次编码。

已用当前fresh页面验证：`urllib.parse.unquote(token_match.group(1))`解码一次后，再交给requests params，`callback=stockrank,limit=3000`实际200且2808条完整响应。不需要旧访问值、硬编码或用户凭据。首次研究旧HTML值为未百分号编码形态，故未暴露此差异。实现应覆盖“官网值已percent-encoded”的用例，且任何失败信息不得带包含访问值的请求URL。此修复验证只在`/tmp/hk_token_decode.py`探针进行，本研究代理未改业务代码。

## 第三轮补足验证：无凭据长期PB与官方HK排期

### 000985长期PB：公开乐咕路径可访问但目标指数无数据

[乐咕000985.CSI页面](https://www.legulegu.com/stockdata/index-basic?indexCode=000985.CSI)当前实际200，标题“中证全指”、内容标“中证全指指数 · 000985.CSI”；`.SH`页面标题“中证全指(SH)”。这证明页面身份，并不能证明历史数据存在。[官网页面JS](https://www.legulegu.com/static/js/lg-charts-industry-chart.js)明确普通图表请求`/api/stockdata/index-basic?indexCode=...&freq=day&token=...`，主题图表省略freq；token是官网JS将当日`YYYY-MM-DD`做MD5产生的公开页面请求标识，另有正常CSRF/cookies链。探针只执行公开页面正常读取流程，未用登录凭据/伪造VIP或绕身份校验。

实际API：`https://www.legulegu.com/api/stockdata/index-basic`，参数`indexCode=000985.CSI`或`000985.SH`，`freq=day/month`或省略，带正常页面CSRF/cookies、Referer及当日公开请求标识，均200但`data=[]`、`vip=false/user=false`。同链正控`930743.CSI`实测61条（2021-10-29至2026-10-12），包含`pb/addPb/middlePb`，所以不能把目标空数据归因于全站网络失败；也不能将生科指数PB套给中证全指。公开overview索引没有找到000985链接。本次仅证明“这些公开请求没有目标数据”，不能推断VIP是否有数据。

原始目标空响应与正控放于`/tmp/stock-coverage-probes/legu-000985.CSI.json`、`legu-000985.SH.json`、`legu-930743.CSI-None.json`，不含请求访问值。无配置LIXINGER_TOKEN/TUSHARE_TOKEN时，中证全指多年PB仍应报告blocked/unknown；已验证CSI多年PE与仅20行PE/dividend文件不能填补PB。完整实时真实持仓也不等于PCF，任何新增来源必须独立核验口径。

### HK官方通用已公告排期：主板/GEM董事会通知汇总表已取到

无需ticker硬编码或用户凭据，公开GET：

- [Main Board董事会排期](https://www.hkex-is.hk/wwwroot/link/ebmn.htm)
- [GEM董事会排期](https://www.hkex-is.hk/wwwroot/link/ebmngem.htm)

HKEX[官方说明](https://www.hkex.com.hk/News/News-Release/2011/110902news?sc_lang=en)明确`www.hkex-is.hk`为额外公告板，官方[投资者FAQ](https://www.hkex.com.hk/-/media/HKEX-Market/Listing/Rules-and-Guidance/eSubmission-System/Listed-Company-Information-Dissemination-and-Related-Trading-Arrangements/faqinv.pdf)说明可在Exchange Reports/Board Meeting Notifications查看上市公司排期。这是官方汇总证据，并非Yahoo供应商预计。

当前两页实际200，更新时间`Date : 09/10/2026`。HTML最后一张表有6列：`BM Date / 空列 / Stock Short Name / Code / Purpose / Period`。`pd.read_html(StringIO(html),keep_default_na=False)[-1]`实测Main `(43,6)`、GEM `(3,6)`，前两行是表头和虚线，实际41/1记录；代码在本次解析为字符串`388/700/8307`，仍应按字符串去空格再zfill(5)，不接受`388.0`。

通用处理：按明确`%d/%m/%Y`解析BM Date；保留Purpose、Period原文，只有结果用途（如RESULTS、3RD QUARTER RES、FIN RES/DIV、INT RES/DIV）进入财报排期，纯`3RD INT DIV`不能当业绩事件，`QUARTERLY REVENUE`也不能凭名字升格为财报。Period含`9-MTH-ENDED30/09/26`或`Y.E.31/07/26`时可独立提取明确期末`%d/%m/%y`；只有`THIRD QUARTER 2026`则保留原文、不从用户requestedquarter伪造actual报告期。无Period保持unknown。

例证：表内已公布`00388 HKEX`会议2026-11-06、用途RESULTS、期末2026-09-30；`00700 TENCENT`会议2026-11-18、用途3RD QUARTER RES/DIV、期末2026-09-30；`08307 MEDICSKIN`会议2026-11-24、RESULTS/INT DIV、期末2026-09-30；此外含大量其他证券。前文HKEX发行人页面TBC是当时检索的旧页面状态，此更新汇总表提供了其精确日期官方排期证据。

输出应标`scheduled_board_meeting`与`confirmation=official_exchange_schedule`、`scheduled_date`及独立`report_date`；`actual_disclosure_date`仍unknown。这些表明确“may not be exhaustive”“以发行人公告为准”，多日会议只显示首日。因此可补足官方已公告排期的通用来源，但不承诺全部港股覆盖，不把不在列表当无排期，不把会议日期自动当实际披露日期，也不声称历史完整列表。实际公布日期仍需对应业绩公告。供应商回退若保留必须单独标provider_estimate，并不得覆盖本官方证据。

原始完整HTML：`/tmp/stock-coverage-probes/hk-board-main.html`、`hk-board-gem.html`。原始报告含人民币柜台（如80388/80700），它们是明确表中证券代码，不能无证据与主柜台混成两个独立发行人。

## 后续实测：定期披露股票表

2026-10-12 实测东财 FundArchivesDatas.aspx type=jjcc，只有显式 year/month 才取得目标报告较完整股票表，year留空即使topline10000仍只有最新季度10条。year=2026,month=6,topline=10000实测510300为339条、159915为121条、513100为102条，报告日均2026-06-30。源列持股数为万股、市值为万元（跨境列万元人民币），规范化分别乘10000；原始证券代码按字符串保留。未以股票表推断完整全资产，未用PCF证券数量或指数名单替代实际组合，未声称实时持仓。源：[基金股票持仓](https://fundf10.eastmoney.com/ccmx_510300.html)、[AKShare持仓实现](https://github.com/akfamily/akshare/blob/main/akshare/fund/fund_portfolio_em.py)。

PR #47 b9f30b8 的CI已全部通过：Python3.10/3.11、三系统compat、四真实宿主、网络和LLM e2e均成功。后续新增实现仍需在新提交上重跑，不能沿用旧提交CI结果。
