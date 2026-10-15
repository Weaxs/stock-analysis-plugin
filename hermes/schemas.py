TOOL_SCHEMAS = [
    {
        "name": "get_announcements",
        "description": "查询A股巨潮公告标题、日期、类型和原文PDF链接；按日期范围/类别筛选，默认近90天，top=1..500。第一版不提取正文，truncated提示截断。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "A股代码"},
                "category": {
                    "type": "string",
                    "enum": [
                        "all",
                        "forecast",
                        "express",
                        "annual",
                        "interim",
                        "quarterly",
                        "reduction",
                        "issuance",
                        "inquiry",
                        "dividend",
                    ],
                    "description": "公告类别，默认all",
                },
                "start_date": {"type": "string", "description": "开始日期YYYY-MM-DD，默认结束日期前90天"},
                "end_date": {"type": "string", "description": "结束日期YYYY-MM-DD，默认今天"},
                "top": {"type": "number", "description": "最多条数1..500，默认50"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_us_holdings",
        "description": "查询SEC 13F：mode=auto默认有13F申报时查机构，否则按发行人CIK反查持仓机构；manager/issuer可显式指定。发行人通过官方13D/G精确映射CUSIP，扫描最新已发布13F窗口，处理重述/补充修订，返回实际报告期、截止日及映射覆盖。首次约100MB下载，后续缓存；无映射明确报错，不做名称猜测。需SEC_USER_AGENT含真实联系邮箱，不是实时或全部证券持仓。",
        "parameters": {
            "type": "object",
            "properties": {
                "cik": {"type": "string", "description": "机构或发行人CIK，1..10位数字，如0001067983/0000320193"},
                "mode": {
                    "type": "string",
                    "enum": ["auto", "manager", "issuer"],
                    "description": "默认auto；manager查机构组合，issuer反查发行人持仓机构",
                },
            },
            "required": ["cik"],
        },
    },
    {
        "name": "get_insider_trades",
        "description": "查询美股发行人近期Form "
        "4/4-A内部人交易（SEC官方XML），保留人员职务、交易代码、买卖方向、数量、价格、脚注和衍生品标志。需SEC_USER_AGENT含真实联系邮箱；days按申报日期筛选，top限制文件数，修订勿重复计数。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "美股ticker或发行人CIK"},
                "days": {"type": "number", "description": "申报日期窗口1..365天，默认90"},
                "top": {"type": "number", "description": "最多解析Form 4文件数1..20，默认10"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_valuation_history",
        "description": "查询A/HK股票历史PE-TTM/PB、当前正值分位和10/25/50/75/90分位Band，返回实际采样区间；亏损估值分位为null。years=1..10，按天缓存。HK配置LIXINGER_TOKEN时优先理杏仁非金融公司日历史，失败降级百度，不混接来源。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "A股代码或港股.HK代码"},
                "years": {"type": "number", "description": "历史窗口年数1..10，默认5"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_index_valuation",
        "description": "查询中国指数估值历史分位与Band；乐咕乐股PE/PB优先，中证官方历史补充滚动PE并保留近期PE1口径。缺PB时可用LIXINGER_TOKEN补充理杏仁mcw历史，来源与算法分别标明，不混接同一指标。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "指数代码或名称，如000300、沪深300、000985"},
                "years": {"type": "number", "description": "历史窗口年数1..10，默认5"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_earnings_calendar",
        "description": "查询财报日历：A股report_date必填季度期末，返回预约/修订/实际披露与业绩预告；HK需symbol，优先HKEX已公告业绩审批董事会排期（不是实际披露日），缺失时返回供应商日期。HK可按已知财政期末过滤，未知期次另列。",
        "parameters": {
            "type": "object",
            "properties": {
                "report_date": {
                    "type": "string",
                    "description": "A股必填季度期末YYYY-MM-DD；HK可选已公告财政期末，未知期次另列",
                },
                "symbol": {"type": "string", "description": "A股代码可选（省略查全市场）；HK必须提供港股.HK代码"},
                "top": {"type": "number", "description": "每类最多返回条数1..500，默认50"},
            },
            "required": [],
        },
    },
    {
        "name": "get_shareholder_structure",
        "description": "查询A股股东户数、十大流通股东变化及沪深交易所季度北向合计持股；官方总量优先，十大HKSCC名义持股仅兜底且标明口径。HK返回港交所南向记录。缺失不当零，期末不等于已公布。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "A股代码或港股.HK代码"},
                "report_date": {
                    "type": "string",
                    "description": "A股报告期YYYY-MM-DD；省略时北向使用最新已公布季度，十大股东使用最新财报期；HK忽略",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_etf_details",
        "description": "查询A股ETF净值折溢价、规模、披露持仓及沪深完整PCF申赎篮子；篮子不是实际持仓。include_full_holdings=true另取最近可得半年度/年度披露股票表，含数量/市值/报告期，非实时全资产组合；benchmark用于跟踪误差。",
        "parameters": {
            "type": "object",
            "properties": {
                "include_full_holdings": {
                    "type": "boolean",
                    "description": "显式请求半年度/年度全部披露股票表，默认false；不是实时全资产组合",
                },
                "symbol": {"type": "string", "description": "A股ETF代码，如510300"},
                "benchmark": {"type": "string", "description": "可选基准代码，如sh000300；不猜测基准"},
                "days": {"type": "number", "description": "净值窗口交易日数10..252，默认60"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_dividends",
        "description": "查询A/HK分红历史、除息日历、已实施TTM股息与连续年数。mode=ranking省略symbol，market=A查报告期排行，market=HK查HKEX/LSEG年度股息率排行（report_date须年末）；排行均非当前TTM收益率。缺失不按零。",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["A", "HK"],
                    "description": "仅ranking选择市场，默认A；历史/除息日历由symbol决定",
                },
                "symbol": {"type": "string", "description": "history/calendar必填A股或港股.HK代码；ranking省略"},
                "mode": {
                    "type": "string",
                    "description": "history默认；calendar未来已公布除息事件；ranking按market查询A/HK排行",
                    "enum": ["history", "calendar", "ranking"],
                },
                "report_date": {
                    "type": "string",
                    "description": "ranking报告期YYYY-MM-DD，默认上一年年末；HK仅年度年末日期",
                },
                "years": {"type": "number", "description": "历史窗口年数1..20，默认10"},
                "top": {"type": "number", "description": "排名条数1..500，默认20"},
            },
            "required": [],
        },
    },
    {
        "name": "get_kline",
        "description": "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）、日股（如7203.T）、韩股（如005930.KS）、台股（如2330.TW）及A股ETF。A股股票/ETF返回前复权价格，每条记录 adjusted=qfq；sh000xxx/sz399xxx 指数返回原始点位，adjusted=none。需要历史价格自行计算或画图时用；只问指标用 get_technical_analysis，只问现价用 get_quote。每条记录附 source、UTC fetched_at、data_as_of 和 adjusted；非A股复权口径可能为total_return/qfq/unknown，未知不能假定已复权。部分来源附 adjustment_factor（复权价格/原始价格）；缺失不能猜测换算系数。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码，如 600519（A股）、00700.HK（港股）、AAPL（美股）、7203.T（日股）、005930.KS（韩股）、2330.TW（台股）",
                },
                "period": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "K线周期，默认 daily",
                },
                "count": {
                    "type": "number",
                    "description": "返回数据条数，默认 60",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_quote",
        "description": "获取股票实时行情报价（现价、涨跌幅、量比等）。支持A股、港股、美股、日股、韩股、台股；非交易时段返回最近交易日收盘价并以 as_of/stale 标注；ETF 的 premium_discount_rate 为正=溢价、负=折价。返回 source、UTC fetched_at 和提供方 data_as_of（未知为null）；as_of_basis区分提供方时间与交易日历推断，采集时间不等于行情时间。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_capital_flow",
        "description": "获取A股资金流向（主力/超大单/大单/中单/小单净流入）。detail=个股每日明细，summary=多日汇总+趋势，sector_flow=板块资金流排行。可配合 get_chip_distribution 验证主力行为",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码（detail/summary模式必填），如 600519",
                },
                "mode": {
                    "type": "string",
                    "enum": ["detail", "summary", "sector_flow"],
                    "description": "模式：detail=每日明细（默认），summary=多日汇总，sector_flow=板块排行",
                },
            },
        },
    },
    {
        "name": "get_news",
        "description": "获取个股最近N天财经新闻快讯（轻量、无需配置）。需要深度全网情报用 search_comprehensive_intel，按主题搜索用 search_stock_news，读文章全文用 extract_article",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "days": {
                    "type": "number",
                    "description": "获取最近几天的新闻，默认 3",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_financials",
        "description": "获取股票财务摘要及历史趋势，按天缓存。A股annual=true与periods=10取十年年报；港股可配置LIXINGER_TOKEN取非金融公司财务序列，保留币种/披露日/实际财年，历史比率单位percent。有效Yahoo顶层快照按整组保留，currency为财报币种、market_cap_currency为市值币种、snapshot_source标明来源；缺失值及report_date不混入历史数值/日期。缺token或无权限降级原有来源并标注不足，不补造缺失。估值历史用get_valuation_history，披露排期用get_earnings_calendar",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "annual": {"type": "boolean", "description": "只取年度报告，默认false"},
                "periods": {
                    "type": "number",
                    "description": "返回最近N个报告期1..40，默认1；annual=true时periods=10请求10年年报",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_technical_analysis",
        "description": "获取股票技术面分析（MA/MACD/RSI/BOLL/KDJ/成交量等指标 + 100分综合评分 + 6级买卖信号 + 趋势/偏离度/支撑压力位；多周期共振时 resonance.direction ∈ aligned_bullish/aligned_bearish/divergent）。个股技术面综合判断与买卖时机分析的首选；只要均线数值或自定义周期用 calculate_ma，专问量价用 get_volume_analysis，扫当日异动用 detect_anomaly",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "period": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "分析周期，默认 daily",
                },
                "count": {
                    "type": "number",
                    "description": "用于计算指标的K线条数，默认 120",
                },
                "periods": {
                    "type": "string",
                    "description": '多周期共振分析，逗号分隔的周期列表，如 "daily,weekly"；不传则只分析 period 指定的单周期',
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "analyze_pattern",
        "description": "K线形态识别 — 检测十字星、锤子线、吞没、启明星、黄昏星、双底、20日突破等12+种经典形态。综合技术面判断用 get_technical_analysis",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "period": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "K线周期，默认 daily",
                },
                "days": {
                    "type": "number",
                    "description": "分析的K线天数，默认 60",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_market_indices",
        "description": "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500；JP: 日经225/东证；KR: KOSPI/KOSDAQ；TW: 台湾加权。大盘复盘用 get_market_review 一站式获取",
        "parameters": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "enum": ["cn", "hk", "us", "jp", "kr", "tw"],
                    "description": "市场区域，默认 cn",
                },
            },
        },
    },
    {
        "name": "get_sector_rankings",
        "description": "获取A股板块涨跌幅排行（含领涨股、涨跌家数等）。board_type=industry（默认）为行业板块，board_type=concept 时为概念板块排行。支持查看涨幅榜/跌幅榜/双向",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {
                    "type": "number",
                    "description": "返回排名前N的板块，默认 10",
                },
                "direction": {
                    "type": "string",
                    "enum": ["top", "bottom", "both"],
                    "description": "top=涨幅榜（默认），bottom=跌幅榜，both=双向",
                },
                "board_type": {
                    "type": "string",
                    "enum": ["industry", "concept"],
                    "description": "板块类型：industry=行业（默认），concept=概念",
                },
            },
        },
    },
    {
        "name": "get_sector_constituents",
        "description": "查询A股板块成分股（正向映射：板块→股票列表）。板块名支持模糊匹配（如「创新药」自动解析到精确板块名），东财行业/概念 + 新浪多源 failover，结果按用户目录缓存7天。仅支持A股；反向查个股所属板块用 resolve_stock_sectors",
        "parameters": {
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": "板块名称（可模糊，如 创新药、半导体）",
                },
                "board_type": {
                    "type": "string",
                    "enum": ["industry", "concept", "auto"],
                    "description": "板块类型：industry=行业，concept=概念，auto=自动（默认）",
                },
            },
            "required": ["sector"],
        },
    },
    {
        "name": "resolve_stock_sectors",
        "description": "查询个股所属板块（反向映射：个股→板块）。A股返回东财行业 + efinance 概念/板块（雪球行业兜底，需 XUEQIU_TOKEN），港股返回 yfinance GICS 英文 sector/industry 口径。板块级情报（如「创新药回调」）映射到池内个股时调本工具；正向查板块成分股用 get_sector_constituents",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如 600519，港股如 00700.HK）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_stock_info",
        "description": "获取股票基本信息（行业、板块、上市日期、总股本等）。A股返回板块/行业，其他市场返回行业/公司简介",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_chip_distribution",
        "description": "获取A股筹码分布数据（获利比例、平均成本、90%/70%成本集中度）。仅支持A股。与 get_capital_flow 配合验证主力行为",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码，如 600519",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_market_stats",
        "description": "获取A股市场整体统计（涨跌家数、涨停跌停数、平均涨幅、涨跌Top5、总成交额）。用于衡量市场整体情绪与温度",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_limit_up_pool",
        "description": "获取A股涨停池/涨停板复盘——当日涨停个股，含连板数、封板资金、炸板次数、首末次封板时间、所属行业。短线情绪与龙头战法核心数据",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期（YYYYMMDD），默认当日；非交易日或未来日期自动回退到最近交易日（返回含 requested_date 与 stale 标注）",
                },
            },
        },
    },
    {
        "name": "get_dragon_tiger",
        "description": "获取A股龙虎榜——上榜个股净买额/买入额/卖出额/上榜原因/解读，可按个股过滤",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期（YYYY-MM-DD），默认当日；非交易日或未来日期自动回退到最近交易日（返回含 requested_date 与 stale 标注）",
                },
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码（可选，按个股过滤），如 600519",
                },
                "top": {
                    "type": "number",
                    "description": "返回前N条，默认 20",
                },
            },
        },
    },
    {
        "name": "get_hot_stocks",
        "description": "获取A股全市场人气热搜榜（东方财富人气榜）",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {
                    "type": "number",
                    "description": "返回前N只，默认 20",
                },
            },
        },
    },
    {
        "name": "get_margin_trading",
        "description": "获取A股个股融资融券明细（融资余额/融资买入额/融券余量等，上交所/深交所官方数据按交易所分流），返回最近N个交易日序列（最新在前）。金额单位为元，short_*_shares 单位为股。include_ratio=true追加同日融资买入/成交额百分比（margin_buy_ratio_pct），缺少同日元口径成交额则为null。仅两融标的有数据，非两融标的返回错误",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码，如 600519",
                },
                "include_ratio": {"type": "boolean", "description": "追加同日融资买入占比，默认false"},
                "days": {
                    "type": "number",
                    "description": "返回最近N个交易日，默认 10",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_northbound_flow",
        "description": "获取北向资金市场级净买入序列（最新在前，单位亿元）。数据口径：东方财富沪深港通历史数据；2024-08-16 起交易所停止披露日度净买额，仅 2024-08 之前历史数据可查（近期小 days 窗口会返回停披错误，加大 days 可取历史）",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "number",
                    "description": "返回最近N个交易日，默认 10",
                },
            },
        },
    },
    {
        "name": "get_fundamental_context",
        "description": "获取A股基本面上下文（估值 PE/PB/市值 + 营收/净利增速 + ROE/毛利率/净利率 + 分红历史）。用于评估公司质地与长期持有价值",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码，如 600519",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "screen_stocks",
        "description": "全市场股票筛选（AlphaSift L1 多因子硬筛 + 市场情绪调节）。按PE/PB/市值/换手率/涨跌幅/量比等因子过滤和评分，可用 l2 开启质量/成长/动量/波动率/资金流增强。显式筛选条件缺少数据时报error，filters_applied只列实际执行的条件。候选factor_coverage列出可用/缺失因子、加权覆盖率和effective_weights；L1与L2均按可用因子归一化后排序，缺失/非有限值不按零分计；L2另返回effective_scores供结合市场系数复算final，按可用因子重归一化，高分不代表数据完整。选股预算按候选数量及采集批次计算，top须为正整数。",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["A", "HK", "US"],
                    "description": "市场，默认 A",
                },
                "top": {
                    "type": "number",
                    "description": "返回排名前N的股票，默认 20",
                },
                "config": {
                    "type": "string",
                    "description": "自定义筛选配置YAML文件路径，不填用默认配置",
                },
                "l2": {
                    "type": "boolean",
                    "description": "开启L2量化增强（质量/成长/真实动量/波动率/资金流因子，逐股抓取数据较慢），默认关闭",
                },
            },
        },
    },
    {
        "name": "run_backtest",
        "description": "策略回测（AlphaEvo）。读取YAML策略定义，在历史数据上模拟交易，输出收益率/回撤/胜率等指标。只想知道单个技术信号的历史胜率用更轻量的 evaluate_signal。period为实际数据期间，requested_period保留请求期间，warnings提示覆盖差异；年化按实际期间计算。默认前日收盘信号、次日开盘成交；YAML simulation.execution=same_close可复现旧假设。execution返回模型、阻止成交次数和未模拟约束；零成交量及带历史限价的一字板不成交。",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "description": "策略YAML文件路径",
                },
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "start": {
                    "type": "string",
                    "description": "回测起始日期，格式 YYYY-MM-DD",
                },
                "end": {
                    "type": "string",
                    "description": "回测结束日期，格式 YYYY-MM-DD",
                },
                "capital": {
                    "type": "number",
                    "description": "初始资金，默认 1000000",
                },
            },
            "required": ["strategy", "symbol"],
        },
    },
    {
        "name": "evaluate_signal",
        "description": "技术信号历史准确率评估 — 回溯历史数据，统计某个技术信号触发后N日的胜率和平均收益。支持9种信号：macd_golden_cross/macd_death_cross/rsi_oversold/rsi_overbought/breakout_20d/breakdown_20d/volume_surge/ma_golden_cross/ma_death_cross。要完整模拟交易过程用 run_backtest",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "signal": {
                    "type": "string",
                    "enum": [
                        "macd_golden_cross",
                        "macd_death_cross",
                        "rsi_oversold",
                        "rsi_overbought",
                        "breakout_20d",
                        "breakdown_20d",
                        "volume_surge",
                        "ma_golden_cross",
                        "ma_death_cross",
                    ],
                    "description": "信号名称",
                },
                "forward_days": {
                    "type": "string",
                    "description": "逗号分隔的前瞻天数，默认 3,5,10",
                },
                "lookback": {
                    "type": "number",
                    "description": "回溯K线条数，默认 250（约1年）",
                },
            },
            "required": ["symbol", "signal"],
        },
    },
    {
        "name": "resolve_stock_name",
        "description": "股票名称智能解析 — 输入中文名（贵州茅台）、拼音（guizhou maotai/gzmt）、部分代码，返回匹配的股票代码。仅支持A股。用户给出中文名/拼音/部分代码时先调本工具换成代码再调其他工具",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "股票名称、拼音、拼音首字母或部分代码",
                },
                "top": {
                    "type": "number",
                    "description": "返回匹配数量，默认 5",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_trading_day",
        "description": "查询某日是否为交易日。支持CN（A股）、HK（港股）、US（美股）、JP（日股）、KR（韩股）、TW（台股）",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["CN", "HK", "US", "JP", "KR", "TW"],
                    "description": "市场",
                },
                "date": {
                    "type": "string",
                    "description": "日期（YYYY-MM-DD），不填则查今天",
                },
            },
            "required": ["market"],
        },
    },
    {
        "name": "get_trading_days",
        "description": "获取最近/未来N个交易日列表。支持CN/HK/US/JP/KR/TW",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["CN", "HK", "US", "JP", "KR", "TW"],
                    "description": "市场",
                },
                "direction": {
                    "type": "string",
                    "enum": ["next", "prev"],
                    "description": "next=未来交易日, prev=过去交易日",
                },
                "count": {
                    "type": "number",
                    "description": "返回天数，默认 5",
                },
                "date": {
                    "type": "string",
                    "description": "起始日期（YYYY-MM-DD），默认今天",
                },
            },
            "required": ["market"],
        },
    },
    {
        "name": "get_trading_phase",
        "description": "查询某市场当前交易时段（盘前/早盘/午间休市/午盘/交易中/盘后/休市），用于正确解读实时行情的盘中语义。支持CN/HK/US/JP/KR/TW",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["CN", "HK", "US", "JP", "KR", "TW"],
                    "description": "市场",
                },
            },
            "required": ["market"],
        },
    },
    {
        "name": "calculate_ma",
        "description": "独立均线计算器 — 支持任意周期MA（5/10/20/30/60/120/250或自定义）+ 偏离度 + 均线排列 + 金叉死叉检测。只要均线数值或需要30/120/250等非默认周期时用本工具；综合技术面分析用 get_technical_analysis",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "periods": {
                    "type": "string",
                    "description": "逗号分隔的MA周期列表，如 5,10,20,60,120,250",
                },
                "kline_period": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "K线周期，默认 daily",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_volume_analysis",
        "description": "独立量价分析 — 量价相关性、上涨/下跌日成交量对比、量能趋势、量价模式解读（放量上涨/缩量回调等）。综合技术面判断用 get_technical_analysis，扫当日异动用 detect_anomaly",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "period": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "K线周期，默认 daily",
                },
                "count": {
                    "type": "number",
                    "description": "分析K线条数，默认 60",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "search_stock_news",
        "description": "多引擎股票新闻搜索（Tavily/Brave/SerpAPI/Bocha/SearXNG）。需配置至少一个搜索源的 Key 或 SearXNG URL。get_news 快讯不够或要按主题搜索时用；深度6维情报用 search_comprehensive_intel",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "count": {
                    "type": "number",
                    "description": "返回结果数量，默认 10",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_comprehensive_intel",
        "description": "股票综合情报搜索 — 从6个维度（新闻/公告/行情分析/风险/业绩/行业）搜索综合信息。用于个股的深入研究/全面调研；快速看新闻用 get_news",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
                "name": {
                    "type": "string",
                    "description": "股票名称（可选，提升搜索准确度）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_social_sentiment",
        "description": "获取股票社交媒体情绪数据。A股：东方财富股吧热度+雪球讨论热度（无需配置）；美股/港股：Reddit/X/Polymarket情绪（需 SENTIMENT_API_KEY）。自动按市场选数据源。面向个股维度；全市场热门讨论用 get_trending_sentiment",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_trending_sentiment",
        "description": "获取社交媒体热门趋势（Reddit/X/Polymarket热门股票讨论）。适用于发现市场热点；查个股情绪用 get_social_sentiment",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "extract_article",
        "description": "网页文章全文提取 — 输入URL，提取文章标题、正文（最多3000字）、作者、发布日期等。配合 get_news/search_stock_news 的搜索结果做深度阅读",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "文章URL",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "screen_risk",
        "description": "A股风险专项筛查 — 检查估值、技术预警、解禁、减持、业绩预警、监管和行业政策，返回风险评级和 veto_buy。新闻搜索命中是待核实线索，不是已证实风险。data_complete和warnings标明覆盖；数据不足时risk_level=unknown，risk_score或veto_buy可为null，不能视为低风险",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码，如 600519",
                },
                "name": {
                    "type": "string",
                    "description": "股票名称（可选，提升新闻搜索准确度）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "detect_market_regime",
        "description": "市场状态检测 — 分析大盘指数判断当前市场阶段（上涨趋势/下跌趋势/横盘震荡/高波动/板块热点），并推荐适合的分析策略",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["A", "HK", "US"],
                    "description": "市场代码，默认 A",
                },
            },
        },
    },
    {
        "name": "get_market_review",
        "description": "大盘复盘 — 获取市场日度复盘数据，包含指数、涨跌统计、板块排名、新闻、市场温度与策略建议。复盘类需求的首选，无需再分别调指数/统计/板块工具。数据不足时温度score及strategy_stance为null，信号unknown；warnings/data_quality说明限制",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["A", "HK", "US", "all"],
                    "description": "市场代码，默认 A。all 表示所有市场",
                },
            },
        },
    },
    {
        "name": "run_watchlist_analysis",
        "description": "批量自选股分析 — 对多只股票并行采集行情/技术/资金/风险等数据，返回汇总结果。适用于每日定时分析自选股列表",
        "parameters": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "string",
                    "description": "逗号分隔的股票代码列表，如 600519,000001,300750",
                },
                "workers": {
                    "type": "number",
                    "description": "并发数，默认 3（建议不超过5，避免API限流）",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "detect_anomaly",
        "description": "异常/事件检测 — 一键扫描股票当前所有异动信号（MACD金叉死叉、RSI超买超卖、20日突破、放量异动、涨跌停、布林突破、KDJ极值、资金异动等），返回结构化异常列表。当日异动归因的首选；综合技术面判断用 get_technical_analysis",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "diagnose_data_sources",
        "description": "数据源诊断 — 检查当前环境各数据 provider 的包与凭据就绪情况（不探测网络可达性），输出每个市场的可用链路、缺失 env、warnings。工具拿不到数据或报错时调用排查",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["A", "HK", "US", "JP", "KR", "TW", "all"],
                    "description": "市场，默认 all",
                },
            },
        },
    },
    {
        "name": "get_market_capabilities",
        "description": "市场能力边界 — 返回指定市场支持/不支持的工具列表。不确定某市场能否用某工具（如港股的筹码分布、美股的资金流）时先调本工具，避免调用不支持的工具后编造数据",
        "parameters": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "enum": ["A", "HK", "US", "JP", "KR", "TW"], "description": "市场代码"},
                "symbol": {"type": "string", "description": "股票代码（自动识别市场，与 market 二选一）"},
            },
        },
    },
    {
        "name": "render_stock_report",
        "description": "股票分析报告渲染 — 将结构化报告 JSON 通过 j2 模板渲染为 Markdown。report 字段以 skill 提供的 report_schema 为准，不要自造字段。template: brief|full。全部分析完成后的最后一步调用。仅渲染，不保存不推送。report可包含evidence（path/label/value/source/as_of）与data_quality（flags/limitations），brief/full均展示证据和限制。",
        "parameters": {
            "type": "object",
            "properties": {
                "report": {"type": "object", "description": "结构化股票报告 JSON"},
                "template": {"type": "string", "enum": ["brief", "full"], "description": "模板类型，默认 full"},
            },
            "required": ["report"],
        },
    },
    {
        "name": "render_market_report",
        "description": "大盘复盘报告渲染 — 将结构化报告 JSON 通过 j2 模板渲染为 Markdown。report 字段以 schemas/market_review_schema.json 为准。save=true 时把报告对象追加归档到本地 JSONL（默认不保存不推送），纵向对比用 get_review_history",
        "parameters": {
            "type": "object",
            "properties": {
                "report": {"type": "object", "description": "结构化市场复盘"},
                "save": {"type": "boolean", "description": "是否归档到本地复盘 JSONL 存储，默认 false"},
            },
            "required": ["report"],
        },
    },
    {
        "name": "get_review_history",
        "description": "复盘归档历史 — 读取本地 JSONL 归档的最近 N 条市场复盘（最新在前），用于纵向对比（温度/姿态/主线变化）。归档由 render_market_report 的 save=true 写入，空存档返回空列表",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "number", "description": "返回条数，默认 10"},
            },
        },
    },
    {
        "name": "build_watchlist_context",
        "description": "自选股上下文包 — 对多只股票输出评分/趋势/异常/风险/建议 next_tools 的 agent 友好摘要。宿主 agent 决定如何写日报或深入分析。异动检查失败时anomaly_status=unavailable、anomaly_count=null，保留anomaly_error/warnings；汇总计入partial",
        "parameters": {
            "type": "object",
            "properties": {
                "symbols": {"type": "string", "description": "逗号分隔的股票代码列表"},
                "include_market_review": {"type": "boolean", "description": "是否附带各市场复盘，默认 false"},
                "workers": {"type": "number", "description": "并发数，默认 3"},
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "analyze_position_context",
        "description": "持仓上下文分析 — 输入成本/仓位/止损止盈，结合现价和技术位输出浮盈亏、离止损距离、风险级别、操作建议。无状态、不存账户",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）"},
                "cost": {"type": "number", "description": "成本价"},
                "quantity": {"type": "number", "description": "持仓数量"},
                "stop_loss": {"type": "number", "description": "止损价"},
                "take_profit": {"type": "number", "description": "止盈价"},
            },
            "required": ["symbol", "cost", "quantity"],
        },
    },
    {
        "name": "check_alert_rules",
        "description": "无状态告警规则检查 — 传入规则数组，返回当前是否触发。规则类型：price_below/price_above/change_pct_above/change_pct_below/volume_ratio_above/anomaly/risk_veto/risk_level_at_least。不做调度不存历史。依赖数据不可用时evaluated条目含error，meta.warnings提示缺失，triggered=false不代表检查成功",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）"},
                "rules": {
                    "type": "array",
                    "description": "规则列表，每项 { type, value }",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "value": {},
                        },
                    },
                },
            },
            "required": ["symbol", "rules"],
        },
    },
    {
        "name": "record_signal",
        "description": "记录一条 AI 分析建议信号（方向/入场价/目标价/止损价/持有期限），供后续胜率评估。信号持久化在本地 JSONL（$STOCK_SIGNAL_STORE 或 ~/.stock-analysis/signals.jsonl），宿主 Agent 自行决定何时 record/evaluate",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代码（A股如600519，美股如AAPL，港股如00700.HK）"},
                "direction": {
                    "type": "string",
                    "enum": ["buy", "sell"],
                    "description": "建议方向",
                },
                "entry_price": {"type": "number", "description": "入场价（可选）"},
                "target_price": {"type": "number", "description": "目标价（可选）"},
                "stop_price": {"type": "number", "description": "止损价（可选）"},
                "horizon_days": {"type": "number", "description": "持有期限（交易日），默认 10"},
                "source": {"type": "string", "description": "信号来源（如触发该建议的 skill 名）"},
                "note": {"type": "string", "description": "备注（可选）"},
            },
            "required": ["symbol", "direction"],
        },
    },
    {
        "name": "evaluate_signals",
        "description": "评估到期信号：拉取记录日之后的日K，判定 target_hit/stop_hit/timeout 并计算收益，回写存储。记录建议用 record_signal，胜率复盘用 get_signal_summary。历史K线未覆盖记录日时skipped_no_data并保持未结算，不能用较新行情代替。原始信号价与复权K线须有可核实的adjustment_factor，缺失则skipped_price_basis保持未结算。CLI预算300秒，截止前返回并保存已完成结果；complete/deferred/warnings说明未处理记录，下次可重试",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代码（可选，只评估该股的信号）"},
            },
        },
    },
    {
        "name": "get_signal_summary",
        "description": "信号胜率汇总（可按 source/symbol/status 过滤）：胜率、平均收益、结果分布与明细。by_source附avg_return_pct、return_samples和sample_warning；仅观察性复盘，样本少不能认定策略有效。",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "按信号来源过滤（可选）"},
                "symbol": {"type": "string", "description": "按股票代码过滤（可选）"},
                "status": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "description": "open=未结算，closed=已结算，all=全部（默认）",
                },
            },
        },
    },
    {
        "name": "parse_stock_list",
        "description": "自选股/文本导入解析 — 从自然语言、CSV、Markdown 表格提取股票，自动识别 A 股 6 位代码、港股 xxxxx.HK、美股 ticker、日股 xxxx.T、韩股 xxxxxx.KS/KQ、台股 xxxx.TW，并调用 name_resolver 处理中文股票名",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待解析的文本"},
            },
            "required": ["text"],
        },
    },
]

