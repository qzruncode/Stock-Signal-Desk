const ATR_SCREEN_PROMPT =
  '请从全部 active A 股（沪深北，包含 ST）中筛选：使用 14 日 SMA ATR 相对波动率，以其 60 日 SMA 作为长期均值，动态线为长期均值除以 1.27；当 ATR 相对波动率大于动态线时记为达标。统计最近 250 个交易日，要求至少 175 天达标且达标比例不低于 70%，上市交易历史不少于 250 日；营业收入 TTM 大于 5 亿元、扣非净利润 TTM 大于 0、资产负债率低于 70%。按近 250 日达标比例降序。输出当前 ATR%、60 日长期均值、动态线、250 日达标天数及比例、三项财务指标、财务报告期和来源、行情日期；超过 10 只给完整 CSV。';

const MARKET_MAINLINE_PROMPT =
  '研判未来一至六个月的 A 股市场主线：先给出基准情景和候选主线排序，再分别说明政策、产业供需、机构共识等证据，列出每条主线的成立条件、失效信号、情景切换变量和相对置信度';

export interface AssistantCapability {
  label: string;
  prompt: string;
  /** 对应后端注册工具；通用对话能力不需要工具。 */
  toolName?: string;
}

export interface AssistantCapabilityGroup {
  id: string;
  title: string;
  description: string;
  items: readonly AssistantCapability[];
}

/** 首页优先展示的研究问题。 */
export const SUGGESTIONS: readonly { label: string; prompt: string }[] = [
  {
    label: '帮我分析下人形机器人产业链，哪些领域最受益？',
    prompt: '帮我分析下人形机器人产业链，哪些领域最受益？',
  },
  {
    label: '比较贵州茅台与五粮液的盈利质量和当前估值',
    prompt: '比较贵州茅台与五粮液的盈利质量和当前估值',
  },
  {
    label: '宁德时代最近有哪些重要公告和风险事件？',
    prompt: '宁德时代最近有哪些重要公告和风险事件？',
  },
  {
    label: '未来一至六个月，A 股市场主线会是什么？',
    prompt: MARKET_MAINLINE_PROMPT,
  },
  {
    label: '按 14 日 ATR 相对波动率与财务条件筛选全部 A 股',
    prompt: ATR_SCREEN_PROMPT,
  },
] as const;

/** 本轮 RSSHub 与会话原始文件能力的可直接执行示例。 */
export const RSS_DOCUMENT_SUGGESTIONS: readonly AssistantCapability[] = [
  {
    label: '查看助手已筛选的 RSSHub 财经来源',
    prompt: '仅使用已筛选的 RSSHub 财经来源，不调用联网搜索、行情或财务工具。查找可用于 A 股公告、市场快讯和宏观资讯的来源，说明来源名称、路由、健康状态和用途',
  },
  {
    label: '读取指定公告 Feed，并打开最新 PDF',
    prompt: '只使用深交所“上市公司公告”路由 /szse/disclosure/listed/notice/:query?，参数 query=stock=001399，读取最近 5 条；然后读取最新一条的全文和 PDF 原文件，不要按标题重新搜索，也不要调用其他数据工具',
  },
  {
    label: '从最新公告 PDF 中按页提取关键信息',
    prompt: '仅使用已筛选的 RSSHub 财经来源，查找宁德时代最近 5 条公司公告；读取最新一条的全文和 PDF 原文件，并从同一份 PDF 提取公告事项、金额、期限、决议和风险控制措施，逐项引用页码，不要调用行情或财务工具',
  },
  {
    label: '把公开网页转换为纯文本资讯 Feed',
    prompt: '我会提供一个公开网页地址。请将网页转换为可订阅的资讯 Feed，预览纯文本条目并过滤图片、音频和视频；我确认后再按 RSS、Atom、JSON Feed 或 RSS3 格式导出',
  },
] as const;

/**
 * AI 助手的完整能力目录。
 *
 * 除第一组通用对话能力外，每项都与 `src/tools/registry.py` 中的一个注册工具
 * 一一对应。点击能力会发送一个安全、可继续补充条件的示例指令。
 */
