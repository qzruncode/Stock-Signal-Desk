# -*- coding: utf-8 -*-
"""Evidence-first fallback renderers for chat answers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.v1.endpoints.agent.chat_decision_renderers import (
    _build_professional_buy_decision_answer,
    _build_professional_decision_fallback,
)
from api.v1.endpoints.agent.chat_research_renderers import _build_workflow_evidence_fallback
from src.agent.domain_renderers import build_domain_candidate_answer as _build_domain_candidate_answer

def _build_verified_evidence_fallback(
    evidence: Optional[List[Dict[str, Any]]],
    *,
    professional_decision_requested: Optional[bool] = None,
) -> str:
    """Return a complete deterministic answer when final model text is empty.

    A provider can occasionally finish a streaming request without emitting a
    text delta.  Dropping the already verified tool result leaves the user with
    a blank assistant turn.  For the high-value multi-stock path we can still
    provide a compact, auditable screen directly from the batch payload without
    inventing business facts or pretending this mechanical screen is advice.
    """
    batch: Optional[Dict[str, Any]] = None

    professional_buy_answer = _build_professional_buy_decision_answer(evidence)
    if professional_buy_answer:
        return professional_buy_answer

    for item in reversed(evidence or []):
        if not isinstance(item, dict) or item.get("tool") != "prepare_market_mainline_snapshot":
            continue
        result = item.get("result")
        if not isinstance(result, dict) or result.get("success") is False or result.get("available") is not True:
            continue
        current = [row for row in result.get("current_mainlines") or [] if isinstance(row, dict)]
        candidates = [
            row
            for row in (result.get("candidate_mainlines") or result.get("future_mainlines") or [])
            if isinstance(row, dict)
        ]
        lines = [
            "## 市场主线研判",
            "",
            str(result.get("overview") or "已形成结构化市场主线快照。"),
        ]
        if candidates:
            lines.extend(
                [
                    "",
                    "### 未来一至六个月候选排序",
                    "",
                ]
            )
            for index, row in enumerate(candidates[:5], 1):
                triggers = [
                    str(value.get("description") or "").strip()
                    for value in row.get("trigger_assessments") or []
                    if isinstance(value, dict) and str(value.get("description") or "").strip()
                ]
                lines.append(
                    f"{index}. **{row.get('name') or '未命名方向'}**：" f"{row.get('reason') or '结构化理由缺失'}"
                )
                lines.append("   - 成立条件：" + ("；".join(triggers[:4]) if triggers else "尚待补充验证"))
                lines.append(
                    f"   - 阶段/期限：{row.get('stage_hint') or '待验证'} / "
                    f"{row.get('expected_horizon') or '期限未标注'}"
                )
        if current:
            lines.extend(
                [
                    "",
                    "### 当前已确认主线",
                    "",
                    "、".join(str(row.get("name") or "未命名方向") for row in current[:5]),
                ]
            )
        lines.extend(
            [
                "",
                "### 风险边界",
                "",
                "- 候选排序是条件化研判，不是对未来赢家的确定性承诺；" "触发条件未兑现或反向证据增强时应下调排序。",
                f"- 数据截至：{result.get('as_of_date') or result.get('data_time') or '未标注'}。",
            ]
        )
        return "\n".join(lines)

    # Reading a persisted report is retrieval, not a new model judgement.  If
    # the provider emits no final text after the tool succeeds, return the
    # stored report itself instead of asking the user to retry.  The LLM-facing
    # tool payload may contain only a Markdown excerpt, so reload the complete
    # local report when necessary; this path never invents or rewrites facts.
    for item in reversed(evidence or []):
        if not isinstance(item, dict) or item.get("tool") != "read_analysis_report":
            continue
        result = item.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue

        markdown = str(result.get("markdown") or "").strip()
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        record_id = str(result.get("record_id") or arguments.get("record_id") or "").strip()
        complete_report_reloaded = False
        if result.get("markdown_excerpt") and record_id:
            try:
                from src.services.history_service import HistoryService

                complete_markdown = HistoryService().get_markdown_report(record_id)
                if complete_markdown and complete_markdown.strip():
                    markdown = complete_markdown.strip()
                    complete_report_reloaded = True
            except Exception:
                logger.exception(
                    "[Agent] failed to reload complete persisted report record_id=%s",
                    record_id,
                )

        if markdown:
            try:
                expected_length = int(result.get("markdown_length") or 0)
            except (TypeError, ValueError):
                expected_length = 0
            if result.get("markdown_excerpt") and not complete_report_reloaded and len(markdown) < expected_length:
                markdown += "\n\n> 报告正文较长，当前只取得工具上下文中的节选；" "请指定报告章节继续读取。"
            return markdown

        report = result.get("report")
        if isinstance(report, dict):
            meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
            summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            strategy = report.get("strategy") if isinstance(report.get("strategy"), dict) else {}
            stock_name = meta.get("stock_name") or meta.get("stock_code") or "股票"
            stock_code = meta.get("stock_code") or ""
            title = f"# {stock_name}{f'（{stock_code}）' if stock_code else ''}正式分析报告"
            lines = [title]
            if meta.get("created_at"):
                lines.append(f"\n> 报告时间：{meta['created_at']}")
            fields = (
                ("关键结论", summary.get("analysis_summary")),
                ("操作建议", summary.get("operation_advice")),
                ("趋势判断", summary.get("trend_prediction")),
                ("情绪", summary.get("sentiment_label")),
                ("理想买入价", strategy.get("ideal_buy")),
                ("第二买入价", strategy.get("secondary_buy")),
                ("止损价", strategy.get("stop_loss")),
                ("止盈价", strategy.get("take_profit")),
            )
            for label, value in fields:
                if value not in (None, ""):
                    lines.append(f"\n## {label}\n\n{value}")
            if len(lines) > 1:
                return "".join(lines)

    workflow_answer = _build_workflow_evidence_fallback(evidence)
    if workflow_answer:
        return workflow_answer

    domain_answer = _build_domain_candidate_answer(evidence)
    if domain_answer:
        return domain_answer

    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_multi_stock_decision_evidence":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            answer = _build_professional_decision_fallback(
                result,
                decision_requested=professional_decision_requested,
            )
            market = next(
                (
                    packet.get("result")
                    for packet in evidence or []
                    if isinstance(packet, dict)
                    and packet.get("tool") == "get_market_breadth"
                    and isinstance(packet.get("result"), dict)
                    and packet["result"].get("success") is not False
                ),
                None,
            )
            if isinstance(market, dict):
                answer += (
                    "\n\n### 市场宽度\n\n"
                    f"- 上涨 **{market.get('up_count', '缺失')}** 家，下跌 **{market.get('down_count', '缺失')}** 家，"
                    f"涨跌比 **{market.get('advance_decline_ratio', '缺失')}**；"
                    f"成交额 **{market.get('total_amount', '缺失')} {market.get('total_amount_unit') or ''}**。\n"
                    f"- 数据时间：{market.get('data_time') or market.get('market_date') or '缺失'}；"
                    "市场宽度只用于判断介入环境，不改变单家公司基本面结论。"
                )
            return answer

    if professional_decision_requested is not None:
        return (
            "## 专业决策证据未完成\n\n"
            "本轮未成功取得覆盖全部公司的专业决策证据，因此停止买入判断。"
            "请重试本轮查询；在专业证据工具成功前，不输出买入、持有或卖出结论。"
        )

    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_multi_stock_snapshot":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            batch = result
            break

    if not batch or not isinstance(batch.get("items"), list):
        if not evidence:
            return (
                "当前回答服务暂时不可用，因此没有生成不可靠的内容。"
                "本轮没有执行数据查询、写入或其他外部动作；服务恢复后可以继续当前问题。"
            )
        return (
            "已取得工具证据，但模型本次没有返回最终文本。为避免编造结论，本轮不补写未经"
            "核验的判断；已取得的证据仍保留在工具卡片中。"
        )

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "缺失"

    rows: List[str] = []
    stale_names: List[str] = []
    for item in batch["items"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("symbol") or "未知")
        symbol = str(item.get("symbol") or "—")
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        financial = item.get("financial") if isinstance(item.get("financial"), dict) else {}
        technical = item.get("technical") if isinstance(item.get("technical"), dict) else {}
        pe = quote.get("pe_dynamic")
        debt = financial.get("debt_ratio_pct")
        profit = financial.get("net_profit")
        if technical.get("is_stale"):
            stale_names.append(f"{name}({symbol})")

        rows.append(
            f"| {name} ({symbol}) | {number(quote.get('price'))} / "
            f"{number(quote.get('change_pct'))}% | {number(pe)} / "
            f"{number(quote.get('pb_ratio'))} | 未生成结构化语义结论 |"
        )

    warning = ""
    if stale_names:
        warning = "\n- 技术数据陈旧：" + "、".join(stale_names) + "，未据此作判断。"
    warnings = batch.get("warnings")
    warning_text = "；".join(str(value) for value in warnings or [] if value)
    if warning_text and not warning:
        warning = f"\n- 数据警示：{warning_text}。"

    basis = str(batch.get("quote_basis") or "最新行情快照")
    data_time = str(batch.get("data_time") or "时间缺失")
    return (
        "## 多股数据快照\n\n"
        "下表只是基于本轮已核验行情、动态 PE/PB 与最新报告期财务的机械数据整理。"
        "用户未明确要求买卖判断时，不据此推导整组股票适合买入或不适合买入。\n\n"
        "| 公司/代码 | 最新价/涨跌 | 动态PE/PB | 初筛 |\n"
        "|---|---:|---:|---|\n"
        + "\n".join(rows)
        + "\n\n- 后续如需投资判断，应另外核验相关业务订单、收入、现金流及估值匹配度。"
        f"{warning}\n- 数据口径：{data_time}，{basis}；PE 为动态市盈率，不是 PE(TTM)。"
    )


def _build_realtime_quote_answer(
    evidence: Optional[List[Dict[str, Any]]],
    intent_kind: Optional[str] = None,
) -> str:
    """Render quote-only turns without letting synthesis invent market context."""
    if intent_kind != "market_snapshot":
        return ""
    packets = [item for item in evidence or [] if isinstance(item, dict)]
    quote_only_support_tools = {
        "get_realtime_quotes",
        "get_market_status",
        "get_market_breadth",
    }
    if not packets or any(item.get("tool") not in quote_only_support_tools for item in packets):
        return ""
    results = [
        item.get("result")
        for item in packets
        if item.get("tool") == "get_realtime_quotes" and isinstance(item.get("result"), dict)
    ]
    if not results:
        return ""
    result = results[-1]
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    if not items:
        return ""

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    rows: List[str] = []
    for item in items:
        symbol = str(item.get("symbol") or item.get("code") or "")
        name = str(item.get("name") or symbol)
        pct = number(item.get("pct_chg", item.get("change_pct")))
        rows.append(
            f"| {name} ({symbol}) | {number(item.get('price'))} 元 | {pct}% | "
            f"{number(item.get('high'))} / {number(item.get('low'))} | "
            f"{number(item.get('amount'), 0)} 元 |"
        )

    quote_mode = str(result.get("quote_mode") or "")
    is_live = quote_mode == "live" or result.get("is_trading_session") is True
    mode_label = str(result.get("quote_mode_label") or "").strip() or (
        "交易时段实时行情" if is_live else "最近交易日行情快照"
    )
    data_time = str(result.get("data_time") or "时间未知").replace("T", " ")
    sources = result.get("source") or []
    source_text = "、".join(map(str, sources)) if isinstance(sources, list) else str(sources)
    stale_note = "；数据已陈旧，请勿据此判断当前价格" if result.get("is_stale") is True else ""
    return (
        "## 最新行情\n\n"
        "| 股票 | 最新价 | 涨跌幅 | 最高 / 最低 | 成交额 |\n"
        "|---|---:|---:|---:|---:|\n"
        + "\n".join(rows)
        + f"\n\n- 数据时间：{data_time}\n"
        + f"- 行情口径：{mode_label}{stale_note}\n"
        + f"- 数据来源：{source_text or '行情工具返回来源'}\n"
        + ("" if is_live else "- 当前为非交易时段；以上不是当前时刻的实时成交，也不等同于收盘价。\n")
    )


def _build_quantitative_screen_answer(evidence: Optional[List[Dict[str, Any]]]) -> str:
    """Render a validated screen verbatim without changing its conditions."""
    results = [
        item.get("result")
        for item in evidence or []
        if isinstance(item, dict)
        and item.get("tool") == "screen_atr_volatility_stocks"
        and isinstance(item.get("result"), dict)
    ]
    if not results:
        return "## 筛选未完成\n\n量化筛选工具没有返回结构化结果，因此本轮不输出股票结论。"
    result = results[-1]
    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
    coverage_parts = [f"本轮股票范围 {coverage.get('universe', '—')} 只"]
    if coverage.get("history_preexcluded") is not None:
        coverage_parts.append(f"上市历史确定不足预排除 {coverage.get('history_preexcluded')} 只")
    if coverage.get("financial_covered") is not None:
        coverage_parts.append(f"必需财务字段覆盖 {coverage.get('financial_covered')} 只")
    if coverage.get("financial_eligible") is not None:
        coverage_parts.append(f"财务条件后候选 {coverage.get('financial_eligible')} 只")
    if coverage.get("fresh_kline") is not None:
        coverage_parts.append(f"取得行情 {coverage.get('fresh_kline')} 只")
    fallback_count = int(coverage.get("financial_fallback_count") or 0)
    cache_count = int(coverage.get("financial_cache_count") or 0)
    if cache_count:
        coverage_parts.append(f"本日财务快照接管 {cache_count} 只")
    if fallback_count:
        coverage_parts.append(f"财务补源 {fallback_count} 只")
    coverage_text = "；".join(coverage_parts) + "。"
    if result.get("success") is not True:
        errors = [str(item) for item in result.get("errors") or [] if item]
        failed = [str(item) for item in result.get("failed_symbols") or [] if item]
        detail = "\n".join(f"- {item}" for item in errors) or "- 工具没有提供失败原因。"
        failed_text = ("\n- 失败样例：" + "；".join(failed[:10])) if failed else ""
        stage = str(result.get("failure_stage") or "unknown")
        return (
            "## 筛选未完成\n\n"
            "**本轮不输出任何股票结论。** 条件、数据刷新或全市场覆盖没有通过硬校验。\n\n"
            f"- 失败阶段：`{stage}`\n{detail}{failed_text}\n\n- 覆盖校验：{coverage_text}"
        )

    screen_spec = result.get("screen_spec")
    columns = result.get("columns")
    applied_rules = result.get("applied_rules")
    if (
        not isinstance(screen_spec, dict)
        or not isinstance(columns, list)
        or not columns
        or not isinstance(applied_rules, list)
        or not applied_rules
        or coverage.get("complete") is not True
    ):
        return (
            "## 筛选未完成\n\n**本轮不输出任何股票结论。** 工具虽返回成功，"
            "但缺少实际执行规格、动态列定义、规则回显或完整覆盖证明。"
        )

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    def format_value(value: Any, format_name: str) -> str:
        if value is None:
            return "—"
        if format_name == "currency_yuan":
            try:
                return f"{float(value) / 100_000_000:,.2f}亿"
            except (TypeError, ValueError):
                return "—"
        if format_name == "percent":
            return number(value) + "%"
        if format_name == "integer":
            try:
                return f"{int(value):,}"
            except (TypeError, ValueError):
                return "—"
        return str(value)

    normalized_columns: List[Dict[str, str]] = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        field = str(column.get("field") or "").strip()
        label = str(column.get("label") or "").strip()
        if field and label:
            normalized_columns.append(
                {
                    "field": field,
                    "label": label,
                    "format": str(column.get("format") or "text"),
                }
            )
    if not normalized_columns:
        return "## 筛选未完成\n\n工具返回的结果列合同无效，本轮不输出股票结论。"

    preview_items = result.get("items") or []
    for item in preview_items:
        if not isinstance(item, dict) or any(column["field"] not in item for column in normalized_columns):
            return "## 筛选未完成\n\n工具返回的预览行缺少请求字段，" "结果合同不完整，因此本轮不输出股票结论。"

    rows: List[str] = []
    for item in preview_items:
        cells = [format_value(item.get(column["field"]), column["format"]) for column in normalized_columns]
        rows.append("| " + " | ".join(cells) + " |")
    total = int(result.get("total") or 0)
    preview_limit = int(screen_spec.get("preview_limit") or 10)
    download_url = str(result.get("download_url") or "").strip()
    if total > preview_limit and not download_url:
        return (
            "## 筛选未完成\n\n完整结果超过页面预览上限，但工具没有生成下载文件；"
            "为避免交付不完整名单，本轮不输出股票结论。"
        )
    if rows:
        table = "| " + " | ".join(column["label"] for column in normalized_columns) + " |\n" "| " + " | ".join(
            "---" for _ in normalized_columns
        ) + " |\n" + "\n".join(rows)
    else:
        table = "完整执行本轮全部条件后，合格股票为 **0 只**。"
    sort_spec = screen_spec.get("sort") if isinstance(screen_spec.get("sort"), dict) else {}
    sort_text = f"{sort_spec.get('field', '工具指定字段')} {sort_spec.get('order', '—')}"
    download = f"\n\n[下载完整 {total} 只筛选结果（CSV）]({download_url})" if download_url else ""
    rules_text = "\n".join(f"- {item}" for item in applied_rules if str(item).strip())
    fingerprint = str(result.get("spec_fingerprint") or "—")
    data_times = result.get("data_times") if isinstance(result.get("data_times"), dict) else {}
    kline_time = str(data_times.get("kline_expected_date") or "").strip()
    financial_period = str(
        data_times.get("financial_report_period") or result.get("financial_report_period") or ""
    ).strip()
    time_parts: List[str] = []
    if kline_time:
        time_parts.append(f"行情刷新基准日 {kline_time}")
    if financial_period:
        time_parts.append(f"主财务报告期 {financial_period}")
    if not time_parts:
        time_parts.append(f"数据日期 {result.get('data_time', '—')}")
    warnings = [str(item) for item in result.get("warnings") or [] if item]
    warnings_text = ""
    if warnings:
        warnings_text = "\n- 数据源切换：" + "；".join(warnings)
    saved_group = result.get("saved_group") if isinstance(result.get("saved_group"), dict) else None
    saved_group_text = ""
    if saved_group:
        saved_group_text = (
            f"\n\n### 已保存到自选分组\n\n"
            f"完整筛选结果已保存为 **{saved_group.get('name') or '未命名分组'}**，"
            f"共 {saved_group.get('count', total)} 只股票。"
        )
    return (
        f"## 筛选结论\n\n共 **{total} 只**股票满足本轮完整规格，排序为 `{sort_text}`。"
        + (f"下表展示前{preview_limit}只。\n\n" if total > preview_limit else "\n\n")
        + table
        + download
        + "\n\n### 本轮实际执行规格\n\n"
        + rules_text
        + f"\n- 规格指纹：`{fingerprint}`"
        + "\n\n### 数据与覆盖\n\n"
        + f"- 数据日期：{'；'.join(time_parts)}。\n"
        + f"- 覆盖：{coverage_text}\n"
        + f"- 来源：{result.get('source', '工具返回来源')}。"
        + warnings_text
        + saved_group_text
    )



__all__ = ["_build_verified_evidence_fallback", "_build_realtime_quote_answer", "_build_quantitative_screen_answer"]
