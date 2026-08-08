"""Stable source metadata for Agent-visible RSSHub atomic tools.

This module describes data sources only.  It contains no query routing,
relevance score, fallback chain, or workflow.  Every definition below becomes
one model-visible tool whose executor reads exactly the fixed ``route_path``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RssRouteParameter:
    """One path parameter accepted by a fixed RSSHub route."""

    name: str
    description: str
    required: bool = False
    enum: tuple[str, ...] = ()
    route_default: str | None = None
    retrieval_query: bool = False


@dataclass(frozen=True)
class RssSourceDefinition:
    """Static identity and schema metadata for one RSSHub data source."""

    tool_name: str
    route_path: str
    namespace: str
    provider: str
    feed_name: str
    purpose: str
    capabilities: frozenset[str]
    parameters: tuple[RssRouteParameter, ...] = ()


def _parameter(
    name: str,
    description: str,
    *,
    required: bool = False,
    enum: tuple[str, ...] = (),
    route_default: str | None = None,
    retrieval_query: bool = False,
) -> RssRouteParameter:
    return RssRouteParameter(
        name=name,
        description=description,
        required=required,
        enum=enum,
        route_default=route_default,
        retrieval_query=retrieval_query,
    )


def _source(
    tool_name: str,
    route_path: str,
    namespace: str,
    provider: str,
    feed_name: str,
    purpose: str,
    *capabilities: str,
    parameters: tuple[RssRouteParameter, ...] = (),
) -> RssSourceDefinition:
    return RssSourceDefinition(
        tool_name=tool_name,
        route_path=route_path,
        namespace=namespace,
        provider=provider,
        feed_name=feed_name,
        purpose=purpose,
        capabilities=frozenset(capabilities),
        parameters=parameters,
    )


RSS_SOURCE_DEFINITIONS: tuple[RssSourceDefinition, ...] = (
    _source(
        "read_rss_10jqka_realtime_news",
        "/10jqka/realtimenews/:tag?",
        "10jqka",
        "同花顺财经",
        "7x24 小时要闻直播",
        "读取同花顺实时市场要闻，可按标签缩小范围。",
        "market",
        "realtime",
        parameters=(
            _parameter("tag", "可选标签；留空读取全部实时要闻"),
        ),
    ),
    _source(
        "read_rss_cih_index_reports",
        "/cih-index/report/list/:report?",
        "cih-index",
        "中指研究院",
        "房地产与城市指数报告",
        "读取中指研究院报告列表；分类编码可先由 list_rss_cih_report_categories 获取。",
        "research",
        "industry",
        parameters=(
            _parameter("report", "报告分类路径段；留空读取最新全部报告"),
        ),
    ),
    _source(
        "read_rss_cls_telegraph",
        "/cls/telegraph/:category?",
        "cls",
        "财联社",
        "电报",
        "读取财联社实时电报，可按财联社分类筛选。",
        "market",
        "realtime",
        parameters=(
            _parameter("category", "财联社电报分类；留空读取全部"),
        ),
    ),
    _source(
        "read_rss_cls_depth",
        "/cls/depth/:category?",
        "cls",
        "财联社",
        "深度文章",
        "读取财联社深度文章，覆盖公司、行业和市场分析。",
        "market",
        "company",
        "industry",
        parameters=(
            _parameter("category", "财联社深度频道分类代码；留空读取默认频道"),
        ),
    ),
    _source(
        "read_rss_cls_hot_articles",
        "/cls/hot",
        "cls",
        "财联社",
        "热门文章排行榜",
        "读取财联社热门文章榜。",
        "market",
        "social",
    ),
    _source(
        "read_rss_cls_subject",
        "/cls/subject/:id?",
        "cls",
        "财联社",
        "指定话题文章",
        "读取一个财联社话题；话题编号可先由 list_rss_cls_subjects 获取。",
        "market",
        "company",
        "industry",
        parameters=(
            _parameter("id", "财联社话题编号；留空使用上游默认话题"),
        ),
    ),
    _source(
        "read_rss_eastmoney_search",
        "/eastmoney/search/:keyword",
        "eastmoney",
        "东方财富",
        "关键词资讯搜索",
        "按原样关键词读取东方财富资讯搜索结果。",
        "company",
        "industry",
        parameters=(
            _parameter(
                "keyword",
                "东方财富搜索关键词",
                required=True,
                retrieval_query=True,
            ),
        ),
    ),
    _source(
        "read_rss_eastmoney_reports",
        "/eastmoney/report/:category",
        "eastmoney",
        "东方财富",
        "研究报告",
        "读取东方财富策略、宏观、晨报、行业或个股研报列表。",
        "research",
        "company",
        "industry",
        "macro",
        parameters=(
            _parameter(
                "category",
                "研报类型：策略、宏观、券商晨报、行业或个股",
                required=True,
                enum=("strategyreport", "macresearch", "brokerreport", "industry", "stock"),
            ),
        ),
    ),
    _source(
        "read_rss_eeo_flash",
        "/eeo/kuaixun",
        "eeo",
        "经济观察网",
        "财经快讯",
        "读取经济观察网财经快讯。",
        "market",
        "realtime",
    ),
    _source(
        "read_rss_followin_news",
        "/followin/news/:lang?",
        "followin",
        "Followin",
        "全球财经新闻",
        "读取 Followin 全球财经新闻，可选择简体中文、繁体中文、英文或越南语。",
        "market",
        "company",
        parameters=(
            _parameter(
                "lang",
                "返回语言；留空使用上游默认语言",
                enum=("en", "zh-Hans", "zh-Hant", "vi"),
            ),
        ),
    ),
    _source(
        "read_rss_futunn_live",
        "/futunn/live/:lang?",
        "futunn",
        "富途牛牛",
        "实时快讯",
        "读取富途牛牛实时快讯。",
        "market",
        "realtime",
        parameters=(
            _parameter(
                "lang",
                "快讯语言；留空使用上游默认语言",
                enum=("Mandarin", "Cantonese", "English"),
            ),
        ),
    ),
    _source(
        "read_rss_futunn_headlines",
        "/futunn/main",
        "futunn",
        "富途牛牛",
        "财经要闻",
        "读取富途牛牛财经要闻。",
        "market",
        "company",
    ),
    _source(
        "read_rss_futunn_topic",
        "/futunn/topic/:id",
        "futunn",
        "富途牛牛",
        "指定专题",
        "读取一个富途专题；专题编号可先由 list_rss_futunn_topics 获取。",
        "company",
        "industry",
        parameters=(
            _parameter("id", "富途专题编号", required=True),
        ),
    ),
    _source(
        "read_rss_fx678_flash",
        "/fx678/kx",
        "fx678",
        "汇通网",
        "7x24 小时快讯",
        "读取汇通网外汇、宏观和市场快讯。",
        "market",
        "macro",
        "realtime",
    ),
    _source(
        "read_rss_gelonghui_live",
        "/gelonghui/live",
        "gelonghui",
        "格隆汇",
        "实时快讯",
        "读取格隆汇实时市场快讯。",
        "market",
        "realtime",
    ),
    _source(
        "read_rss_gelonghui_hot_articles",
        "/gelonghui/hot-article/:type?",
        "gelonghui",
        "格隆汇",
        "最热文章",
        "读取格隆汇日榜或周榜热门文章。",
        "market",
        "social",
        parameters=(
            _parameter("type", "榜单周期", enum=("day", "week")),
        ),
    ),
    _source(
        "read_rss_gelonghui_subject",
        "/gelonghui/subject/:id",
        "gelonghui",
        "格隆汇",
        "指定主题文章",
        "读取一个格隆汇主题；主题编号可先由 list_rss_gelonghui_subjects 获取。",
        "company",
        "industry",
        parameters=(
            _parameter("id", "格隆汇主题编号", required=True),
        ),
    ),
    _source(
        "read_rss_gelonghui_home",
        "/gelonghui/home/:tag?",
        "gelonghui",
        "格隆汇",
        "频道首页文章",
        "读取格隆汇推荐、股票、基金、新股或研报频道。",
        "market",
        "company",
        "industry",
        parameters=(
            _parameter(
                "tag",
                "频道标签；留空读取推荐频道",
                enum=("web_home_page", "stock", "fund", "new_stock", "research"),
            ),
        ),
    ),
    _source(
        "read_rss_gelonghui_search",
        "/gelonghui/keyword/:keyword",
        "gelonghui",
        "格隆汇",
        "关键词文章搜索",
        "按原样关键词读取格隆汇文章搜索结果。",
        "company",
        "industry",
        parameters=(
            _parameter(
                "keyword",
                "格隆汇搜索关键词",
                required=True,
                retrieval_query=True,
            ),
        ),
    ),
    _source(
        "read_rss_pbc_open_market_announcements",
        "/gov/pbc/tradeAnnouncement",
        "gov",
        "中国人民银行",
        "公开市场业务交易公告",
        "读取人民银行公开市场操作公告。",
        "macro",
        "monetary_policy",
        "regulatory",
    ),
    _source(
        "read_rss_hexun_private_equity_news",
        "/hexun/pe/news",
        "hexun",
        "和讯网",
        "创投行业新闻",
        "读取和讯创投与私募股权行业新闻。",
        "company",
        "industry",
    ),
    _source(
        "read_rss_investor_org_channel",
        "/investor/:id{.+}?",
        "investor",
        "中国投资者网",
        "投资者保护栏目",
        "读取中国投资者网的政策资讯、投资者保护、持股行权或维权调解栏目。",
        "company",
        "industry",
        "regulatory",
        parameters=(
            _parameter("id", "栏目路径；留空读取最新动态，如 zczx 或 qybh/cgxq"),
        ),
    ),
    _source(
        "read_rss_jin10_category",
        "/jin10/category/:id",
        "jin10",
        "金十数据",
        "指定财经分类",
        "按金十分类编号读取外汇、宏观或市场资讯。",
        "market",
        "macro",
        "realtime",
        parameters=(
            _parameter("id", "金十分类编号", required=True),
        ),
    ),
    _source(
        "read_rss_jin10_market_flash",
        "/jin10/:important?",
        "jin10",
        "金十数据",
        "市场快讯",
        "读取金十市场快讯，可选择只看重要快讯。",
        "market",
        "macro",
        "realtime",
        parameters=(
            _parameter("important", "任意非空值表示只看重要快讯；留空读取全部"),
        ),
    ),
    _source(
        "read_rss_jrj_channel",
        "/jrj/:channelNum",
        "jrj",
        "金融界",
        "指定资讯频道",
        "读取金融界的财经、科技、汽车、医疗、消费、A股、港股等单一频道。",
        "market",
        "company",
        "industry",
        parameters=(
            _parameter(
                "channelNum",
                "金融界频道编号",
                required=True,
                enum=(
                    "001", "004", "007", "009", "010", "102", "103", "104",
                    "105", "106", "107", "112", "113", "115", "118", "119",
                    "503", "508", "603", "629", "630", "632",
                ),
            ),
        ),
    ),
    _source(
        "read_rss_mckinsey_china_insights",
        "/mckinsey/cn/:category?",
        "mckinsey",
        "麦肯锡中国",
        "行业与宏观洞见",
        "读取麦肯锡中国在制造、汽车、医疗、科技、消费、宏观等领域的洞见。",
        "research",
        "industry",
        "macro",
        parameters=(
            _parameter(
                "category",
                "麦肯锡洞见分类；留空读取最新洞见",
                enum=(
                    "autos", "banking-insurance", "consumers", "healthcare-pharmaceuticals",
                    "business-technology", "manufacturing", "technology-media-and-telecom",
                    "urbanization-sustainability", "innovation", "talent-leadership",
                    "macroeconomy", "mckinsey-global-institute", "insights",
                    "capital-projects-infrastructure", "交通运输与物流", "全球基础材料",
                    "出海与国际化、转型",
                ),
            ),
        ),
    ),
    _source(
        "read_rss_moodys_industry_reports",
        "/moodysmismicrosite/report/:industry?",
        "moodysmismicrosite",
        "穆迪评级",
        "行业与评级报告",
        "读取穆迪企业、金融机构、主权、城投、宏观、结构融资或 ESG 报告。",
        "research",
        "industry",
        "macro",
        "rating",
        parameters=(
            _parameter(
                "industry",
                "行业编号；可用 & 连接多个编号，留空读取全部",
            ),
        ),
    ),
    _source(
        "read_rss_nanhua_futures_reports",
        "/nanhua/report/:type1/:type2",
        "nanhua",
        "南华期货",
        "期货研报",
        "读取南华期货一个明确分类的研报；合法分类组合可先由 list_rss_nanhua_report_types 获取。",
        "research",
        "industry",
        "macro",
        "futures",
        parameters=(
            _parameter("type1", "一级分类代码", required=True),
            _parameter("type2", "与一级分类匹配的二级分类代码", required=True),
        ),
    ),
    _source(
        "read_rss_nifd_research",
        "/nifd/research/:categoryGuid?",
        "nifd",
        "国家金融与发展实验室",
        "宏观与金融研究",
        "读取国家金融与发展实验室的周报及专题研究。",
        "research",
        "macro",
        "industry",
        parameters=(
            _parameter("categoryGuid", "研究分类标识；留空读取默认周报"),
        ),
    ),
    _source(
        "read_rss_qianzhan_analyst_columns",
        "/qianzhan/analyst/column/:type?",
        "qianzhan",
        "前瞻网",
        "产业分析文章",
        "读取前瞻网产业分析师文章列表。",
        "research",
        "industry",
        parameters=(
            _parameter("type", "前瞻文章分类；留空读取默认分类"),
        ),
    ),
    _source(
        "read_rss_qianzhan_analyst_rankings",
        "/qianzhan/analyst/rank/:type?",
        "qianzhan",
        "前瞻网",
        "产业排行榜",
        "读取前瞻网产业分析排行榜。",
        "research",
        "industry",
        "ranking",
        parameters=(
            _parameter("type", "前瞻排行榜分类；留空读取默认分类"),
        ),
    ),
    _source(
        "read_rss_sse_disclosures",
        "/sse/disclosure/:query?",
        "sse",
        "上海证券交易所",
        "上市公司公告",
        "读取上交所正式公司公告；query 可携带交易所原生筛选条件。",
        "announcement",
        "regulatory",
        "company",
        parameters=(
            _parameter("query", "上交所原生公告筛选查询串；留空读取最新公告"),
        ),
    ),
    _source(
        "read_rss_sse_inquiries",
        "/sse/inquire",
        "sse",
        "上海证券交易所",
        "监管问询",
        "读取上交所监管问询记录。",
        "announcement",
        "regulatory",
    ),
    _source(
        "read_rss_sse_star_market_projects",
        "/sse/renewal",
        "sse",
        "上海证券交易所",
        "科创板项目动态",
        "读取上交所科创板发行上市项目动态。",
        "announcement",
        "regulatory",
        "project",
    ),
    _source(
        "read_rss_stcn_news",
        "/stcn/article/list/:id?",
        "stcn",
        "证券时报网",
        "指定资讯频道",
        "读取证券时报要闻、股市、公司、产经、科创板、ESG 等频道文章。",
        "market",
        "company",
        "industry",
        parameters=(
            _parameter(
                "id",
                "证券时报频道；留空读取要闻",
                enum=("yw", "gs", "company", "fund", "finance", "comment", "cj", "kcb", "xsb", "zk", "gd"),
            ),
        ),
    ),
    _source(
        "read_rss_stcn_flash",
        "/stcn/article/list/kx",
        "stcn",
        "证券时报网",
        "财经快讯",
        "读取证券时报财经快讯。",
        "market",
        "realtime",
    ),
    _source(
        "read_rss_stcn_rankings",
        "/stcn/article/rank/:id?",
        "stcn",
        "证券时报网",
        "文章热榜",
        "读取证券时报指定频道热榜。",
        "market",
        "social",
        parameters=(
            _parameter(
                "id",
                "证券时报热榜频道；留空读取要闻",
                enum=("yw", "gs", "company", "fund", "finance", "comment", "cj", "kcb", "xsb", "zk", "gd"),
            ),
        ),
    ),
    _source(
        "read_rss_szse_inquiries",
        "/szse/inquire/:category?/:select?/:keyword?",
        "szse",
        "深圳证券交易所",
        "问询函件",
        "读取深交所问询函，可按板块、函件类别和公司代码或简称筛选。",
        "announcement",
        "regulatory",
        parameters=(
            _parameter(
                "category",
                "板块类型：0 为主板，1 为创业板；留空使用主板",
                enum=("0", "1"),
                route_default="0",
            ),
            _parameter(
                "select",
                "函件类别；留空读取全部类别",
                route_default="全部函件类别",
            ),
            _parameter("keyword", "公司代码或简称；留空不限定公司"),
        ),
    ),
    _source(
        "read_rss_szse_growth_market_projects",
        "/szse/projectdynamic/:type?/:stage?/:status?",
        "szse",
        "深圳证券交易所",
        "创业板项目动态",
        "读取深交所创业板发行上市项目，可按类型、阶段和状态筛选。",
        "announcement",
        "regulatory",
        "project",
        parameters=(
            _parameter(
                "type",
                "项目类型：1 为 IPO，2 为再融资，3 为重大资产重组",
                enum=("1", "2", "3"),
                route_default="1",
            ),
            _parameter(
                "stage",
                "审核阶段代码；0 为全部，10 受理，20 问询，30 上市委会议，35 提交注册，40 注册结果，50 中止，60 终止",
                enum=("0", "10", "20", "30", "35", "40", "50", "60"),
                route_default="0",
            ),
            _parameter(
                "status",
                "项目状态代码；0 为全部，20 新受理，30 已问询，45 通过，44 未通过，46 暂缓，56 复审通过，54 复审未通过，60 提交注册，70 注册生效，74 不予注册，78 补充审核，76 终止注册，80 中止，90 审核不通过，95 撤回",
                enum=("0", "20", "30", "45", "44", "46", "56", "54", "60", "70", "74", "78", "76", "80", "90", "95"),
                route_default="0",
            ),
        ),
    ),
    _source(
        "read_rss_szse_disclosures",
        "/szse/disclosure/listed/notice/:query?",
        "szse",
        "深圳证券交易所",
        "上市公司公告",
        "读取深交所正式公司公告；query 支持 stock、beginDate 和 endDate。",
        "announcement",
        "regulatory",
        "company",
        parameters=(
            _parameter(
                "query",
                "筛选查询串，例如 stock=000001&beginDate=2026-01-01&endDate=2026-01-31",
            ),
        ),
    ),
    _source(
        "read_rss_szse_convertible_bond_notices",
        "/szse/notice",
        "szse",
        "深圳证券交易所",
        "可转换债券上市公告",
        "读取深交所可转换债券上市公告。",
        "announcement",
        "regulatory",
        "convertible_bond",
    ),
    _source(
        "read_rss_wabei_hot_recommendations",
        "/wabei/hot-recommend",
        "wabei",
        "挖贝网",
        "热门推荐",
        "读取挖贝网热门公司和市场文章。",
        "market",
        "company",
        "social",
    ),
    _source(
        "read_rss_wallstreetcn_hot_articles",
        "/wallstreetcn/hot/:period?",
        "wallstreetcn",
        "华尔街见闻",
        "热门文章",
        "读取华尔街见闻当日或当周热门文章。",
        "market",
        "social",
        parameters=(
            _parameter("period", "榜单周期；留空读取当日", enum=("day", "week")),
        ),
    ),
    _source(
        "read_rss_wallstreetcn_news",
        "/wallstreetcn/news/:category?",
        "wallstreetcn",
        "华尔街见闻",
        "财经资讯",
        "读取华尔街见闻市场、公司、宏观和行业资讯。",
        "market",
        "company",
        "macro",
        "industry",
        parameters=(
            _parameter("category", "华尔街见闻资讯分类；留空读取默认分类"),
        ),
    ),
    _source(
        "read_rss_wallstreetcn_live",
        "/wallstreetcn/live/:category?/:score?",
        "wallstreetcn",
        "华尔街见闻",
        "实时快讯",
        "读取华尔街见闻实时快讯，可按分类和重要度筛选。",
        "market",
        "macro",
        "realtime",
        parameters=(
            _parameter(
                "category",
                "快讯分类；留空使用 global",
                route_default="global",
            ),
            _parameter(
                "score",
                "重要度：1 为全部，2 为重要快讯",
                enum=("1", "2"),
                route_default="1",
            ),
        ),
    ),
    _source(
        "read_rss_wkjyqh_futures_research",
        "/wkjyqh/research",
        "wkjyqh",
        "五矿期货",
        "期货研究报告",
        "读取五矿期货产业、宏观和期货研究报告。",
        "research",
        "industry",
        "macro",
        "futures",
    ),
    _source(
        "read_rss_xueqiu_hot_posts",
        "/xueqiu/hots",
        "xueqiu",
        "雪球",
        "热门讨论",
        "读取雪球热门帖子，仅作为公开讨论热度证据，不代表事实或投资结论。",
        "social",
        "market",
    ),
)


RSS_SOURCE_BY_TOOL = {
    source.tool_name: source for source in RSS_SOURCE_DEFINITIONS
}
RSS_SOURCE_BY_ROUTE = {
    source.route_path: source for source in RSS_SOURCE_DEFINITIONS
}

# Compatibility for data-source adapters that still use the audited source
# semantics.  This is metadata only and never selects a route for the model.
RSS_ROUTE_CAPABILITIES: dict[str, frozenset[str]] = {
    source.route_path: source.capabilities for source in RSS_SOURCE_DEFINITIONS
}


def routes_for_capability(capability: str) -> frozenset[str]:
    return frozenset(
        path
        for path, capabilities in RSS_ROUTE_CAPABILITIES.items()
        if capability in capabilities
    )


TOPIC_ROUTE_PATHS: dict[str, frozenset[str]] = {
    topic: routes_for_capability(topic)
    for topic in (
        "market",
        "company",
        "announcement",
        "research",
        "macro",
        "industry",
        "social",
    )
}


__all__ = [
    "RSS_ROUTE_CAPABILITIES",
    "RSS_SOURCE_BY_ROUTE",
    "RSS_SOURCE_BY_TOOL",
    "RSS_SOURCE_DEFINITIONS",
    "RssRouteParameter",
    "RssSourceDefinition",
    "TOPIC_ROUTE_PATHS",
    "routes_for_capability",
]
