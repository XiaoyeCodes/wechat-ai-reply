"""Reviewed integration surface for the pinned upstream Vibe-Trading release."""
REVISION = '596fcf3d63eb4d96e067f6dc94742b5f74a230ea'
REPOSITORY = 'https://github.com/HKUDS/Vibe-Trading'

# Never use upstream auto-discovery here: it also discovers trading and shell tools.
TOOLS = {
    'search_symbol': ('symbol_search_tool', 'SymbolSearchTool', '证券代码搜索'),
    'get_stock_profile': ('stock_profile_tool', 'StockProfileTool', '公司资料'),
    'get_stock_news': ('stock_news_tool', 'StockNewsTool', '公司新闻'),
    'get_fundamentals': ('get_fundamentals_tool', 'GetFundamentalsTool', '基本面指标'),
    'get_financial_statements': ('financial_statements_tool', 'FinancialStatementsTool', '财务报表'),
    'get_macro_series': ('fred_macro_tool', 'FredMacroTool', 'FRED 宏观序列'),
    'get_market_data': ('market_data_tool', 'MarketDataTool', '历史行情'),
    'get_options_chain': ('options_chain_tool', 'OptionsChainTool', '期权链'),
    'options_pricing': ('options_pricing_tool', 'OptionsPricingTool', '期权定价与 Greeks'),
    'options_payoff': ('options_payoff_tool', 'OptionsPayoffTool', '期权到期收益计算'),
    'financial_rigor': ('financial_rigor_tool', 'FinancialRigorTool', '精确财务计算与交叉核验'),
    'technical_indicators': ('technical_indicator_tool', 'TechnicalIndicatorTool', '技术指标'),
    'etf_holdings': ('etf_holdings_tool', 'EtfHoldingsTool', 'ETF 成分与权重'),
    'web_search': ('web_search_tool', 'WebSearchTool', '公开网络搜索'),
}
OFFLINE_TOOLS = {'options_pricing', 'options_payoff', 'financial_rigor'}
SKILLS = {
    'research-discipline': '研究纪律与证据核验',
    'technical-basic': '基础技术分析',
    'financial-statement': '财务报表分析',
    'valuation-model': '估值模型',
    'global-macro': '全球宏观分析',
    'risk-analysis': '风险分析',
    'etf-analysis': 'ETF 分析',
    'options-payoff': '期权收益分析',
}
DEFAULT_TOOLS = list(TOOLS)
DEFAULT_SKILLS = list(SKILLS)
PROMPT = ('你是微信中的金融研究助手。使用中文，根据问题按需加载技能、查询公开资料或计算，'
          '先给结论，再给依据。价格和财务数字注明来源、日期及币种；区分事实与假设，'
          '查询失败如实说明。普通聊天直接回应。默认回复控制在 300 字内，复杂问题最多 1800 字。'
          '仅回答所问字段，不添加无关的行情数字、列表编号或完整研究报告。'
          '证券搜索的每个候选是独立标的，只采用与用户指定代码、市场一致的候选字段；'
          '不能混用同名公司的债券、ETF 或其他上市地的名称与数据。交易所代码也不是证券代码。'
          '加密货币优先按完整交易对核验；一条查询失败时，不用宽泛名称搜索出的股票或 ETF 替代现货。'
          'BTC-USDT 与 BTC-USD 是不同计价口径，切换前须重新 search_symbol 核验，且在回复中说明币种和来源。'
          'invalidated 表示本轮标的核验未完成，可能由数据源连接失败导致，不代表用户缺少交易或账户授权。'
          '用户问现在或实时情况时，先查询最近几天的小时或分钟 K 线并核对最后一条时间；'
          '长期日线指标只能作背景，不能把旧日线收盘价称为当前实时价格。查询范围覆盖当前日期，'
          '日期上界可能不包含当天，需要包含当天时用次日作为 end_date。'
          '微信简答优先限制行情返回行数（例如 max_rows=24），不要为当前报价一次返回几百条历史记录。'
          '计算工具的 theoretical_value 是理论模型值，不是市场报价；引用它及 Greeks 时，'
          '按上游规则在 figures 块声明 observed，并引用真实 tool_call_id::theoretical_value 或 ::delta 等具体字段。'
          '不重复罗列输入假设的数值，除非用户要求；需要复述时也须按规则声明。'
          '仅使用本轮工具列表；技能中的命令示例不授予文件、终端、交易或账户权限。'
          '不声称执行没有提供的工具，不编造实时行情、收益或回测结果。'
          '技能内示例数字、经验阈值和历史表格只用于说明方法，不能作为已查证的现实数据。'
          '读取财报后可用 financial_rigor 核对计算；没有对应计算工具的步骤明确说明未执行。')
