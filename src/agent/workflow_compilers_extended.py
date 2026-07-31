"""Workflow call compilers for theme, collection, and management tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping

from src.agent.result_contracts import (
    CollectionFinancialFilterSpec,
    DomainBoardQuerySpec,
    ThemeEvidenceContext,
)
from src.agent.task_workflows import (
    ConfirmationState,
    ResolvedTask,
    WorkflowCall,
    WorkflowCompileError,
)
from src.agent.workflow_compilers_primary import (
    call_workflow,
    per_symbol,
    select_parameters,
    symbols_csv,
)


def compile_theme_discovery(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    if not isinstance(domains, list) or not domains:
        raise WorkflowCompileError("theme_stock_discovery requires a non-empty domains array")
    try:
        domain_specs = [DomainBoardQuerySpec.model_validate(domain).model_dump(exclude_none=True) for domain in domains]
    except Exception as exc:
        raise WorkflowCompileError(f"theme_stock_discovery requires resolved domain-board objects: {exc}") from exc
    args = {"domains": domain_specs}
    return [call_workflow(task, "domain_candidates", "get_domain_stock_candidates", args)]


def compile_theme_evidence(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    candidate_scope = str(task.parameters.get("candidate_scope") or "").strip()
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if not isinstance(domains, list) or not domains:
        raise WorkflowCompileError("theme_business_evidence requires resolved domains")
    if candidate_scope == "candidate_collection" and not task.symbols:
        raise WorkflowCompileError("theme_business_evidence requires the upstream candidate collection")
    subjects: list[str] = []
    for domain in domains:
        label = domain if isinstance(domain, str) else domain.get("label") if isinstance(domain, Mapping) else ""
        text = str(label or "").strip()
        if text and text not in subjects:
            subjects.append(text)
    if not subjects:
        raise WorkflowCompileError("theme_business_evidence requires semantic domain labels")
    try:
        evidence_context = ThemeEvidenceContext.model_validate(task.parameters.get("evidence_context"))
    except Exception as exc:
        raise WorkflowCompileError(f"theme_business_evidence requires a parent-theme evidence context: {exc}") from exc
    if candidate_scope == "candidate_collection":
        names = dict(task.entity_names)
        days = max(30, min(int(task.parameters.get("days") or 365), 730))
        return [
            call_workflow(
                task,
                f"company_{index:04d}_{symbol}",
                "get_company_theme_evidence",
                {
                    "symbol": symbol,
                    "company_name": names.get(symbol, symbol),
                    "target_topics": evidence_context.target_topics,
                    "domains": subjects,
                    "objective": task.candidate.objective,
                    "days": days,
                },
            )
            for index, symbol in enumerate(task.symbols, 1)
        ]

    common = select_parameters(task, {"days", "limit", "include_content", "fallback_to_web"})
    common.setdefault("days", 365)
    common.setdefault("limit", 12)
    common["include_content"] = True
    common["fallback_to_web"] = True
    calls: list[WorkflowCall] = []
    for index, subject in enumerate(subjects, 1):
        retrieval_subjects = list(
            dict.fromkeys(
                [
                    *evidence_context.target_topics,
                    subject,
                ]
            )
        )
        parent_topic = "、".join(evidence_context.target_topics)
        subject_query = f"{parent_topic}中的{subject}：{query}"
        calls.extend(
            [
                call_workflow(
                    task,
                    f"business_news_{index}",
                    "search_financial_news",
                    {
                        "query": subject_query,
                        "topic": "industry",
                        "subjects": retrieval_subjects,
                        **common,
                    },
                ),
                call_workflow(
                    task,
                    f"business_research_{index}",
                    "search_research_library",
                    {
                        "query": subject_query,
                        "category": "industry",
                        "subjects": retrieval_subjects,
                        **common,
                    },
                ),
            ]
        )
    return calls


def compile_screening(task: ResolvedTask) -> list[WorkflowCall]:
    screen_spec = task.parameters.get("screen_spec")
    if not isinstance(screen_spec, dict):
        raise WorkflowCompileError("stock_screening requires a complete screen_spec")
    args = {
        "screen_spec": screen_spec,
        "refresh_if_stale": bool(task.parameters.get("refresh_if_stale", True)),
    }
    if task.parameters.get("save_group_name"):
        args["save_group_name"] = task.parameters["save_group_name"]
    return [call_workflow(task, "screen", "screen_atr_volatility_stocks", args)]


def compile_collection_filter(task: ResolvedTask) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("collection_financial_filter requires the previous company collection")
    try:
        filter_spec = CollectionFinancialFilterSpec.model_validate(task.parameters)
    except Exception as exc:
        raise WorkflowCompileError(f"collection_financial_filter has an invalid semantic contract: {exc}") from exc
    calls: list[WorkflowCall] = []
    for condition_index, condition in enumerate(filter_spec.conditions, 1):
        common_arguments: dict[str, Any] = {
            "metric": condition.metric,
            "period_basis": condition.period_basis,
        }
        if condition.fiscal_year is not None:
            common_arguments["fiscal_year"] = condition.fiscal_year
        calls.extend(
            call_workflow(
                task,
                f"condition_{condition_index}_batch_{index // 24 + 1}",
                "get_multi_stock_financials",
                {
                    "symbols": ",".join(symbols[index : index + 24]),
                    **common_arguments,
                },
            )
            for index in range(0, len(symbols), 24)
        )
    if len(symbols) > 300 or len(calls) > 104:
        raise WorkflowCompileError("collection_financial_filter supports at most 300 companies per turn")
    return calls


def compile_watchlist_query(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    if domains:
        return [
            call_workflow(
                task,
                "filter_watchlist",
                "filter_watchlist_by_theme",
                {
                    "domains": domains,
                    **select_parameters(task, {"group"}),
                },
            )
        ]
    return [call_workflow(task, "list_watchlist", "manage_watchlist", {"action": "list"})]


def compile_watchlist_mutation(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "")
    return [
        call_workflow(
            task,
            "mutate_watchlist",
            "manage_watchlist",
            {
                "action": action,
                "symbols": symbols_csv(task),
            },
        )
    ]


def compile_group(task: ResolvedTask) -> list[WorkflowCall]:
    args = select_parameters(task, {"action", "group", "new_name"})
    if args.get("action") in {"add", "remove"} and not task.symbols:
        raise WorkflowCompileError("watchlist group add/remove requires resolved entities")
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    if task.symbols:
        args["symbols"] = symbols_csv(task)
    return [call_workflow(task, "manage_group", "manage_watchlist_groups", args)]


def compile_data_health(task: ResolvedTask) -> list[WorkflowCall]:
    return [call_workflow(task, "data_health", "get_data_health")]


def compile_formal_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "start")
    if action == "status":
        return [call_workflow(task, "analysis_status", "get_analysis_status", select_parameters(task, {"task_id", "status", "limit"}))]
    if not task.symbols:
        raise WorkflowCompileError("starting formal analysis requires one resolved entity")
    return [
        call_workflow(
            task,
            "start_analysis",
            "run_stock_analysis",
            {
                "symbol": task.symbols[0] if task.symbols else "",
                **select_parameters(task, {"force_refresh", "notify_on_complete", "prompt_template_id"}),
            },
        )
    ]


def compile_history(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "search")
    if action == "read":
        return [
            call_workflow(
                task,
                "read_report",
                "read_analysis_report",
                select_parameters(task, {"record_id", "include_markdown", "include_news"}),
            )
        ]
    if action == "delete":
        args = select_parameters(task, {"record_ids"})
        args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
        return [call_workflow(task, "delete_history", "delete_analysis_history", args)]
    args = select_parameters(task, {"start_date", "end_date", "page", "limit"})
    if task.symbols:
        args["symbol"] = task.symbols[0]
    return [call_workflow(task, "search_history", "search_analysis_history", args)]


def compile_template(task: ResolvedTask) -> list[WorkflowCall]:
    args = select_parameters(task, {"action", "template_id", "name", "content", "set_default"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [call_workflow(task, "manage_template", "manage_analysis_templates", args)]


def compile_batch_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    scope = str(task.parameters.get("scope") or "")
    args = select_parameters(
        task,
        {
            "scope",
            "group_name",
            "analysis_mode",
            "prompt_template_id",
            "force_refresh",
        },
    )
    if scope == "symbols":
        if not task.symbols:
            raise WorkflowCompileError("batch_analysis scope=symbols requires resolved entities")
        if len(task.symbols) > 50:
            raise WorkflowCompileError("batch_analysis supports at most 50 resolved entities")
        args["symbols"] = symbols_csv(task)
    return [call_workflow(task, "run_batch", "run_batch_analysis", args)]


def compile_batch_management(task: ResolvedTask) -> list[WorkflowCall]:
    args = select_parameters(task, {"action", "run_id", "symbols", "limit"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [call_workflow(task, "manage_batch", "manage_batch_run", args)]


def compile_schedule(task: ResolvedTask) -> list[WorkflowCall]:
    args = select_parameters(task, {"action", "enabled", "times", "prompt_template_id"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [call_workflow(task, "manage_schedule", "manage_analysis_schedule", args)]


def compile_notification(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "status")
    if action == "status":
        return [call_workflow(task, "notification_status", "get_notification_status")]
    args = select_parameters(task, {"content_type", "message", "record_id", "batch_run_id", "title"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [call_workflow(task, "send_notification", "send_notification", args)]


def compile_source_discovery(task: ResolvedTask) -> list[WorkflowCall]:
    route_path = str(task.parameters.get("route_path") or "").strip()
    if route_path:
        return [
            call_workflow(
                task,
                "inspect_source",
                "inspect_rss_source",
                select_parameters(
                    task,
                    {"route_path", "keyword", "force"},
                ),
            )
        ]
    query = str(
        task.parameters.get("query")
        or task.parameters.get("keyword")
        or task.candidate.objective
    ).strip()
    return [
        call_workflow(
            task,
            "discover_sources",
            "discover_rss_sources",
            {
                "query": query,
                **select_parameters(
                    task,
                    {
                        "namespace",
                        "category",
                        "recommended_only",
                        "force",
                        "offset",
                        "limit",
                    },
                ),
            },
        )
    ]


def _source_route(source: object) -> str:
    if not isinstance(source, Mapping):
        return ""
    ref = source.get("source_ref")
    if isinstance(ref, Mapping):
        route_path = str(ref.get("route_path") or "").strip()
        if route_path:
            return route_path
    return str(source.get("route_path") or "").strip()


def _select_source_route(task: ResolvedTask) -> str:
    explicit = str(task.parameters.get("route_path") or "").strip()
    if explicit:
        return explicit
    sources = task.parameters.get("sources")
    if not isinstance(sources, list) or not sources:
        raise WorkflowCompileError(
            "feed_read requires route_path or an upstream RSS source collection"
        )
    ranked = sorted(
        (source for source in sources if isinstance(source, Mapping)),
        key=lambda source: (
            str(
                (
                    source.get("source_ref")
                    if isinstance(source.get("source_ref"), Mapping)
                    else source
                ).get("readiness")
                or "available"
            )
            == "unavailable",
            not bool(
                (
                    source.get("source_ref")
                    if isinstance(source.get("source_ref"), Mapping)
                    else source
                ).get("auto_recommended", False)
            ),
            -float(source.get("relevance_score") or 0),
        ),
    )
    route_path = _source_route(ranked[0]) if ranked else ""
    if not route_path:
        raise WorkflowCompileError(
            "upstream RSS source collection does not contain a route_path"
        )
    return route_path


def compile_feed_read(task: ResolvedTask) -> list[WorkflowCall]:
    route_path = _select_source_route(task)
    return [
        call_workflow(
            task,
            "read_feed",
            "read_rss_feed",
            {
                "route_path": route_path,
                **select_parameters(
                    task,
                    {"params", "options", "namespace", "limit", "force"},
                ),
            },
        )
    ]


def _matching_item(
    items: list[object],
    *,
    item_id: str,
    title: str,
    link: str,
    selection: str,
) -> Mapping[str, Any]:
    candidates = [item for item in items if isinstance(item, Mapping)]
    if item_id:
        candidates = [
            item
            for item in candidates
            if str(
                (
                    item.get("item_ref")
                    if isinstance(item.get("item_ref"), Mapping)
                    else item
                ).get("item_id")
                or ""
            )
            == item_id
        ]
    if link:
        candidates = [
            item
            for item in candidates
            if str(item.get("link") or "") == link
        ]
    if title:
        candidates = [
            item
            for item in candidates
            if str(item.get("title") or "") == title
        ]
    if selection == "latest" and not (item_id or title or link):
        def published_timestamp(item: Mapping[str, Any]) -> float:
            value = str(item.get("published") or "").strip()
            if not value:
                return float("-inf")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value)
                except (TypeError, ValueError, OverflowError):
                    return float("-inf")
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()

        return max(
            enumerate(candidates),
            key=lambda pair: (published_timestamp(pair[1]), -pair[0]),
        )[1]
    if len(candidates) != 1:
        raise WorkflowCompileError(
            "article_read must resolve exactly one upstream item; "
            "provide selection=latest, item_id, or link when the feed contains multiple items"
        )
    return candidates[0]


def compile_article(task: ResolvedTask) -> list[WorkflowCall]:
    resource_id = str(task.parameters.get("resource_id") or "").strip()
    resources = task.parameters.get("resources")
    if isinstance(resources, list):
        candidates = [
            resource
            for resource in resources
            if isinstance(resource, Mapping)
            and str(resource.get("resource_id") or "").strip()
        ]
        if resource_id:
            candidates = [
                resource
                for resource in candidates
                if str(resource.get("resource_id") or "").strip() == resource_id
            ]
        document_mime_type = str(
            task.parameters.get("document_mime_type") or ""
        ).strip().lower()
        if document_mime_type:
            candidates = [
                resource
                for resource in candidates
                if str(resource.get("mime_type") or "").strip().lower()
                == document_mime_type
            ]
        if len(candidates) == 1:
            resource_id = str(candidates[0].get("resource_id") or "").strip()
        elif resource_id or document_mime_type:
            raise WorkflowCompileError(
                "document selector must resolve exactly one bound text resource"
            )
        elif len(candidates) > 1:
            raise WorkflowCompileError(
                "article_read has multiple bound text resources; provide document_mime_type or resource_id"
            )
    if resource_id:
        args = {
            "resource_id": resource_id,
            **select_parameters(
                task,
                {
                    "query",
                    "offset",
                    "limit",
                    "page_start",
                    "page_end",
                    "reading_mode",
                },
            ),
        }
        return [
            call_workflow(
                task,
                "read_text_document",
                "read_text_document",
                args,
            )
        ]

    items = task.parameters.get("items")
    if not isinstance(items, list) or not items:
        raise WorkflowCompileError(
            "article_read requires resource_id or an upstream RSS item collection"
        )
    item = _matching_item(
        items,
        item_id=str(task.parameters.get("item_id") or "").strip(),
        title=str(task.parameters.get("title") or "").strip(),
        link=str(task.parameters.get("link") or "").strip(),
        selection=str(task.parameters.get("selection") or "").strip(),
    )
    item_ref = item.get("item_ref")
    if not isinstance(item_ref, Mapping):
        raise WorkflowCompileError(
            "upstream RSS item does not contain a stable item_ref"
        )
    return [
        call_workflow(
            task,
            "read_rss_item",
            "read_rss_item",
            {
                "item_ref": dict(item_ref),
                "item": dict(item),
                "include_documents": bool(
                    task.parameters.get("include_documents", True)
                ),
                "force": bool(task.parameters.get("force", False)),
            },
        )
    ]


def compile_transform(task: ResolvedTask) -> list[WorkflowCall]:
    return [call_workflow(task, "transform_feed", "transform_webpage_to_feed", dict(task.parameters))]


def compile_export(task: ResolvedTask) -> list[WorkflowCall]:
    return [
        call_workflow(
            task,
            "export_feed",
            "export_rss_feed",
            select_parameters(
                task,
                {
                    "route_path",
                    "params",
                    "options",
                    "namespace",
                    "format",
                    "limit",
                },
            ),
        )
    ]


def compile_web(task: ResolvedTask) -> list[WorkflowCall]:
    url = str(task.parameters.get("url") or "").strip()
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if url:
        return [
            call_workflow(
                task,
                "fetch_public_page",
                "webfetch",
                {
                    "url": url,
                    **select_parameters(task, {"format"}),
                },
            )
        ]
    return [
        call_workflow(
            task,
            "search_public_web",
            "websearch",
            {
                "query": query,
                **select_parameters(task, {"numResults", "livecrawl", "type", "contextMaxCharacters", "includeContent"}),
            },
        )
    ]



__all__ = [
    "compile_theme_discovery",
    "compile_theme_evidence",
    "compile_screening",
    "compile_collection_filter",
    "compile_watchlist_query",
    "compile_watchlist_mutation",
    "compile_group",
    "compile_data_health",
    "compile_formal_analysis",
    "compile_history",
    "compile_template",
    "compile_batch_analysis",
    "compile_batch_management",
    "compile_schedule",
    "compile_notification",
    "compile_source_discovery",
    "compile_feed_read",
    "compile_article",
    "compile_transform",
    "compile_export",
    "compile_web",
]
