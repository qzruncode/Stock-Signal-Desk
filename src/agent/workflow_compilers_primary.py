"""Workflow call compilers for core market and security tasks."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from src.agent.result_contracts import InvestmentThesisContext
from src.agent.task_workflows import (
    ConfirmationState,
    ResolvedTask,
    WorkflowCall,
    WorkflowCompileError,
    WorkflowExecutionGuard,
)


def call_workflow(
    task: ResolvedTask,
    step_id: str,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    depends_on: Sequence[str] = (),
    after: Sequence[str] = (),
    bind_results: Mapping[str, str] | None = None,
    execute_when: WorkflowExecutionGuard | None = None,
) -> WorkflowCall:
    return WorkflowCall(
        task_id=task.task_id,
        step_id=step_id,
        tool_name=tool_name,
        arguments=dict(arguments or {}),
        depends_on_steps=tuple(depends_on),
        after_steps=tuple(after),
        result_bindings=tuple((bind_results or {}).items()),
        execution_guard=execute_when,
    )


def select_parameters(task: ResolvedTask, allowed: Iterable[str]) -> dict[str, Any]:
    allowed_set = set(allowed)
    return {key: value for key, value in task.parameters.items() if key in allowed_set}


def symbols_csv(task: ResolvedTask) -> str:
    if not task.symbols:
        raise WorkflowCompileError(f"{task.kind.value} requires at least one resolved entity")
    return ",".join(task.symbols)


def per_symbol(
    task: ResolvedTask,
    tool_names: Sequence[str],
    argument_keys: Mapping[str, Iterable[str]] | None = None,
    *,
    max_symbols: int | None = None,
) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError(f"{task.kind.value} requires at least one resolved entity")
    if max_symbols is not None and len(symbols) > max_symbols:
        raise WorkflowCompileError(
            f"{task.kind.value} supports at most {max_symbols} entities per standard task; split the task"
        )
    calls: list[WorkflowCall] = []
    for symbol_index, symbol in enumerate(symbols, 1):
        for tool_index, tool_name in enumerate(tool_names, 1):
            args = {"symbol": symbol}
            args.update(select_parameters(task, (argument_keys or {}).get(tool_name, ())))
            calls.append(call_workflow(task, f"{tool_name}_{symbol_index}_{tool_index}", tool_name, args))
    return calls


def compile_no_tools(task: ResolvedTask) -> list[WorkflowCall]:
    return []


def compile_security_lookup(task: ResolvedTask) -> list[WorkflowCall]:
    return [
        call_workflow(
            task,
            "search_security",
            "search_stocks",
            select_parameters(task, {"query", "exchange", "board", "sector", "limit"}),
        )
    ]


def compile_realtime_quote(task: ResolvedTask) -> list[WorkflowCall]:
    return [call_workflow(task, "quotes", "get_realtime_quotes", {"symbols": symbols_csv(task)})]


def compile_price_history(task: ResolvedTask) -> list[WorkflowCall]:
    has_range = bool(task.parameters.get("start_date") and task.parameters.get("end_date"))
    if has_range:
        return per_symbol(
            task,
            ["get_history_data"],
            {"get_history_data": {"start_date", "end_date", "use_cache"}},
            max_symbols=8,
        )
    return per_symbol(task, ["get_kline"], {"get_kline": {"count", "use_cache"}}, max_symbols=8)


def compile_technical(task: ResolvedTask) -> list[WorkflowCall]:
    calls = per_symbol(
        task,
        ["get_technical_indicators"],
        {"get_technical_indicators": {"count"}},
        max_symbols=8,
    )
    return [
        WorkflowCall(
            task_id=call.task_id,
            step_id=call.step_id,
            tool_name=call.tool_name,
            arguments={**call.arguments, "include_structured": True},
            depends_on_steps=call.depends_on_steps,
            after_steps=call.after_steps,
            result_bindings=call.result_bindings,
            execution_guard=call.execution_guard,
        )
        for call in calls
    ]


def compile_fundamental(task: ResolvedTask) -> list[WorkflowCall]:
    if len(task.symbols) > 2:
        return [call_workflow(task, "multi_fundamental_snapshot", "get_multi_stock_snapshot", {"symbols": symbols_csv(task)})]
    calls = per_symbol(
        task,
        ["get_stock_info", "get_financials", "get_business_segments", "get_shareholder_structure"],
        {
            "get_financials": {"periods"},
            "get_business_segments": {"category", "periods"},
        },
        max_symbols=2,
    )
    calls.extend(
        call_workflow(
            task,
            f"structured_company_{index}",
            "get_company_structured_evidence",
            {
                "symbol": symbol,
                "scope": "risk_and_catalyst",
                "days": 730,
                "report_period_count": 4,
            },
        )
        for index, symbol in enumerate(task.symbols, 1)
    )
    return calls


def compile_valuation(task: ResolvedTask) -> list[WorkflowCall]:
    if len(task.symbols) > 2:
        return [call_workflow(task, "multi_valuation_snapshot", "get_multi_stock_snapshot", {"symbols": symbols_csv(task)})]
    return per_symbol(
        task,
        ["get_valuation_ratios", "get_consensus_estimates", "get_peer_comparison"],
        {
            "get_valuation_ratios": {"with_history"},
            "get_consensus_estimates": {"metric"},
            "get_peer_comparison": {"dimension"},
        },
        max_symbols=2,
    )


def compile_statements(task: ResolvedTask) -> list[WorkflowCall]:
    return per_symbol(
        task,
        ["get_balance_sheet", "get_income_statement", "get_cashflow"],
        {
            "get_balance_sheet": {"periods"},
            "get_income_statement": {"periods"},
            "get_cashflow": {"periods"},
        },
        max_symbols=2,
    )


def compile_news(task: ResolvedTask) -> list[WorkflowCall]:
    if not task.symbols or len(task.symbols) > 2:
        query = str(task.parameters.get("query") or " ".join(task.symbols)).strip()
        query = query or task.candidate.objective
        topic = str(task.parameters.get("topic") or "").strip()
        if not topic:
            raise WorkflowCompileError(
                "news_analysis requires a Planner-supplied topic for thematic or multi-company news"
            )
        subjects = task.parameters.get("subjects") or list(task.symbols)
        args = {
            "query": query,
            "topic": topic,
            **({"subjects": subjects} if subjects else {}),
            **select_parameters(task, {"days", "limit", "include_content", "fallback_to_web"}),
        }
        return [call_workflow(task, "multi_company_news", "search_financial_news", args)]
    calls = per_symbol(task, ["search_news"], {"search_news": {"days", "limit", "use_cache"}}, max_symbols=2)
    calls.extend(per_symbol(task, ["get_announcements"], {"get_announcements": {"days", "limit"}}, max_symbols=2))
    return calls


def compile_announcements(task: ResolvedTask) -> list[WorkflowCall]:
    return per_symbol(
        task,
        ["get_announcements"],
        {"get_announcements": {"days", "limit"}},
        max_symbols=8,
    )


def compile_risk(task: ResolvedTask) -> list[WorkflowCall]:
    calls = per_symbol(
        task,
        ["get_announcements", "get_risk_events"],
        {
            "get_announcements": {"days", "limit"},
            "get_risk_events": {"days", "limit"},
        },
        max_symbols=4,
    )
    return [
        WorkflowCall(
            task_id=call.task_id,
            step_id=call.step_id,
            tool_name=call.tool_name,
            arguments=(
                {**call.arguments, "include_structured": True}
                if call.tool_name == "get_risk_events"
                else call.arguments
            ),
            depends_on_steps=call.depends_on_steps,
            after_steps=call.after_steps,
            result_bindings=call.result_bindings,
            execution_guard=call.execution_guard,
        )
        for call in calls
    ]


def compile_regulatory(task: ResolvedTask) -> list[WorkflowCall]:
    return [
        call_workflow(
            task,
            "regulatory_updates",
            "get_regulatory_updates",
            select_parameters(
                task,
                {
                    "keyword",
                    "event_type",
                    "market",
                    "days",
                    "limit",
                    "include_content",
                    "fallback_to_web",
                    "project_type",
                    "project_stage",
                    "project_status",
                },
            ),
        )
    ]


def compile_reports(task: ResolvedTask) -> list[WorkflowCall]:
    return per_symbol(
        task,
        ["get_research_report"],
        {"get_research_report": {"days", "limit"}},
        max_symbols=8,
    )


def compile_sentiment(task: ResolvedTask) -> list[WorkflowCall]:
    return per_symbol(
        task,
        ["get_social_sentiment"],
        {"get_social_sentiment": {"days", "limit", "max_pages"}},
        max_symbols=8,
    )


def compile_comparison(task: ResolvedTask) -> list[WorkflowCall]:
    calls = [call_workflow(task, "comparison_snapshot", "get_multi_stock_snapshot", {"symbols": symbols_csv(task)})]
    if len(task.symbols) <= 7 and bool(task.parameters.get("include_peers", True)):
        calls.extend(
            per_symbol(
                task,
                ["get_peer_comparison"],
                {"get_peer_comparison": {"dimension"}},
                max_symbols=7,
            )
        )
    return calls


def compile_decision_packet(task: ResolvedTask) -> list[WorkflowCall]:
    if len(task.symbols) > 8:
        raise WorkflowCompileError(
            "stock_deep_research supports at most 8 entities per task; narrow the research scope"
        )
    return [
        call_workflow(
            task,
            "decision_evidence",
            "get_multi_stock_decision_evidence",
            {"symbols": symbols_csv(task), "thesis": str(task.parameters.get("thesis") or "")},
        )
    ]


def compile_catalyst_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("catalyst_analysis requires at least one resolved entity")
    if len(symbols) > 8:
        raise WorkflowCompileError("catalyst_analysis supports at most 8 entities per standard task; narrow the scope")
    return [
        call_workflow(
            task,
            f"catalyst_{index}",
            "analyze_stock_catalysts",
            {"symbols": symbol},
        )
        for index, symbol in enumerate(symbols, 1)
    ]


def compile_professional_buy_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    from src.services.buy_criteria.mainline_policy import (
        normalize_mainline_strategy,
    )

    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("investment_decision requires at least one resolved entity")
    if len(symbols) > 300:
        raise WorkflowCompileError("investment_decision supports at most 300 entities; narrow the collection first")
    thesis = str(task.parameters.get("thesis") or "").strip()
    raw_thesis_context = task.parameters.get("thesis_context")
    thesis_context = (
        InvestmentThesisContext.model_validate(raw_thesis_context).model_dump()
        if raw_thesis_context is not None
        else None
    )
    common_arguments = {
        "thesis": thesis,
        "mainline_strategy": normalize_mainline_strategy(task.parameters.get("mainline_strategy")).value,
        **({"thesis_context": thesis_context} if thesis_context is not None else {}),
    }
    snapshot_step = "market_mainline_snapshot"
    mainline_gate_step = "market_mainline_gate"
    return [
        call_workflow(
            task,
            snapshot_step,
            "prepare_market_mainline_snapshot",
            {},
        ),
        call_workflow(
            task,
            mainline_gate_step,
            "evaluate_market_mainline_gate",
            common_arguments,
            depends_on=(snapshot_step,),
            bind_results={
                "market_mainline_snapshot": snapshot_step,
            },
        ),
        *[
            call_workflow(
                task,
                f"professional_buy_{index:03d}",
                "evaluate_multi_stock_buy_criteria",
                {
                    "symbols": symbol,
                    **common_arguments,
                },
                depends_on=(snapshot_step, mainline_gate_step),
                bind_results={
                    "market_mainline_snapshot": snapshot_step,
                    "market_mainline_assessment": mainline_gate_step,
                    "market_mainline_model_error": mainline_gate_step,
                },
                execute_when=WorkflowExecutionGuard(
                    source_step=mainline_gate_step,
                    result_path=("market_mainline_assessment", "status"),
                    allowed_values=frozenset({"pass"}),
                    blocked_reason=(
                        "共享市场主线第一关未通过，程序已按八维顺序" "直接形成逐股终态，后续七维不得执行。"
                    ),
                ),
            )
            for index, symbol in enumerate(symbols, 1)
        ],
    ]


def compile_market(task: ResolvedTask) -> list[WorkflowCall]:
    calls = [
        call_workflow(task, "market_status", "get_market_status"),
        call_workflow(task, "market_breadth", "get_market_breadth"),
        call_workflow(task, "market_regime", "get_market_regime"),
    ]
    if bool(task.parameters.get("include_index", True)):
        calls.append(call_workflow(task, "index", "get_index_data", select_parameters(task, {"index_code", "days"})))
    return calls


def compile_market_mainline_research(
    task: ResolvedTask,
) -> list[WorkflowCall]:
    return [
        call_workflow(
            task,
            "market_mainline_snapshot",
            "prepare_market_mainline_snapshot",
            {"force": False},
        )
    ]


def compile_sector(task: ResolvedTask) -> list[WorkflowCall]:
    base = select_parameters(task, {"type"})
    flow = select_parameters(task, {"type", "period", "top_n"})
    calls = [
        call_workflow(task, "sector_list", "get_sector_list", base),
        call_workflow(task, "sector_flow", "get_sector_flow", flow),
    ]
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if query:
        calls.append(
            call_workflow(
                task,
                "sector_news",
                "search_financial_news",
                {
                    "query": query,
                    "topic": "industry",
                    **select_parameters(task, {"subjects", "days", "limit", "include_content"}),
                },
            )
        )
    return calls


def compile_capital_flow(task: ResolvedTask) -> list[WorkflowCall]:
    return per_symbol(
        task,
        ["get_stock_capital_flow"],
        {"get_stock_capital_flow": {"days"}},
        max_symbols=8,
    )


def compile_macro(task: ResolvedTask) -> list[WorkflowCall]:
    indicators = task.parameters.get("indicators") or []
    if isinstance(indicators, str):
        indicators = [indicators]
    calls = [
        call_workflow(
            task,
            f"macro_{index}",
            "get_macro_indicator",
            {
                "indicator": indicator,
                **select_parameters(task, {"periods"}),
            },
        )
        for index, indicator in enumerate(indicators[:5], 1)
    ]
    if bool(task.parameters.get("include_bond_yield")):
        calls.append(call_workflow(task, "bond_yield", "get_bond_yield", select_parameters(task, {"country", "term", "days"})))
    if bool(task.parameters.get("include_monetary_operations")):
        calls.append(
            call_workflow(
                task,
                "monetary_operations",
                "get_monetary_policy_operations",
                select_parameters(task, {"days", "instrument", "limit", "include_content", "fallback_to_web"}),
            )
        )
    query = str(task.parameters.get("query") or "").strip()
    if query and len(calls) < 8:
        subjects = task.parameters.get("subjects")
        if not isinstance(subjects, list) or not subjects:
            raise WorkflowCompileError("macro research requires Planner-supplied semantic subjects")
        calls.append(
            call_workflow(
                task,
                "macro_research",
                "search_research_library",
                {
                    "query": query,
                    "category": "macro",
                    "subjects": subjects,
                    **select_parameters(task, {"days", "limit", "include_content", "fallback_to_web"}),
                },
            )
        )
    if not calls:
        raise WorkflowCompileError("macro_analysis requires indicators or an explicitly requested macro source")
    return calls


def compile_industry(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    if not isinstance(domains, list) or not domains:
        raise WorkflowCompileError("industry_research requires a non-empty domains array")
    labels = [
        str(domain if isinstance(domain, str) else domain.get("label") if isinstance(domain, Mapping) else "").strip()
        for domain in domains
    ]
    if not all(labels):
        raise WorkflowCompileError("industry_research domains must contain semantic topic labels")
    return [call_workflow(task, "domain_board_catalog", "get_domain_board_catalog", {})]


def compile_industry_index_research(task: ResolvedTask) -> list[WorkflowCall]:
    arguments = select_parameters(
        task,
        {"query", "index_type", "index_code", "include_components", "max_matches", "history_points"},
    )
    if not (arguments.get("query") or arguments.get("index_code")):
        raise WorkflowCompileError("industry_index_research requires query or index_code")
    return [
        call_workflow(
            task,
            "industry_index_context",
            "get_industry_index_context",
            arguments,
        )
    ]


__all__ = [
    "compile_no_tools",
    "compile_security_lookup",
    "compile_realtime_quote",
    "compile_price_history",
    "compile_technical",
    "compile_fundamental",
    "compile_valuation",
    "compile_statements",
    "compile_news",
    "compile_announcements",
    "compile_risk",
    "compile_regulatory",
    "compile_reports",
    "compile_sentiment",
    "compile_comparison",
    "compile_decision_packet",
    "compile_catalyst_analysis",
    "compile_professional_buy_analysis",
    "compile_market",
    "compile_market_mainline_research",
    "compile_sector",
    "compile_capital_flow",
    "compile_macro",
    "compile_industry",
    "compile_industry_index_research",
]
