"""Generic atomic operations over explicitly named data sources.

The Agent sees stable operations such as ``read_rss_source`` and the complete
source directory for each operation.  ``source_id`` names one preferred source
from that directory.  Daily-bar operations may record and use a declared
fallback source, while strict single-provider reads remain available for
diagnostics.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Mapping

from pydantic import Field, ValidationInfo, WithJsonSchema, field_validator

from src.tools._rss_agent import rss_options_schema
from src.tools.base import ToolSpec, model_from_object_schema, object_schema, report_tool_progress
from src.tools._trading_calendar import latest_completed_trade_day
from src.tools.kline_source_tools import _read_source as _read_kline_source
from src.tools.realtime_quote_source_tools import _read_source as _read_quote_source
from src.tools.read_rss_feed import read_rss_feed
from src.tools.rss_route_tools import (
    list_rss_cih_report_categories,
    list_rss_cls_subjects,
    list_rss_futunn_topics,
    list_rss_gelonghui_subjects,
    list_rss_nanhua_report_types,
)
from src.tools.rss_sources import RSS_SOURCE_DEFINITIONS, RssSourceDefinition
from src.tools.technical_indicator_source_tools import calculate_indicator as _calculate_indicator
from src.tools.web_source_tools import (
    read_web_auto,
    read_web_firecrawl,
    read_web_http,
    read_web_patchright,
    read_web_scrapling,
    search_web_exa,
    search_web_firecrawl_searxng,
    search_web_parallel,
)
from src.tools.websearch import websearch


def _rss_source_id(source: RssSourceDefinition) -> str:
    return source.tool_name.removeprefix("read_rss_")


_RSS_SOURCES = {_rss_source_id(source): source for source in RSS_SOURCE_DEFINITIONS}


def _rss_source_metadata(source: RssSourceDefinition) -> dict[str, Any]:
    return {
        "id": _rss_source_id(source),
        "provider": source.provider,
        "name": source.feed_name,
        "purpose": source.purpose,
        "capabilities": sorted(source.capabilities),
        "parameters": [
            {
                "name": item.name,
                "description": item.description,
                "required": item.required,
                "enum": list(item.enum),
                "default": item.route_default,
            }
            for item in source.parameters
        ],
    }


RSS_SOURCE_CATALOG = tuple(_rss_source_metadata(source) for source in RSS_SOURCE_DEFINITIONS)

# Provider schemas and runtime validation share the existing source definitions.
_RSS_PARAM_MODELS = {
    _rss_source_id(source): model_from_object_schema(
        f"{source.tool_name}_params",
        object_schema(
            {
                item.name: {
                    "type": "string",
                    "minLength": 1,
                    "description": item.description,
                    **({"enum": list(item.enum)} if item.enum else {}),
                }
                for item in source.parameters
            },
            [item.name for item in source.parameters if item.required],
        ),
    )
    for source in RSS_SOURCE_DEFINITIONS
}


def _rss_params(source: RssSourceDefinition, params: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(params, Mapping):
        raise ValueError("source_params 必须是所选来源的路径参数对象")
    model = _RSS_PARAM_MODELS[_rss_source_id(source)]
    # Keep numeric path IDs and omitted optional values supported by the API.
    # Containers, booleans and unknown keys must still reach typed validation.
    prepared = {
        key: str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else value
        for key, value in params.items()
    }
    prepared = {
        key: value for key, value in prepared.items()
        if key not in model.model_fields or value not in (None, "")
    }
    normalized = model.model_validate(prepared).model_dump(exclude_unset=True, exclude_none=True)
    last_bound = max(
        (index for index, item in enumerate(source.parameters) if item.name in normalized),
        default=-1,
    )
    for item in source.parameters[:last_bound]:
        if item.name not in normalized:
            if item.route_default is None:
                raise ValueError(
                    f"source_id={_rss_source_id(source)} 设置后续路径参数前必须先提供 {item.name}"
                )
            normalized[item.name] = item.route_default
    return normalized


def read_rss_source(
    source_id: str,
    source_params: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
    limit: int = 30,
    force: bool = False,
) -> dict[str, Any]:
    """Read one declared RSS source without discovery or provider fallback."""
    source = _RSS_SOURCES.get(str(source_id or "").strip())
    if source is None:
        raise ValueError(f"未知 RSS source_id: {source_id}")
    report_tool_progress(f"正在读取 {source.provider} · {source.feed_name}", progress=10)
    result = read_rss_feed(
        source.route_path,
        params=_rss_params(source, source_params or {}),
        options=dict(options or {}) or None,
        namespace=source.namespace,
        limit=max(1, min(int(limit), 100)),
        force=bool(force),
        validate_catalog=False,
    )
    result["source"] = {
        "id": _rss_source_id(source),
        "provider": source.provider,
        "feed_name": source.feed_name,
        "route_path": source.route_path,
    }
    result["capabilities"] = sorted(source.capabilities)
    report_tool_progress(f"{source.provider} · {source.feed_name}读取完成", progress=100)
    return result


_RSS_CATALOG_READERS: dict[str, tuple[str, Callable[..., dict[str, Any]], bool]] = {
    "cih_report_categories": ("中指研究院报告分类", list_rss_cih_report_categories, False),
    "cls_subjects": ("财联社话题目录", list_rss_cls_subjects, True),
    "futunn_topics": ("富途专题目录", list_rss_futunn_topics, True),
    "gelonghui_subjects": ("格隆汇主题目录", list_rss_gelonghui_subjects, True),
    "nanhua_report_types": ("南华期货研报分类", list_rss_nanhua_report_types, False),
}

RSS_CATALOG_SOURCE_CATALOG = tuple(
    {
        "id": source_id,
        "name": title,
        "purpose": "读取该来源允许的分类、话题或目录值；不读取资讯正文。",
        "parameters": ([{"name": "keyword", "required": False}] if supports_keyword else []),
    }
    for source_id, (title, _reader, supports_keyword) in _RSS_CATALOG_READERS.items()
)


def list_rss_source_catalog(
    source_id: str,
    keyword: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Read one source's category/topic catalog only."""
    item = _RSS_CATALOG_READERS.get(str(source_id or "").strip())
    if item is None:
        raise ValueError(f"未知 RSS 目录 source_id: {source_id}")
    _title, reader, supports_keyword = item
    if supports_keyword:
        return reader(keyword=str(keyword or "").strip(), force=bool(force))
    if str(keyword or "").strip():
        raise ValueError(f"source_id={source_id} 不支持 keyword")
    return reader(force=bool(force))


