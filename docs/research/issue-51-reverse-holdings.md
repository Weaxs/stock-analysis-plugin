# Issue #51：SEC 个股反查机构持仓

核验日期：2026-10-13。以 SEC 官方页面、ZIP 表头及原始 XML 为依据；不使用名称模糊匹配。

## 数据路径

SEC [13F 数据目录](https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets)截至本次最新发布窗口为 2026 年 6—8 月（约 96 MB），不是 `2026q3` 日历季度包。2024 年起窗口为 12—2、3—5、6—8、9—11 月，需读取目录真实链接，不拼接季度文件名。

- 最新：[01jun2026-31aug2026_form13f.zip](https://www.sec.gov/files/datastandardsinnovation/data/form-13f-data-sets/01jun2026-31aug2026_form13f.zip)。
- 上一期：[01mar2026-31may2026_form13f.zip](https://www.sec.gov/files/structureddata/data/form-13f-data-sets/01mar2026-31may2026_form13f.zip)。

窗口指申报日期，持仓日期另见 `PERIODOFREPORT`。目录说明尾日截止后提交会进入下一包。最新包也含 2001 年等旧期补报，不能用文件首行、文件名或全部行的最大值推断通常最新持仓期；应从正常原始报告识别最新已发布报告期，再按该日期精确筛选。[官方字段文档](https://www.sec.gov/files/form_13f.pdf)

## 精确映射与实际字段

[AAPL 官方 submissions](https://data.sec.gov/submissions/CIK0000320193.json)最新结构化 Schedule 13G 是 `0002100119-26-000139`（2026-04-29）。[原始 XML](https://www.sec.gov/Archives/edgar/data/320193/000210011926000139/primary_doc.xml)确认：`formData/coverPageHeader/issuerInfo/issuerCik=0000320193`，`issuerInfo/issuerCusips/issuerCusipNumber=037833100`，`securitiesClassTitle=Common Stock`。`primaryDocument` 的 `xslSCHEDULE_13G_X02/` 路径是展示层，解析时取原始 XML。13D 的字段可能为 `issuerCIK`；兼容标签大小写，但必须校验发行人 CIK 精确相等。当前入口无结构化映射时报缺失，不能猜。[EDGAR Filer Manual](https://www.sec.gov/file/efmvol2-c8-0)

实测 ZIP：UTF-8、制表符、表头行。`ACCESSION_NUMBER` 连接以下三张表；不要用 accession 前十位代替申报机构 CIK。

- `SUBMISSION.tsv`：`FILING_DATE`、`SUBMISSIONTYPE`、`CIK`、`PERIODOFREPORT`；日期为 `DD-MON-YYYY`。
- `COVERPAGE.tsv`：`REPORTCALENDARORQUARTER`、`ISAMENDMENT`、`AMENDMENTNO`、`AMENDMENTTYPE`、`FILINGMANAGER_NAME`。
- `INFOTABLE.tsv`：`INFOTABLE_SK`、`NAMEOFISSUER`、`TITLEOFCLASS`、`CUSIP`、`FIGI`、`VALUE`、`SSHPRNAMT`、`SSHPRNAMTTYPE`、`PUTCALL`、`INVESTMENTDISCRETION`、`OTHERMANAGER`、`VOTING_AUTH_SOLE/SHARED/NONE`。

完整 ZIP 实测 100,719,015 字节。`SUBMISSION/COVERPAGE` 各 11,852 行；`13F-HR` 9,501 份，其中报告日期 `30-JUN-2026` 8,857 份；`13F-HR/A` 399 份。申报日期实际覆盖 2026-06-01 至 2026-08-31。修订字段标签为 `RESTATEMENT`（262 份）、`NEW HOLDINGS`（144 份，含 NT 修订）。`INFOTABLE.tsv` 3,830,274 行，压缩 99,340,913 字节，解压约 396.6 MB，应流式筛选 CUSIP，不整表创建 DataFrame。保留证券类别、SH/PRN、PUT/CALL 区别，不能把期权和本金混进普通股股数。旧 PDF 的 `VALUE x$1000` 已过时；2023-01-03 起按美元整数申报，现代包不要再乘 1000。[SEC FAQ 36、61—62](https://www.sec.gov/rules-regulations/staff-guidance/frequently-asked-questions-about-form-13f)

## 修订与覆盖边界

按申报机构 `CIK` 和报告日期重建申报链。`RESTATEMENT` 替换此前整张表；`NEW HOLDINGS` 补充此前表。不能把原始版和所有修订直接求和，也不能取最新修订后丢掉原始版。遇到无法确认含义或缺少基础版本的修订，须标记覆盖不完整。[SEC FAQ 58b—58d](https://www.sec.gov/rules-regulations/staff-guidance/frequently-asked-questions-about-form-13f)

结果须包含报告日期、数据发布日期/窗口、申报日期和原文 URL；完整性仅指已下载 SEC 公开结构化报告窗口。迟报、后续修订、保密持仓、小额豁免及不受 13F 义务约束的机构不在这个保证之内；不能宣称实时全部股东。机构共享投资决策权也可能跨报告重复，不能把跨机构合计当去重的全市场持股比例。[SEC FAQ 2、35、39、58d](https://www.sec.gov/rules-regulations/staff-guidance/frequently-asked-questions-about-form-13f)