export const ASSISTANT_CAPABILITY_GROUPS: readonly AssistantCapabilityGroup[] = [
  {
    id: 'general',
    title: '通用助手',
    description: '不局限于股票问题，也能完成日常知识与文字工作。',
    items: [
      { label: '有个日常问题，能帮我解释清楚吗？', prompt: '请回答我的日常知识问题，并在信息不确定时明确说明' },
      { label: '帮我把这段文字写得更自然一些', prompt: '请帮我起草或润色一段文字；先问我用途、读者和期望语气' },
      { label: '这个复杂概念能用大白话讲讲吗？', prompt: '请用通俗语言解释一个复杂概念，并给出一个具体例子' },
      { label: '帮我总结这段内容的重点', prompt: '我会提供一段内容，请帮我提炼结论、关键依据和待办事项' },
      { label: '帮我算一算，并给出可执行的计划', prompt: '我会提供目标和约束，请帮我完成计算或拆解一份可执行计划' },
    ],
  },
  {
    id: 'portfolio',
    title: '股票与自选',
    description: '查找股票，维护股票池和自选分组。',
    items: [
      { label: '帮我查找一只股票', prompt: '搜索新强联，并告诉我股票代码、所属市场和行业', toolName: 'search_stocks' },
      { label: '我的默认自选股里有哪些股票？', prompt: '列出我的默认自选股，并告诉我可以怎样添加或移除股票', toolName: 'manage_watchlist' },
      { label: '帮我看看有哪些自选分组', prompt: '列出我的全部自选分组、每组股票数量和成员，并说明可执行的管理操作', toolName: 'manage_watchlist_groups' },
      { label: '哪些自选股属于我关注的主题？', prompt: '先列出我的自选分组；我指定分组和主题后，请只在该分组内筛选相关股票', toolName: 'filter_watchlist_by_theme' },
      { label: '当前股票数据完整、够新吗？', prompt: '检查股票基础库、K 线和财务数据的覆盖范围、数据时间及新鲜度', toolName: 'get_data_health' },
    ],
  },
  {
    id: 'workflow',
    title: '报告与分析工作流',
    description: '保存正式报告，管理单股、批量、定时与通知任务。',
    items: [
      { label: '帮我生成一份正式的个股分析报告', prompt: '分析宁德时代，保存正式报告，完成后告诉我', toolName: 'run_stock_analysis' },
      { label: '我的分析任务进行到哪一步了？', prompt: '查看我正在运行和最近完成的分析任务', toolName: 'get_analysis_status' },
      { label: '帮我找找以前做过的分析报告', prompt: '查找最近完成的股票分析报告，先列出可选记录', toolName: 'search_analysis_history' },
      { label: '我想打开一份完整的历史报告', prompt: '先查找最近完成的股票分析报告；我指定一份后再读取完整内容', toolName: 'read_analysis_report' },
      { label: '帮我删除一份不再需要的历史报告', prompt: '先列出最近的历史分析报告；我确认具体记录后再删除', toolName: 'delete_analysis_history' },
      { label: '我有哪些分析模板可以使用？', prompt: '列出我的分析模板，并告诉我当前默认模板及可执行的管理操作', toolName: 'manage_analysis_templates' },
      { label: '帮我批量分析一个自选分组', prompt: '列出我的自选分组；我指定范围并确认后再发起批量分析', toolName: 'run_batch_analysis' },
      { label: '帮我查看或调整批量分析任务', prompt: '查看最近的批量分析任务及进度，并列出可暂停、继续或重试的任务', toolName: 'manage_batch_run' },
      { label: '我想设置一个定时分析计划', prompt: '查看当前的定时分析计划，并告诉我可以怎样创建或修改', toolName: 'manage_analysis_schedule' },
      { label: '我的分析通知配置好了吗？', prompt: '检查通知配置，并告诉我可以发送哪些分析通知；不要直接发送', toolName: 'get_notification_status' },
      { label: '把完成的分析报告通知给我', prompt: '先列出最近完成的分析报告；我确认具体报告后再发送通知', toolName: 'send_notification' },
    ],
  },
  {
    id: 'quant',
    title: '行情与量化',
    description: '获取价格、K 线、技术指标并完成多股与条件筛选。',
    items: [
      { label: '这只股票现在的实时行情怎么样？', prompt: '查询宁德时代当前行情，标明数据时间和来源', toolName: 'get_realtime_quotes' },
      { label: '帮我看看这只股票最近的 K 线', prompt: '查看宁德时代最近 60 个交易日的日 K 线，并说明数据时间范围', toolName: 'get_kline' },
      { label: '这只股票过去一年的表现如何？', prompt: '查询宁德时代过去一年的历史行情，并概括区间表现', toolName: 'get_history_data' },
      { label: '从技术面看，这只股票处于什么状态？', prompt: '分析宁德时代当前趋势、MACD、RSI、ATR、量价和支撑阻力', toolName: 'get_technical_indicators' },
      { label: '帮我快速比较几只股票的综合表现', prompt: '比较宁德时代、比亚迪和贵州茅台的行情、估值、技术面与最新财务快照', toolName: 'get_multi_stock_snapshot' },
      { label: '按财务指标筛选上面这些股票', prompt: '把上面这些股票中资产负债率高于70%的筛掉，并说明完整覆盖情况', toolName: 'get_multi_stock_financials' },
      { label: '这几只股票里，哪只更值得继续研究？', prompt: '比较宁德时代与比亚迪的投资决策证据、反证、成立条件和失效条件', toolName: 'get_multi_stock_decision_evidence' },
      { label: '先统一判断这批股票的产业方向是不是当前主线', prompt: '基于结构化产业方向和同一份市场主线快照，只做一次批次共享的市场主线判断，再继续逐股八维分析', toolName: 'evaluate_market_mainline_gate' },
      { label: '这几只股票现在有哪些能买入？', prompt: '按当前主线、产业竞争力、行业周期、竞争格局、增长驱动、未来催化、估值赔率和重大风险，完整分析宁德时代与比亚迪现在能否买入', toolName: 'evaluate_multi_stock_buy_criteria' },
      { label: '这家公司未来有哪些明确催化？', prompt: '看下鸣志电器未来 6—12 个月的催化事件，逐项给出明确时间窗、来源和证据状态', toolName: 'analyze_stock_catalysts' },
      { label: '项目当前有哪些实时概念板块？', prompt: '读取项目当前完整的实时概念板块目录，并说明板块总数、数据时间和来源', toolName: 'get_domain_board_catalog' },
      { label: '几个产业环节分别有哪些股票？', prompt: '按行星滚柱丝杠、减速器、无框力矩电机三个领域分别查找 A 股候选，只使用结构化板块和本地股票库', toolName: 'get_domain_stock_candidates' },
      { label: '逐只核验板块候选公司的业务进展', prompt: '先从项目实时板块获取目标领域的完整候选集合，再对每只股票分别核验公司资料、主营、公告、个股新闻和个股研报，完整报告逐股覆盖情况', toolName: 'get_company_theme_evidence' },
      { label: '帮我按 ATR 波动率和财务条件筛选股票', prompt: ATR_SCREEN_PROMPT, toolName: 'screen_atr_volatility_stocks' },
    ],
  },
  {
    id: 'market',
    title: '大盘、板块与资金',
    description: '观察交易状态、市场宽度、板块轮动与资金流。',
    items: [
      { label: '今天 A 股开市吗，现在是什么交易状态？', prompt: '查看当前 A 股市场是否开市，并标明交易日和数据时间', toolName: 'get_market_status' },
      {
        label: '未来一至六个月市场主线会是什么？',
        prompt: MARKET_MAINLINE_PROMPT,
        toolName: 'prepare_market_mainline_snapshot',
      },
      { label: '今天市场整体是普涨还是普跌？', prompt: '分析当前 A 股市场宽度，包括涨跌家数、涨跌停和整体强弱', toolName: 'get_market_breadth' },
      { label: '现在哪些行业和概念板块更强？', prompt: '列出当前表现较强和较弱的行业及概念板块，并标明领涨股', toolName: 'get_sector_list' },
      { label: '最近资金主要流向了哪些板块？', prompt: '比较最近 5 日主要行业板块的资金流向和持续性', toolName: 'get_sector_flow' },
      { label: '这只股票最近的资金流向怎么样？', prompt: '查看宁德时代近期资金流向，并说明数据口径和局限', toolName: 'get_stock_capital_flow' },
    ],
  },
  {
    id: 'company',
    title: '公司与财务',
    description: '核验公司画像、三大报表、估值、预期、同行和股东。',
    items: [
      { label: '这家公司主要是做什么的？', prompt: '介绍宁德时代的公司概况、所属行业和核心业务', toolName: 'get_stock_info' },
      { label: '这家公司的核心财务表现怎么样？', prompt: '查看宁德时代最新核心财务指标、同比趋势、报告期和来源', toolName: 'get_financials' },
      { label: '这家公司的资产负债表健康吗？', prompt: '分析宁德时代最新资产负债表的关键项目、变化和风险信号', toolName: 'get_balance_sheet' },
      { label: '这家公司的收入和利润增长如何？', prompt: '分析宁德时代最新利润表的收入、利润、费用和同比变化', toolName: 'get_income_statement' },
      { label: '这家公司的利润真的变成现金了吗？', prompt: '分析宁德时代最新现金流量表及利润与现金流的匹配情况', toolName: 'get_cashflow' },
      { label: '这家公司靠哪些业务赚钱？', prompt: '拆解宁德时代的主营业务、收入和利润构成及变化', toolName: 'get_business_segments' },
      { label: '这只股票现在的估值贵不贵？', prompt: '查看宁德时代当前 PE、PB、历史分位和估值数据日期', toolName: 'get_valuation_ratios' },
      { label: '机构怎么看这家公司未来的业绩？', prompt: '查看宁德时代未来盈利一致预期及预测分歧，说明统计期和机构覆盖', toolName: 'get_consensus_estimates' },
      { label: '和同行相比，这家公司表现如何？', prompt: '选择合适同行比较宁德时代的盈利、成长、估值和财务质量', toolName: 'get_peer_comparison' },
      { label: '这家公司的主要股东最近有变化吗？', prompt: '查看宁德时代最新股东结构、持股变化和报告期', toolName: 'get_shareholder_structure' },
    ],
  },
  {
    id: 'information',
    title: '资讯、事件与研报',
    description: '检索资讯源和正文，核验公告、监管、研报、风险与情绪。',
    items: [
      { label: '这只股票最近有什么重要新闻？', prompt: '搜索宁德时代最近的重要新闻，按时间排序并标明来源', toolName: 'search_news' },
      { label: '今天有哪些值得关注的财经资讯？', prompt: '搜索今天最重要的 A 股市场资讯，按主题归类并标明来源和时间', toolName: 'search_financial_news' },
      { label: '助手可以查看哪些 RSSHub 来源？', prompt: '列出助手已筛选的 RSSHub 财经来源，按命名空间、健康状态和用途分组', toolName: 'discover_rss_sources' },
      { label: '这个资讯来源具体包含什么内容？', prompt: '先列出可用资讯源；我指定一个后再查看它的参数、主题和运行状态', toolName: 'inspect_rss_source' },
      { label: '帮我读取一个指定的 RSSHub 来源', prompt: '先动态发现可用来源；我指定来源和筛选条件后再读取该 Feed', toolName: 'read_rss_feed' },
      { label: '帮我把资讯和原始文件读完', prompt: '先搜索宁德时代最近的重要资讯；我指定一条后读取准确条目、文本附件和完整原文', toolName: 'read_rss_item' },
      { label: '继续分析刚才那份原始文件', prompt: '继续读取当前会话中刚才那份原始文本文件，按页码、章节或表格位置引用证据，不要按标题重新搜索', toolName: 'read_text_document' },
      { label: '能把这个网页变成可订阅的资讯源吗？', prompt: '我会提供一个公开网页地址，请尝试把它转换成可订阅的资讯 Feed', toolName: 'transform_webpage_to_feed' },
      { label: '帮我把这个资讯源导出来', prompt: '先读取我指定的资讯源，再按我选择的 RSS、Atom、JSON Feed 或 RSS3 格式导出', toolName: 'export_rss_feed' },
      { label: '帮我找找这个行业相关的券商研报', prompt: '搜索人形机器人产业链相关研报，列出标题、机构、时间和核心摘要', toolName: 'search_research_library' },
      { label: '近期有哪些重要的监管政策变化？', prompt: '搜索近期影响 A 股的重要监管政策与规则变化，标明原始来源和时间', toolName: 'get_regulatory_updates' },
      { label: '央行最近在公开市场做了哪些操作？', prompt: '查看近期央行公开市场操作，说明规模、利率、到期量和流动性影响', toolName: 'get_monetary_policy_operations' },
      { label: '这家公司最近发布了哪些重要公告？', prompt: '查看宁德时代最近的重要公司公告，区分正式披露与媒体报道', toolName: 'get_announcements' },
      { label: '这家公司最近有什么风险事件吗？', prompt: '检查宁德时代近期减持、处罚、诉讼、业绩预警等风险事件', toolName: 'get_risk_events' },
      { label: '券商最近是怎么看这只股票的？', prompt: '搜索宁德时代近期券商研究报告，汇总核心观点和分歧', toolName: 'get_research_report' },
      { label: '大家最近对这只股票的情绪怎么样？', prompt: '查看宁德时代近期社交讨论情绪和热度，并说明样本局限', toolName: 'get_social_sentiment' },
    ],
  },
  {
    id: 'macro-web',
    title: '宏观与公开网页',
    description: '补充指数、利率、宏观指标和公开互联网证据。',
    items: [
      { label: '主要市场指数最近表现怎么样？', prompt: '查看上证指数、沪深 300 和创业板指近期表现并标明数据时间', toolName: 'get_index_data' },
      { label: '近期国债收益率发生了什么变化？', prompt: '查看近期中国国债收益率及期限结构变化，并说明数据日期', toolName: 'get_bond_yield' },
      { label: '最近的宏观经济数据表现如何？', prompt: '查看中国近期 CPI、PPI、PMI 和社融等宏观指标，标明统计期', toolName: 'get_macro_indicator' },
      { label: '帮我从公开网页查找可靠资料', prompt: '搜索公开网页中关于固态电池产业进展的可靠资料，并交叉核验来源', toolName: 'websearch' },
      { label: '帮我阅读并总结这个网页', prompt: '我会提供一个公开网页地址，请读取正文、提炼关键事实并标明来源', toolName: 'webfetch' },
    ],
  },
] as const;