QUOTE_SOURCE_CATALOG = (
    {
        "id": "auto",
        "name": "自动实时行情",
        "purpose": "交易时段多源故障切换；非交易时段优先使用最近交易日快照",
    },
    {"id": "eastmoney_push", "name": "东方财富 Push 实时行情", "purpose": "A 股单证券实时行情"},
    {"id": "sina", "name": "新浪财经实时行情", "purpose": "A 股单证券实时行情"},
    {"id": "tencent", "name": "腾讯财经实时行情", "purpose": "A 股单证券实时行情"},
    {"id": "xueqiu", "name": "雪球实时行情", "purpose": "A 股单证券实时行情"},
)


def read_realtime_quote(symbol: str, source_id: str = "auto") -> dict[str, Any]:
    """Read one A-share quote, preferring the selected source or auto gateway."""
    normalized_source = str(source_id or "auto").strip() or "auto"
    if normalized_source not in {item["id"] for item in QUOTE_SOURCE_CATALOG}:
        raise ValueError(f"未知实时行情 source_id: {source_id}")
    if normalized_source == "auto":
        from src.tools.realtime_quote_source_tools import _read_auto_source

        return _read_auto_source(str(symbol))
    return _read_quote_source(str(symbol), normalized_source)


KLINE_SOURCE_CATALOG = (
    {"id": "eastmoney", "name": "东方财富日线（AKShare）", "purpose": "A 股前复权日线"},
    {"id": "sina", "name": "新浪财经日线（AKShare）", "purpose": "A 股前复权日线"},
    {"id": "tencent", "name": "腾讯财经日线（AKShare）", "purpose": "A 股前复权日线"},
)


