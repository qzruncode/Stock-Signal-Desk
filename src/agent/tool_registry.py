# -*- coding: utf-8 -*-
"""Tool Registry — register data endpoints as LLM-callable tools.

Each tool is defined as an OpenAI function-calling schema + a Python executor
that directly calls the underlying data_provider / endpoint function (no HTTP detour).

Usage:
    registry = ToolRegistry()
    schemas = registry.get_all_schemas()       # → list[dict] for litellm completion(tools=...)
    result  = registry.execute("get_realtime_quotes", {"symbols": "600519"})
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolDef:
    """Single tool definition: schema + executor."""

    name: str
    description: str
    parameters: Dict[str, Any]          # JSON Schema "parameters" object
    executor: Callable[..., Any]
    category: str = "data"              # data | market | financials | sentiment | macro


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Holds all registered tools. Provides schema listing and execution."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDef] = {}
        self._register_all()

    # ---- public API ----

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Return all tool schemas in OpenAI function-calling format."""
        return [self._to_openai_schema(td) for td in self._tools.values()]

    def get_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def execute(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a tool by name with the given arguments.

        Returns the raw result (dict/list). Raises KeyError if tool not found.
        """
        td = self._tools.get(name)
        if td is None:
            raise KeyError(f"Tool not found: {name}")
        try:
            return td.executor(**arguments)
        except TypeError as e:
            logger.error(f"[ToolRegistry] executor call error for {name}: {e}")
            raise

    # ---- internal ----

    @staticmethod
    def _to_openai_schema(td: ToolDef) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }

    def _add(self, td: ToolDef) -> None:
        if td.name in self._tools:
            logger.warning(f"[ToolRegistry] duplicate tool: {td.name}, overwriting")
        self._tools[td.name] = td

    def _register_all(self) -> None:
        """Register all tools."""
        self._register_quotes_tools()
        self._register_kline_tools()
        self._register_market_status_tools()
        self._register_sector_tools()
        self._register_stock_info_tools()
        self._register_financials_tools()
        self._register_news_sentiment_tools()
        self._register_macro_tools()
        self._register_search_fallback_tools()

    def _resolve_symbol(self, value: str) -> str:
        """Resolve stock name/code into a normalized stock code when possible."""
        raw = (value or "").strip()
        if not raw:
            return raw
        try:
            from src.services.name_to_code_resolver import resolve_name_to_code

            resolved = resolve_name_to_code(raw)
            if resolved:
                return resolved
        except Exception as exc:
            logger.debug("[ToolRegistry] symbol resolve failed for %s: %s", raw, exc)
        return raw

    def _resolve_symbols_csv(self, value: str) -> list[str]:
        return [
            self._resolve_symbol(part.strip())
            for part in (value or "").split(",")
            if part.strip()
        ]


    # ===================================================================
    # 1. 行情类 (quotes)
    # ===================================================================

    def _register_quotes_tools(self) -> None:
        # --- get_realtime_quotes ---
        def _exec_get_realtime_quotes(symbols: str) -> Any:
            from api.v1.endpoints.quotes import get_realtime_quotes
            symbol_list = self._resolve_symbols_csv(symbols)
            return get_realtime_quotes(
                symbol=symbol_list[0] if symbol_list else "",
                symbols=symbol_list,
            )

        self._add(ToolDef(
            name="get_realtime_quotes",
            description="获取单只或多只股票的实时报价，包括最新价、涨跌幅、成交量、成交额、换手率、市值等",
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "string",
                        "description": "股票代码，多个用逗号分隔，如 '600519,000001'",
                    },
                },
                "required": ["symbols"],
            },
            executor=_exec_get_realtime_quotes,
            category="data",
        ))

    # ===================================================================
    # 2. K线类 (kline)
    # ===================================================================

    def _register_kline_tools(self) -> None:
        # --- get_kline ---
        def _exec_get_kline(symbol: str, count: int = 60) -> Any:
            from api.v1.endpoints.kline import get_kline
            return get_kline(symbol=self._resolve_symbol(symbol), count=count)

        self._add(ToolDef(
            name="get_kline",
            description="获取股票日线K线数据（前复权），包含日期、开盘价、收盘价、最高价、最低价、成交量、成交额、涨跌幅、换手率",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 600519",
                    },
                    "count": {
                        "type": "integer",
                        "description": "返回最近N条K线数据，默认60，通常足够判断近中期走势",
                        "default": 60,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_kline,
            category="data",
        ))

        # --- get_history_data ---
        def _exec_get_history_data(symbol: str, start_date: str, end_date: str) -> Any:
            from api.v1.endpoints.kline import get_history_data
            return get_history_data(
                symbol=self._resolve_symbol(symbol),
                start_date=start_date,
                end_date=end_date,
            )

        self._add(ToolDef(
            name="get_history_data",
            description="获取指定日期范围的日线K线数据（前复权），适用于需要查看特定时间段行情的场景",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 600519",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "起始日期，格式 YYYYMMDD，如 20250101",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYYMMDD，如 20250601",
                    },
                },
                "required": ["symbol", "start_date", "end_date"],
            },
            executor=_exec_get_history_data,
            category="data",
        ))

    # ===================================================================
    # 3. 市场状态类 (market_status)
    # ===================================================================

    def _register_market_status_tools(self) -> None:
        # --- get_market_status ---
        def _exec_get_market_status() -> Any:
            from api.v1.endpoints.market_status import get_market_status
            return get_market_status(force=False)

        self._add(ToolDef(
            name="get_market_status",
            description="获取A股市场整体状态，包括涨跌家数、涨停/跌停数、北向资金、上证指数等大盘概况数据",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            executor=_exec_get_market_status,
            category="market",
        ))

    # ===================================================================
    # 4. 板块类 (sectors)
    # ===================================================================

    def _register_sector_tools(self) -> None:
        # --- get_sector_list ---
        def _exec_get_sector_list(type: str = "industry") -> Any:
            from api.v1.endpoints.sectors import get_sector_list
            return get_sector_list(type=type)

        self._add(ToolDef(
            name="get_sector_list",
            description="获取行业或概念板块列表及涨跌情况，包括板块名称、涨跌幅、领涨股、上涨下跌家数等",
            parameters={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "板块类型: industry(行业板块) 或 concept(概念板块)",
                        "enum": ["industry", "concept"],
                        "default": "industry",
                    },
                },
                "required": [],
            },
            executor=_exec_get_sector_list,
            category="market",
        ))

    # ===================================================================
    # 5. 个股资料类 (stock_info)
    # ===================================================================

    def _register_stock_info_tools(self) -> None:
        # --- get_stock_info ---
        def _exec_get_stock_info(symbol: str) -> Any:
            from api.v1.endpoints.stock_info import get_stock_info
            return get_stock_info(symbol=self._resolve_symbol(symbol))

        self._add(ToolDef(
            name="get_stock_info",
            description="获取个股基本资料，包括公司名称、行业、上市日期、主营业务、经营范围、总股本、流通股本、市盈率、市净率等",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_stock_info,
            category="data",
        ))

    # ===================================================================
    # 6. 财务类 (financials)
    # ===================================================================

    def _register_financials_tools(self) -> None:
        # --- get_financials ---
        def _exec_get_financials(symbol: str, periods: int = 6) -> Any:
            from api.v1.endpoints.financials import get_financials
            return get_financials(symbol=self._resolve_symbol(symbol), periods=periods)

        self._add(ToolDef(
            name="get_financials",
            description="获取单只股票的核心财务指标（单季度数据），包括盈利能力(ROE、毛利率、净利率)、成长性(营收同比/环比、归母净利润同比、扣非净利润同比)、现金流(经营现金流)、营运与资产质量(应收账款、存货、合同负债、资产减值损失)、偿债能力(资产负债率、流动/速动比率)、每股指标(EPS、BPS)",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "periods": {
                        "type": "integer",
                        "description": "返回最近N个报告期数据，默认6(约1.5年)",
                        "default": 6,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_financials,
            category="financials",
        ))

        # --- get_balance_sheet ---
        def _exec_get_balance_sheet(symbol: str, periods: int = 4) -> Any:
            from api.v1.endpoints.financials import get_financial_statements
            result = get_financial_statements(symbol=self._resolve_symbol(symbol), periods=periods)
            return {
                "symbol": result.get("symbol"),
                "periods": result.get("periods"),
                "balance_sheet": result.get("balance_sheet", []),
                "source": result.get("source"),
            }

        self._add(ToolDef(
            name="get_balance_sheet",
            description="获取资产负债表数据（单季度），包括总资产、总负债、股东权益、货币资金、应收账款、存货、短期/长期借款、资产负债率、权益乘数等",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "periods": {
                        "type": "integer",
                        "description": "返回最近N个报告期数据，默认4(最近一年)",
                        "default": 4,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_balance_sheet,
            category="financials",
        ))

        # --- get_income_statement ---
        def _exec_get_income_statement(symbol: str, periods: int = 4) -> Any:
            from api.v1.endpoints.financials import get_financial_statements
            result = get_financial_statements(symbol=self._resolve_symbol(symbol), periods=periods)
            return {
                "symbol": result.get("symbol"),
                "periods": result.get("periods"),
                "income_statement": result.get("income_statement", []),
                "source": result.get("source"),
            }

        self._add(ToolDef(
            name="get_income_statement",
            description="获取利润表数据（单季度），包括营业总收入、营业总成本、营业利润、净利润、扣非净利润、基本/稀释每股收益、毛利率、净利率、各项费用明细等",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "periods": {
                        "type": "integer",
                        "description": "返回最近N个报告期数据，默认4(最近一年)",
                        "default": 4,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_income_statement,
            category="financials",
        ))

        # --- get_cashflow ---
        def _exec_get_cashflow(symbol: str, periods: int = 4) -> Any:
            from api.v1.endpoints.financials import get_financial_statements
            result = get_financial_statements(symbol=self._resolve_symbol(symbol), periods=periods)
            return {
                "symbol": result.get("symbol"),
                "periods": result.get("periods"),
                "cashflow": result.get("cashflow", []),
                "source": result.get("source"),
            }

        self._add(ToolDef(
            name="get_cashflow",
            description="获取现金流量表数据（单季度），包括经营活动/投资活动/筹资活动现金流净额、资本支出、自由现金流、经营现金流/净利润(利润含金量)等",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "periods": {
                        "type": "integer",
                        "description": "返回最近N个报告期数据，默认4(最近一年)",
                        "default": 4,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_cashflow,
            category="financials",
        ))

        # --- get_valuation_ratios ---
        def _exec_get_valuation_ratios(symbol: str, with_history: bool = True) -> Any:
            from api.v1.endpoints.financials import get_valuation_ratios
            return get_valuation_ratios(symbol=self._resolve_symbol(symbol), with_history=with_history)

        self._add(ToolDef(
            name="get_valuation_ratios",
            description="获取当前及历史估值指标，包括PE(静态/动态/TTM)、PB、PS、PCF、PEG、股息率、PE历史分位数(1/3/5年)、行业平均PE/PB等",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "with_history": {
                        "type": "boolean",
                        "description": "是否包含历史PE分位数，默认true",
                        "default": True,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_valuation_ratios,
            category="financials",
        ))

        # --- get_price_overdraft_signal ---
        def _exec_get_price_overdraft_signal(symbol: str) -> Any:
            from api.v1.endpoints.financials import get_price_overdraft_signal
            return get_price_overdraft_signal(symbol=self._resolve_symbol(symbol))

        self._add(ToolDef(
            name="get_price_overdraft_signal",
            description="获取预期校准后的股价透支判定信号，包括透支风险分、估值昂贵度、预期支撑度、触发信号、关键估值指标和判定依据",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_price_overdraft_signal,
            category="financials",
        ))

        # --- get_shareholder_structure ---
        def _exec_get_shareholder_structure(symbol: str) -> Any:
            from api.v1.endpoints.financials import get_shareholder_structure
            return get_shareholder_structure(symbol=self._resolve_symbol(symbol))

        self._add(ToolDef(
            name="get_shareholder_structure",
            description="获取股东结构数据，包括股东人数及变化、前十大股东及持股比例、机构持股占比、重要股东增减持记录、实际控制人等",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_shareholder_structure,
            category="financials",
        ))

    # ===================================================================
    # 7. 新闻舆情类 (news / sentiment)
    # ===================================================================

    def _register_news_sentiment_tools(self) -> None:
        # --- search_news ---
        def _exec_search_news(symbol: str, days: int = 30, source: str = "all") -> Any:
            from api.v1.endpoints.financials import search_news
            return search_news(symbol=self._resolve_symbol(symbol), days=days, source=source)

        self._add(ToolDef(
            name="search_news",
            description="搜索指定股票的相关新闻，返回原始条目和 analysis 结构化汇总（数据质量、来源分布、事件分布、情绪分布、关键事件证据）。数据源：项目内 RSSHub 聚合财经新闻+研报。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码、股票名称或关键词，如 600519、贵州茅台",
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询最近N天的新闻，默认30，优先聚焦近期信息",
                        "default": 30,
                    },
                    "source": {
                        "type": "string",
                        "description": "来源: all(全部) | eastmoney(东方财富) | news(新闻) | research(研报)",
                        "enum": ["all", "eastmoney", "news", "research"],
                        "default": "all",
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_search_news,
            category="sentiment",
        ))

        # --- get_sentiment ---
        def _exec_get_sentiment(symbol: str, days: int = 30) -> Any:
            from api.v1.endpoints.financials import get_sentiment
            return get_sentiment(symbol=self._resolve_symbol(symbol), days=days)

        self._add(ToolDef(
            name="get_sentiment",
            description="分析市场对某股票的情绪倾向，返回舆情分数、正/负/中性数量、趋势、关键词、逐条标注，以及 analysis 结构化汇总（覆盖度、主题/事件/重要性/证据）。数据源：项目内 RSSHub 聚合财经新闻+研报。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "days": {
                        "type": "integer",
                        "description": "分析最近N天，默认30",
                        "default": 30,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_sentiment,
            category="sentiment",
        ))

        # --- get_announcements ---
        def _exec_get_announcements(symbol: str, days: int = 30, type: str = "all") -> Any:
            from api.v1.endpoints.financials import get_announcements
            return get_announcements(symbol=self._resolve_symbol(symbol), days=days, type=type)

        self._add(ToolDef(
            name="get_announcements",
            description="获取上市公司正式公告，返回公告条目和 analysis 结构化汇总（公告类型、重要性、事件标签、风险/资本动作/治理变化等证据）。可按类型过滤。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询最近N天的公告，默认30",
                        "default": 30,
                    },
                    "type": {
                        "type": "string",
                        "description": "公告类型: all(全部) | 业绩 | 分红 | 增持 | 减持 | 高管变动",
                        "enum": ["all", "业绩", "分红", "增持", "减持", "高管变动"],
                        "default": "all",
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_announcements,
            category="sentiment",
        ))

        # --- get_risk_events ---
        def _exec_get_risk_events(symbol: str, days: int = 90) -> Any:
            from api.v1.endpoints.financials import get_risk_events
            return get_risk_events(symbol=self._resolve_symbol(symbol), days=days)

        self._add(ToolDef(
            name="get_risk_events",
            description="聚合相关新闻与公司公告中的风险线索，不做接口层最终风险打分或 LLM 重研判。返回线索清单、来源分布、主题分布和启发式标签，适合后续由模型结合上下文继续判断哪些线索真正构成实质风险。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询最近N天的风险事件，默认90",
                        "default": 90,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_risk_events,
            category="sentiment",
        ))

        # --- get_research_report ---
        def _exec_get_research_report(symbol: str, days: int = 365) -> Any:
            from api.v1.endpoints.financials import get_research_report
            return get_research_report(symbol=self._resolve_symbol(symbol), days=days)

        self._add(ToolDef(
            name="get_research_report",
            description="获取券商对公司的最新研究报告摘要，返回研报条目和 analysis 结构化汇总（覆盖度、评级/情绪/重要性/关键证据）。数据源：项目内 RSSHub 东方财富研报+ulapia。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询最近N天的研报，默认365",
                        "default": 365,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_research_report,
            category="sentiment",
        ))

        # --- get_social_sentiment ---
        def _exec_get_social_sentiment(symbol: str, days: int = 30) -> Any:
            from api.v1.endpoints.financials import get_social_sentiment
            return get_social_sentiment(symbol=self._resolve_symbol(symbol), days=days)

        self._add(ToolDef(
            name="get_social_sentiment",
            description="获取社交/讨论代理舆情，返回讨论条目、情绪比例、趋势，以及 analysis 结构化汇总（覆盖度、主题/事件/重要性/证据）。数据源：项目内 RSSHub 东方财富关键词搜索。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询最近N天，默认30",
                        "default": 30,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_get_social_sentiment,
            category="sentiment",
        ))

    # ===================================================================
    # 8. 宏观类 (macro)
    # ===================================================================

    def _register_macro_tools(self) -> None:
        # --- get_index_data ---
        def _exec_get_index_data(index_code: str = "000001", days: int = 20) -> Any:
            from api.v1.endpoints.macro import get_index_data
            return get_index_data(index_code=index_code, days=days)

        self._add(ToolDef(
            name="get_index_data",
            description="获取大盘指数行情数据，包括上证指数(000001)、深证成指(399001)、创业板指(399006)、科创50(000688)的日线OHLCV及涨跌幅",
            parameters={
                "type": "object",
                "properties": {
                    "index_code": {
                        "type": "string",
                        "description": "指数代码: 000001(上证指数) | 399001(深证成指) | 399006(创业板指) | 000688(科创50)",
                        "enum": ["000001", "399001", "399006", "000688"],
                        "default": "000001",
                    },
                    "days": {
                        "type": "integer",
                        "description": "返回最近N天数据，默认20",
                        "default": 20,
                    },
                },
                "required": [],
            },
            executor=_exec_get_index_data,
            category="macro",
        ))

        # --- get_bond_yield ---
        def _exec_get_bond_yield(country: str = "cn", term: str = "10y") -> Any:
            from api.v1.endpoints.macro import get_bond_yield
            return get_bond_yield(country=country, term=term)

        self._add(ToolDef(
            name="get_bond_yield",
            description="获取国债收益率，包括最新收益率、近一个月走势、期限利差(10y-2y)。支持中国/美国，支持2年/5年/10年/30年期限",
            parameters={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "国家: cn(中国) | us(美国)",
                        "enum": ["cn", "us"],
                        "default": "cn",
                    },
                    "term": {
                        "type": "string",
                        "description": "期限: 2y | 5y | 10y | 30y",
                        "enum": ["2y", "5y", "10y", "30y"],
                        "default": "10y",
                    },
                },
                "required": [],
            },
            executor=_exec_get_bond_yield,
            category="macro",
        ))

        # --- get_macro_indicator ---
        def _exec_get_macro_indicator(indicator: str, months: int = 12) -> Any:
            from api.v1.endpoints.macro import get_macro_indicator
            return get_macro_indicator(indicator=indicator, months=months)

        self._add(ToolDef(
            name="get_macro_indicator",
            description="获取宏观经济指标数据，包括PMI(制造业采购经理指数)、CPI(居民消费价格指数)、PPI(工业生产者出厂价格指数)、GDP(国内生产总值)、M2(货币供应量)、社融(社会融资规模)、LPR(贷款市场报价利率)",
            parameters={
                "type": "object",
                "properties": {
                    "indicator": {
                        "type": "string",
                        "description": "指标名称: PMI | CPI | PPI | GDP | M2 | 社融 | LPR",
                        "enum": ["PMI", "CPI", "PPI", "GDP", "M2", "社融", "LPR"],
                    },
                    "months": {
                        "type": "integer",
                        "description": "返回最近N个月数据，默认12",
                        "default": 12,
                    },
                },
                "required": ["indicator"],
            },
            executor=_exec_get_macro_indicator,
            category="macro",
        ))

        # --- get_sector_flow ---
        def _exec_get_sector_flow(type: str = "industry", top_n: int = 10) -> Any:
            from api.v1.endpoints.macro import get_sector_flow
            return get_sector_flow(type=type, top_n=top_n)

        self._add(ToolDef(
            name="get_sector_flow",
            description="获取行业或概念板块的主力资金净流入/流出情况，包括板块名称、涨跌幅、主力净流入、超大单净流入、大单净流入、总成交额、上涨下跌家数、领涨股等",
            parameters={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "板块类型: industry(行业) | concept(概念)",
                        "enum": ["industry", "concept"],
                        "default": "industry",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "返回前N个板块，默认10",
                        "default": 10,
                    },
                },
                "required": [],
            },
            executor=_exec_get_sector_flow,
            category="macro",
        ))

        # --- get_market_breadth ---
        def _exec_get_market_breadth() -> Any:
            from api.v1.endpoints.macro import get_market_breadth
            return get_market_breadth()

        self._add(ToolDef(
            name="get_market_breadth",
            description="获取市场宽度（赚钱效应），包括上涨/下跌/平盘家数、涨跌比、涨停/跌停数、炸板率、60日新高新低数、连涨连跌天数、总成交额等，辅助判断市场整体参与度",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            executor=_exec_get_market_breadth,
            category="macro",
        ))

    # ===================================================================
    # 9. 联网兜底搜索类 (search fallback)
    # ===================================================================

    def _register_search_fallback_tools(self) -> None:
        def _exec_search_web_news(symbol: str, max_results: int = 5) -> Any:
            from src.search_service import get_search_service
            from src.data.stock_mapping import STOCK_NAME_MAP

            code = self._resolve_symbol(symbol)
            name = STOCK_NAME_MAP.get(code, symbol)
            response = get_search_service().search_stock_news(code, name, max_results=max_results)
            return {
                "query": response.query,
                "provider": response.provider,
                "success": response.success,
                "error_message": response.error_message,
                "search_time": response.search_time,
                "results": [
                    {
                        "title": item.title,
                        "snippet": item.snippet,
                        "url": item.url,
                        "source": item.source,
                        "published_date": item.published_date,
                    }
                    for item in response.results
                ],
            }

        self._add(ToolDef(
            name="search_web_news",
            description="当站内新闻、公告或舆情工具返回空数据、失败或明显过时时，联网搜索最新相关新闻作为兜底。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回几条搜索结果，默认5",
                        "default": 5,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_search_web_news,
            category="search",
        ))

        def _exec_search_web_price_fallback(symbol: str, max_results: int = 5) -> Any:
            from src.search_service import get_search_service
            from src.data.stock_mapping import STOCK_NAME_MAP

            code = self._resolve_symbol(symbol)
            name = STOCK_NAME_MAP.get(code, symbol)
            response = get_search_service().search_stock_price_fallback(
                code,
                name,
                max_attempts=3,
                max_results=max_results,
            )
            return {
                "query": response.query,
                "provider": response.provider,
                "success": response.success,
                "error_message": response.error_message,
                "search_time": response.search_time,
                "results": [
                    {
                        "title": item.title,
                        "snippet": item.snippet,
                        "url": item.url,
                        "source": item.source,
                        "published_date": item.published_date,
                    }
                    for item in response.results
                ],
            }

        self._add(ToolDef(
            name="search_web_price_fallback",
            description="当行情、K线等结构化数据工具失败、空数据或明显过时时，联网搜索价格走势和行情报道作为兜底。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回几条搜索结果，默认5",
                        "default": 5,
                    },
                },
                "required": ["symbol"],
            },
            executor=_exec_search_web_price_fallback,
            category="search",
        ))

        def _exec_fetch_web_content(url: str) -> Any:
            from src.search_service import fetch_url_content

            return {
                "url": url,
                "content": fetch_url_content(url),
            }

        self._add(ToolDef(
            name="fetch_web_content",
            description="获取网页正文内容，用于补充阅读单条新闻、公告或研报页面。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取正文的网页 URL",
                    },
                },
                "required": ["url"],
            },
            executor=_exec_fetch_web_content,
            category="search",
        ))