TOOL_SCHEMAS += [
    {
        "name": "compare_stocks",
        "description": "无状态同行指标比较：先用现有财务/行情工具取数，再传companies；仅相同unit/basis/period可排序，缺失不填零，不生成买卖综合分。",
        "parameters": {
            "type": "object",
            "properties": {
                "companies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "唯一股票代码"},
                            "period": {"type": "string", "description": "统一可比期间；财务用报告期，估值用估值日"},
                            "metrics": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "description": "指标名，如pe/roe/net_income"},
                                        "value": {
                                            "anyOf": [{"type": "number"}, {"type": "null"}],
                                            "description": "有限数值；缺失为null",
                                        },
                                        "unit": {
                                            "type": "string",
                                            "description": "精确单位，如multiple/percent/fraction/CNY/USD",
                                        },
                                        "basis": {
                                            "type": "string",
                                            "description": "口径，如TTM/annual/consolidated；区分累计与单季",
                                        },
                                        "source": {"type": "string", "description": "原始证据URL或字段路径"},
                                    },
                                    "required": ["name", "value", "unit", "basis"],
                                    "additionalProperties": False,
                                },
                                "description": "结构化指标，单位、口径明确，缺失不填零",
                            },
                        },
                        "required": ["symbol", "period", "metrics"],
                        "additionalProperties": False,
                    },
                    "description": "2..30家公司，调用方确认可比性",
                }
            },
            "required": ["companies"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_disclosure_changes",
        "description": "无状态公告财报比较：传新旧正文及指标，返回变更行号、来源与财务变化；负/零基数增长率为null。不下载PDF、不代替经济解读。",
        "parameters": {
            "type": "object",
            "properties": {
                "previous": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "已核实正文，最多200000字符；保留换行，PDF需宿主提取",
                        },
                        "source": {"type": "string", "description": "原文URL或文档标识"},
                        "period": {"type": "string", "description": "报告期，如2025年度"},
                        "metrics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "指标名，如pe/roe/net_income"},
                                    "value": {
                                        "anyOf": [{"type": "number"}, {"type": "null"}],
                                        "description": "有限数值；缺失为null",
                                    },
                                    "unit": {
                                        "type": "string",
                                        "description": "精确单位，如multiple/percent/fraction/CNY/USD",
                                    },
                                    "basis": {
                                        "type": "string",
                                        "description": "口径，如TTM/annual/consolidated；区分累计与单季",
                                    },
                                    "source": {"type": "string", "description": "原始证据URL或字段路径"},
                                },
                                "required": ["name", "value", "unit", "basis"],
                                "additionalProperties": False,
                            },
                            "description": "结构化指标，单位、口径明确，缺失不填零",
                        },
                    },
                    "required": ["text", "source", "period", "metrics"],
                    "additionalProperties": False,
                },
                "current": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "已核实正文，最多200000字符；保留换行，PDF需宿主提取",
                        },
                        "source": {"type": "string", "description": "原文URL或文档标识"},
                        "period": {"type": "string", "description": "报告期，如2025年度"},
                        "metrics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "指标名，如pe/roe/net_income"},
                                    "value": {
                                        "anyOf": [{"type": "number"}, {"type": "null"}],
                                        "description": "有限数值；缺失为null",
                                    },
                                    "unit": {
                                        "type": "string",
                                        "description": "精确单位，如multiple/percent/fraction/CNY/USD",
                                    },
                                    "basis": {
                                        "type": "string",
                                        "description": "口径，如TTM/annual/consolidated；区分累计与单季",
                                    },
                                    "source": {"type": "string", "description": "原始证据URL或字段路径"},
                                },
                                "required": ["name", "value", "unit", "basis"],
                                "additionalProperties": False,
                            },
                            "description": "结构化指标，单位、口径明确，缺失不填零",
                        },
                    },
                    "required": ["text", "source", "period", "metrics"],
                    "additionalProperties": False,
                },
                "max_changes": {"type": "number", "description": "变化上限1..100，默认30"},
            },
            "required": ["previous", "current"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_portfolio_risk",
        "description": "无状态现货多头组合风险：输入持仓、基准币种现金与汇率，返回集中度、币种/行业敞口、价格盈亏和止损情景。缺汇率报错；不保存账户。",
        "parameters": {
            "type": "object",
            "properties": {
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "唯一代码，重复持仓先汇总"},
                            "quantity": {"type": "number", "description": "正持仓数量"},
                            "price": {"type": "number", "description": "正现价"},
                            "currency": {"type": "string", "description": "持仓币种，如CNY/USD/HKD"},
                            "cost": {"type": "number", "description": "同币种正成本价，可选"},
                            "stop_price": {"type": "number", "description": "同币种正止损价，可选"},
                            "sector": {"type": "string", "description": "统一行业分类，可选"},
                            "as_of": {"type": "string", "description": "实际估值时点，建议ISO8601"},
                        },
                        "required": ["symbol", "quantity", "price", "currency"],
                        "additionalProperties": False,
                    },
                    "description": "1..100个现货多头持仓",
                },
                "base_currency": {"type": "string", "description": "基准币种"},
                "cash": {"type": "number", "description": "基准币种现金，非负"},
                "fx_rates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "currency": {"type": "string", "description": "唯一非基准币种"},
                            "rate": {"type": "number", "description": "一单位该币种对应多少基准币种，正数"},
                        },
                        "required": ["currency", "rate"],
                        "additionalProperties": False,
                    },
                    "description": "显式现价汇率；基准币种隐含1，不得重复",
                },
                "histories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "对应唯一持仓股票"},
                            "prices": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "date": {"type": "string", "description": "YYYY-MM-DD，唯一日期"},
                                        "close": {"type": "number", "description": "正有限收盘价"},
                                    },
                                    "required": ["date", "close"],
                                    "additionalProperties": False,
                                },
                                "description": "完整日K收盘序列，2..20000条，不得跨股票或混用复权口径",
                            },
                        },
                        "required": ["symbol", "prices"],
                        "additionalProperties": False,
                    },
                    "description": "可选历史，共同日期本币价格收益相关性，不计汇率",
                },
            },
            "required": ["positions", "base_currency", "cash"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_position_size",
        "description": "无状态多头仓位预算：同币种金额，按现金/风险预算/最大仓位/交易单位取最小数量，双边费用和滑点参与止损情景；不执行交易。",
        "parameters": {
            "type": "object",
            "properties": {
                "equity": {"type": "number", "description": "总权益，正数"},
                "cash": {"type": "number", "description": "可用现金，0..equity"},
                "entry_price": {"type": "number", "description": "计划入场价，正数"},
                "stop_price": {"type": "number", "description": "正止损价，低于入场价"},
                "risk_fraction": {"type": "number", "description": "单笔风险占权益比例0..1，如0.01"},
                "lot_size": {"type": "number", "description": "正整数交易单位，用户确认，不猜港股每手数量"},
                "max_position_fraction": {
                    "type": "number",
                    "description": "本次买入最大资金占比0..1，默认1；已有同股持仓先扣减额度",
                },
                "fee_rate": {"type": "number", "description": "每边比例费用0..0.1，默认0；不含未提供税费/最低收费"},
                "slippage_rate": {"type": "number", "description": "每边滑点0..0.1，默认0"},
            },
            "required": ["equity", "cash", "entry_price", "stop_price", "risk_fraction", "lot_size"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_event_study",
        "description": "无状态事件研究：提供同市场股票及基准完整价格、事件日期；事件前一交易日收盘为基点，计算交易日窗口收益差。缺窗口/基准为null，标注重叠，不推断因果。",
        "parameters": {
            "type": "object",
            "properties": {
                "prices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD，唯一日期"},
                            "close": {"type": "number", "description": "正有限收盘价"},
                        },
                        "required": ["date", "close"],
                        "additionalProperties": False,
                    },
                    "description": "完整日K收盘序列，2..20000条，不得跨股票或混用复权口径",
                },
                "benchmark_prices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD，唯一日期"},
                            "close": {"type": "number", "description": "正有限收盘价"},
                        },
                        "required": ["date", "close"],
                        "additionalProperties": False,
                    },
                    "description": "完整日K收盘序列，2..20000条，不得跨股票或混用复权口径",
                },
                "event_dates": {
                    "type": "array",
                    "items": {"type": "string", "description": "YYYY-MM-DD，盘后公告传下一交易日"},
                    "description": "1..100个唯一事件日",
                },
                "price_basis": {
                    "type": "string",
                    "description": "仅显式prices模式必需；代码模式报告实际提供方标记，缺失为unknown，须核实",
                },
                "horizons": {
                    "type": "array",
                    "items": {"type": "number", "description": "0=事件日，1=下一交易日；整数0..250"},
                    "description": "1..20个窗口，默认0/1/5/10/20",
                },
                "start_date": {"type": "string", "description": "代码模式必需，YYYY-MM-DD，包含事件前基准日或训练窗口"},
                "end_date": {"type": "string", "description": "代码模式必需，YYYY-MM-DD，晚于start_date"},
                "symbol": {
                    "type": "string",
                    "description": "股票代码；与benchmark/start_date/end_date一起直接取数，和prices模式互斥",
                },
                "benchmark": {"type": "string", "description": "同市场基准代码；与symbol一起使用"},
            },
            "required": ["event_dates"],
            "additionalProperties": False,
        },
    },
    {
        "name": "validate_backtest",
        "description": "无状态回测稳健性：输入历史OHLCV和完整候选DSL；滚动训练选参数、后续不重叠窗口验证，列出全部样本内敏感性及跨标的结果。仅next_open，不自动优化或保存。",
        "parameters": {
            "type": "object",
            "properties": {
                "datasets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "唯一股票代码"},
                            "price_basis": {"type": "string", "description": "已核实复权/原始价格口径"},
                            "bars": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                                        "open": {"type": "number", "description": "有限OHLCV；价格为正、volume非负"},
                                        "high": {"type": "number", "description": "有限OHLCV；价格为正、volume非负"},
                                        "low": {"type": "number", "description": "有限OHLCV；价格为正、volume非负"},
                                        "close": {"type": "number", "description": "有限OHLCV；价格为正、volume非负"},
                                        "volume": {"type": "number", "description": "有限OHLCV；价格为正、volume非负"},
                                    },
                                    "required": ["date", "open", "high", "low", "close", "volume"],
                                    "additionalProperties": False,
                                },
                                "description": "日期递增完整OHLCV，每组最多2000条，至少train_bars+test_bars",
                            },
                        },
                        "required": ["symbol", "price_basis", "bars"],
                        "additionalProperties": False,
                    },
                    "description": "1..5个标的，复用get_kline取数",
                },
                "strategies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "唯一候选名"},
                            "strategy": {
                                "type": "object",
                                "additionalProperties": True,
                                "description": "完整既有YAML DSL对应JSON，允许parameters引用；仅next_open",
                            },
                        },
                        "required": ["name", "strategy"],
                        "additionalProperties": False,
                    },
                    "description": "1..5个预先确定的候选，禁止测试结果回调参数",
                },
                "train_bars": {"type": "number", "description": "训练交易日数30..1000，默认126"},
                "test_bars": {"type": "number", "description": "测试交易日数30..1000，默认63"},
                "capital": {"type": "number", "description": "每窗口初始资金，默认1000000"},
                "start_date": {"type": "string", "description": "代码模式必需，YYYY-MM-DD，包含事件前基准日或训练窗口"},
                "end_date": {"type": "string", "description": "代码模式必需，YYYY-MM-DD，晚于start_date"},
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1..5个唯一股票代码，配合start_date/end_date直接取数；和datasets互斥",
                },
            },
            "required": ["strategies"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_analysis_snapshots",
        "description": "无状态快照比较：传新旧JSON，返回JSON Pointer及新增/删除/变化/失效/恢复；不保存快照。数组整体比较，调用前按symbol对齐。",
        "parameters": {
            "type": "object",
            "properties": {
                "previous": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "JSON对象，详见共享Skill与README",
                },
                "current": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "JSON对象，详见共享Skill与README",
                },
            },
            "required": ["previous", "current"],
            "additionalProperties": False,
        },
    },
]