export const ASSISTANT_CAPABILITY_COUNT = ASSISTANT_CAPABILITY_GROUPS.reduce(
  (total, group) => total + group.items.length,
  0,
);

export const ASSISTANT_TOOL_CAPABILITY_COUNT = ASSISTANT_CAPABILITY_GROUPS.reduce(
  (total, group) => total + group.items.filter((item) => item.toolName).length,
  0,
);

/** 首页“你可以这样问”的分类列表：原有问题在前，全部能力依次追加。 */
export const ASSISTANT_SUGGESTION_GROUPS: readonly AssistantCapabilityGroup[] = [
  {
    id: 'featured',
    title: '精选投研问题',
    description: '从产业链、公司比较、公告风险和市场强弱开始研究。',
    items: SUGGESTIONS,
  },
  {
    id: 'rss-documents',
    title: 'RSSHub 与原始文件',
    description: '发现来源、读取指定 Feed，并在会话中预览和分析 PDF 等文本文件。',
    items: RSS_DOCUMENT_SUGGESTIONS,
  },
  ...ASSISTANT_CAPABILITY_GROUPS,
];

export const ASSISTANT_SUGGESTIONS: readonly { label: string; prompt: string }[] = [
  ...ASSISTANT_SUGGESTION_GROUPS.flatMap((group) => group.items),
];