def _valid_kline_source(source_id: str) -> str:
    normalized = str(source_id or "").strip()
    if normalized not in {item["id"] for item in KLINE_SOURCE_CATALOG}:
        raise ValueError(f"未知日线 source_id: {source_id}")
    return normalized


def read_recent_kline(
    source_id: str,
    symbol: str,
    count: int = 60,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Read recent completed bars, preferring the selected source.

    Agent runs enable fallback by default so one transient provider disconnect
    does not make the whole analysis fail.  Set it to false for strict
    single-provider diagnostics.
    """
    from datetime import datetime, timedelta

    bounded = max(20, min(int(count), 500))
    now = datetime.now().astimezone()
    end_date = latest_completed_trade_day(now).strftime("%Y%m%d")
    return _read_kline_source(
        symbol=str(symbol),
        source_key=_valid_kline_source(source_id),
        start_date=(now - timedelta(days=max(120, int(bounded * 1.7) + 30))).strftime("%Y%m%d"),
        end_date=end_date,
        requested_count=bounded,
        range_mode=False,
        allow_fallback=bool(allow_fallback),
    )


def read_kline_range(
    source_id: str,
    symbol: str,
    start_date: str,
    end_date: str,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Read a requested daily-bar range through the declared source gateway."""
    from src.tools.kline_source_tools import _validate_date

    start = _validate_date(start_date, "start_date")
    end = _validate_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date 不能晚于 end_date")
    return _read_kline_source(
        symbol=str(symbol),
        source_key=_valid_kline_source(source_id),
        start_date=start,
        end_date=end,
        requested_count=None,
        range_mode=True,
        allow_fallback=bool(allow_fallback),
    )


_INDICATORS = (
    "moving_average",
    "exponential_moving_average",
    "macd",
    "rsi",
    "atr",
    "bollinger_bands",
    "period_return",
)
def calculate_technical_indicator(
    source_id: str,
    indicator: str,
    symbol: str,
    count: int = 120,
    window: int | None = None,
    period: int | None = None,
    fast_period: int | None = None,
    slow_period: int | None = None,
    signal_period: int | None = None,
    standard_deviations: float | None = None,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Calculate one indicator from completed bars, preferring one source."""
    return _calculate_indicator(
        source_key=_valid_kline_source(source_id),
        indicator=str(indicator or "").strip(),
        symbol=str(symbol),
        count=int(count),
        window=window,
        period=period,
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
        standard_deviations=standard_deviations,
        allow_fallback=bool(allow_fallback),
    )


WEB_SEARCH_SOURCE_CATALOG = (
    {"id": "auto", "name": "自动网页搜索", "purpose": "按项目内置顺序自动切换公开网页搜索来源"},
    {"id": "firecrawl_searxng", "name": "Firecrawl + SearXNG", "purpose": "公开网页搜索"},
    {"id": "exa", "name": "Exa", "purpose": "公开网页搜索"},
    {"id": "parallel", "name": "Parallel", "purpose": "公开网页搜索"},
)
_WEB_SEARCHERS = {
    "firecrawl_searxng": search_web_firecrawl_searxng,
    "exa": search_web_exa,
    "parallel": search_web_parallel,
}


def search_web_source(
    source_id: str,
    query: str,
    num_results: int = 8,
    context_max_characters: int = 12_000,
    livecrawl: str = "fallback",
    search_type: str = "auto",
) -> dict[str, Any]:
    """Search a declared public-web source.

    ``auto`` delegates to the project's existing ``websearch`` fallback
    chain.  Explicit provider ids remain single-provider diagnostics.  The
    fallback is therefore visible in the returned attempts instead of being a
    hidden second tool call.
    """
    source = str(source_id or "").strip()
    if source == "auto":
        return websearch(
            query=str(query),
            num_results=max(1, min(int(num_results), 20)),
            context_max_characters=max(1_000, int(context_max_characters)),
            livecrawl=str(livecrawl),
            search_type=str(search_type),
        )
    searcher = _WEB_SEARCHERS.get(source)
    if searcher is None:
        raise ValueError(f"未知网页搜索 source_id: {source_id}")
    common = {
        "query": str(query),
        "numResults": max(1, min(int(num_results), 20)),
        "contextMaxCharacters": max(1_000, int(context_max_characters)),
    }
    if source == "exa":
        return searcher(livecrawl=str(livecrawl), type=str(search_type), **common)
    return searcher(**common)


WEB_READ_SOURCE_CATALOG = (
    {"id": "auto", "name": "自动网页读取", "purpose": "按页面类型和失败情况自动尝试多个读取器"},
    {"id": "http", "name": "标准 HTTP", "purpose": "公开 URL 直接读取"},
    {"id": "scrapling", "name": "Scrapling", "purpose": "公开 URL HTTP 渲染读取"},
    {"id": "patchright", "name": "Patchright", "purpose": "公开 URL JavaScript 浏览器渲染读取"},
    {"id": "firecrawl", "name": "Firecrawl", "purpose": "公开 URL 抓取"},
)
_WEB_READERS = {
    "auto": read_web_auto,
    "http": read_web_http,
    "scrapling": read_web_scrapling,
    "patchright": read_web_patchright,
    "firecrawl": read_web_firecrawl,
}


def read_web_source(
    source_id: str = "auto",
    url: str = "",
    format: str = "markdown",
    timeout: int | None = None,
) -> dict[str, Any]:
    """Read one public URL, using automatic fallback unless a provider is explicit."""
    reader = _WEB_READERS.get(str(source_id or "").strip())
    if reader is None:
        raise ValueError(f"未知网页读取 source_id: {source_id}")
    return reader(url=str(url), format=str(format), timeout=timeout)


def select_content_sources(source_ids: list[int]) -> dict[str, Any]:
    """Declare which current-run reference candidates need body access.

    The runtime resolves these ephemeral candidate numbers against the current
    run and performs the actual ``read_web_source`` calls. Keeping the
    selection as a small model-visible operation lets the model choose the
    relevant articles/reports without allowing it to bypass the server-owned
    URL and evidence checks.
    """
    normalized = list(dict.fromkeys(int(value) for value in source_ids))
    return {
        "success": True,
        "partial": False,
        "selected_source_ids": normalized,
        "selection_scope": "current_run_content_candidates",
        "data_time": None,
        "data_time_applicable": False,
        "freshness_unknown": True,
        "is_stale": None,
        "errors": [],
        "warnings": [],
    }


def _source_enum(catalog: tuple[dict[str, Any], ...]) -> list[str]:
    return [str(item["id"]) for item in catalog]


_SOURCE_ID = {"type": "string", "description": "从下方完整来源目录选择一个 source_id"}


class ReadRssSourceArgs(model_from_object_schema(
    "read_rss_source_args",
    object_schema(
        {
            "source_id": {**_SOURCE_ID, "enum": _source_enum(RSS_SOURCE_CATALOG)},
            "options": rss_options_schema(),
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
            "force": {"type": "boolean", "default": False},
        },
        ["source_id"],
    ),
)):
    """Validate the selected source before native tool dispatch reaches I/O."""

    # Annotated metadata survives LangChain's tool-call schema projection.
    # Keep the flat call shape and portable property-level anyOf; the field
    # validator enforces the binding to the sibling source_id at execution.
    source_params: Annotated[dict[str, str], WithJsonSchema({
        "type": "object",
        "anyOf": [
            {**model.model_json_schema(), "description": f"source_id={source_id} 的路径参数"}
            for source_id, model in _RSS_PARAM_MODELS.items()
        ],
    })] = Field(
        default_factory=dict,
        validate_default=True,
        description="所选 source_id 声明的路径参数键值；只填写该来源的参数，编号从来源目录获取。",
    )

    @field_validator("source_params", mode="before")
    @classmethod
    def validate_source_params(cls, value: Any, info: ValidationInfo) -> dict[str, Any]:
        source = _RSS_SOURCES.get(info.data.get("source_id"))
        if source is None:
            # source_id has its own enum validation error.
            return value
        return _rss_params(source, {} if value is None else value)


TOOLS = (
    ToolSpec(
        name="read_rss_source",
        description="读取一个明确指定的 RSS 数据源。先在完整 RSS 来源目录中选择 source_id；每次只访问该来源，不搜索或切换来源。",
        parameters=None,
        args_model=ReadRssSourceArgs,
        executor=read_rss_source,
        category="source_read",
        max_attempts=2,
        source_catalog=RSS_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="list_rss_source_catalog",
        description="读取一个 RSS 来源的分类、话题或目录，不读取 Feed 或正文。",
        parameters=object_schema(
            {
                "source_id": {**_SOURCE_ID, "enum": _source_enum(RSS_CATALOG_SOURCE_CATALOG)},
                "keyword": {"type": "string", "default": ""},
                "force": {"type": "boolean", "default": False},
            },
            ["source_id"],
        ),
        executor=list_rss_source_catalog,
        category="source_catalog",
        max_attempts=2,
        source_catalog=RSS_CATALOG_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="read_realtime_quote",
        description=(
            "读取一只 A 股行情；默认使用 auto，交易时段自动进行多源故障切换，"
            "非交易时段优先返回最近交易日快照并明确标注，不把快照当作当前实时成交。"
            "只有排查单一 provider 时才显式指定 eastmoney_push、sina、tencent 或 xueqiu。"
        ),
        parameters=object_schema(
            {
                "source_id": {
                    **_SOURCE_ID,
                    "enum": _source_enum(QUOTE_SOURCE_CATALOG),
                    "default": "auto",
                    "description": "通常使用 auto；仅在排查单一来源时选择其他 source_id",
                },
                "symbol": {"type": "string", "description": "A 股代码或名称"},
            },
            ["symbol"],
        ),
        executor=read_realtime_quote,
        category="source_read",
        max_attempts=1,
        source_catalog=QUOTE_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="read_recent_kline",
        description="读取一只 A 股最近已完成的前复权日线；优先使用指定来源，临时失败时可切换备用来源。",
        parameters=object_schema(
            {
                "source_id": {**_SOURCE_ID, "enum": _source_enum(KLINE_SOURCE_CATALOG)},
                "symbol": {"type": "string", "description": "A 股代码或名称"},
                "count": {"type": "integer", "minimum": 20, "maximum": 500, "default": 60},
                "allow_fallback": {
                    "type": "boolean",
                    "default": True,
                    "description": "临时失败时是否切换到备用来源；排错时可设为 false。",
                },
            },
            ["source_id", "symbol"],
        ),
        executor=read_recent_kline,
        category="source_read",
        max_attempts=1,
        source_catalog=KLINE_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="read_kline_range",
        description=(
            "读取一只 A 股日期区间前复权日线；默认在指定来源失败、空结果、未覆盖区间时"
            "按目录切换备用日线来源，并记录每次尝试。排查单一 provider 时将 allow_fallback 设为 false。"
        ),
        parameters=object_schema(
            {
                "source_id": {**_SOURCE_ID, "enum": _source_enum(KLINE_SOURCE_CATALOG)},
                "symbol": {"type": "string", "description": "A 股代码或名称"},
                "start_date": {"type": "string", "pattern": "^[0-9]{8}$"},
                "end_date": {"type": "string", "pattern": "^[0-9]{8}$"},
                "allow_fallback": {
                    "type": "boolean",
                    "default": True,
                    "description": "失败、空结果或未覆盖请求区间时是否切换备用来源；排错时可设为 false。",
                },
            },
            ["source_id", "symbol", "start_date", "end_date"],
        ),
        executor=read_kline_range,
        category="source_read",
        max_attempts=1,
        source_catalog=KLINE_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="calculate_technical_indicator",
        description="从最近已完成的前复权日线计算一个技术指标；优先使用指定来源，临时失败时可切换备用来源。",
        parameters=object_schema(
            {
                "source_id": {**_SOURCE_ID, "enum": _source_enum(KLINE_SOURCE_CATALOG)},
                "indicator": {"type": "string", "enum": list(_INDICATORS)},
                "symbol": {"type": "string", "description": "A 股代码或名称"},
                "count": {"type": "integer", "minimum": 30, "maximum": 250, "default": 120},
                "window": {"type": "integer", "minimum": 2, "maximum": 250},
                "period": {"type": "integer", "minimum": 2, "maximum": 120},
                "fast_period": {"type": "integer", "minimum": 2, "maximum": 120},
                "slow_period": {"type": "integer", "minimum": 3, "maximum": 250},
                "signal_period": {"type": "integer", "minimum": 2, "maximum": 120},
                "standard_deviations": {"type": "number", "exclusiveMinimum": 0, "maximum": 5},
                "allow_fallback": {
                    "type": "boolean",
                    "default": True,
                    "description": "临时失败时是否切换到备用来源；排错时可设为 false。",
                },
            },
            ["source_id", "indicator", "symbol"],
        ),
        executor=calculate_technical_indicator,
        category="deterministic_calculation",
        max_attempts=1,
        source_catalog=KLINE_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="search_web_source",
        description=(
            "通过公开网页搜索来源检索；source_id=auto 时复用项目内置的多来源故障切换，"
            "显式指定 provider 时只诊断该单一来源。"
        ),
        parameters=object_schema(
            {
                "source_id": {**_SOURCE_ID, "enum": _source_enum(WEB_SEARCH_SOURCE_CATALOG)},
                "query": {"type": "string", "description": "原样发送给所选来源的查询"},
                "num_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                "context_max_characters": {"type": "integer", "minimum": 1000, "maximum": 30000, "default": 12000},
                "livecrawl": {"type": "string", "enum": ["fallback", "preferred"], "default": "fallback"},
                "search_type": {"type": "string", "enum": ["auto", "fast", "deep"], "default": "auto"},
            },
            ["source_id", "query"],
        ),
        executor=search_web_source,
        category="source_search",
        max_attempts=2,
        retrieval_query_fields=("query",),
        source_catalog=WEB_SEARCH_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="read_web_source",
        description=(
            "读取一个公开 URL 并提取正文；默认 source_id=auto，"
            "会按页面类型和失败情况自动尝试 HTTP、Scrapling、Patchright、Firecrawl。"
            "如需排查或强制使用单一来源，可显式指定 source_id；"
            "优先读取前置 reference-only 工具返回的 URL。"
        ),
        parameters=object_schema(
            {
                "source_id": {
                    **_SOURCE_ID,
                    "enum": _source_enum(WEB_READ_SOURCE_CATALOG),
                    "default": "auto",
                    "description": "通常使用 auto；仅在需要指定读取方式时选择其他 source_id",
                },
                "url": {"type": "string", "description": "公开 http(s) URL"},
                "format": {"type": "string", "enum": ["markdown", "text", "html"], "default": "markdown"},
                "timeout": {"type": "integer", "minimum": 5, "maximum": 120},
            },
            ["url"],
        ),
        executor=read_web_source,
        category="source_read",
        max_attempts=2,
        source_catalog=WEB_READ_SOURCE_CATALOG,
    ),
    ToolSpec(
        name="select_content_sources",
        description=(
            "从本轮 reference-only 来源候选中选择需要核验正文的候选编号；"
            "候选编号只来自系统随后提供的‘可选参考来源候选’，不是网页读取器 source_id。"
            "服务端会根据所选编号自动调用 read_web_source，每次最多选择 4 个。"
        ),
        parameters=object_schema(
            {
                "source_ids": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "minItems": 1,
                    "maxItems": 4,
                    "description": "本轮参考来源候选编号，至少选择 1 个，最多选择 4 个",
                }
            },
            ["source_ids"],
        ),
        executor=select_content_sources,
        category="evidence_access",
        max_attempts=1,
    ),
)


__all__ = [
    "RSS_CATALOG_SOURCE_CATALOG",
    "RSS_SOURCE_CATALOG",
    "TOOLS",
    "WEB_READ_SOURCE_CATALOG",
    "WEB_SEARCH_SOURCE_CATALOG",
]
