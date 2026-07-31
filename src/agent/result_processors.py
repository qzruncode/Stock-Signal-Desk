# -*- coding: utf-8 -*-
"""Typed semantic result processors for completed standard tasks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.evidence_facts import (
    bind_company_evidence,
    collect_evidence_sources,
)
from src.agent.industry_catalog_selection import rank_project_board_domains_v2
from src.agent.orchestrator_v2.contracts import AgentErrorCode, RepairIssueV2
from src.agent.orchestrator_v2.planner import (
    ExactContractValidationError,
    call_model_exact_v2,
)
from src.agent.result_contracts import (
    MappingSelectionContext,
    ThemeEvidenceContext,
)
from src.agent.task_workflows import ResolvedTask
from src.agent.task_workflows import ResultSelectionMode, ResultSelectionSpec
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.tools.symbols import find_securities_in_text


Completion = Callable[..., Awaitable[Any]]
ProcessorProgress = Callable[[int, int, dict[str, Any]], Awaitable[None]]
PROJECT_BOARD_CACHE_VERSION = "v4"

logger = logging.getLogger(__name__)


class RankedDomainCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=64)
    tier: int = Field(ge=1, le=4)
    rationale: str = Field(min_length=1, max_length=300)
    support_quote: str = Field(min_length=1, max_length=600)
    source_id: str
    confidence: float = Field(ge=0.0, le=1.0)


class RankedProjectBoard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    board_name: str = Field(min_length=1, max_length=64)
    tier: int = Field(ge=1, le=4)
    rationale: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)


class RankedProjectBoardSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boards: list[RankedProjectBoard] = Field(default_factory=list, max_length=16)


class CompanyThemeEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=32)
    domain: str = Field(min_length=1, max_length=64)


class CompanyThemeAnalysisCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    company_name: str = Field(min_length=1, max_length=120)
    verdict: str = Field(pattern=r"^(pass|fail|insufficient)$")
    theme_fit: str = Field(pattern=r"^(exact|adjacent|none)$")
    development_level: str = Field(
        pattern=(r"^(none|layout|investment|customer_validation|order|" r"mass_production|revenue)$")
    )
    matched_domains: list[str] = Field(default_factory=list, max_length=16)
    reason: str = Field(min_length=1, max_length=600)
    evidence: list[CompanyThemeEvidenceReference] = Field(
        default_factory=list,
        max_length=6,
    )
    confidence: float = Field(ge=0.0, le=1.0)


_COMPANY_THEME_ANALYSIS_SYSTEM_PROMPT = """\
你是逐股产业业务分析器。输入只包含一家公司，你必须独立判断该公司是否符合用户要求的目标产业、
所选子领域及发展强度，不能与其他公司比较，也不能使用模型记忆补事实。

规则：
1. project board membership 只说明候选来源，绝不是业务证据。
2. 必须同时判断上位 target_topics、具体 domain_theses 和 selection_objective。只做相似产品但服务于
   无关应用场景，theme_fit 不能标 exact。
3. development_level 表示本公司在目标命题上的最强已证实阶段：layout、investment、
   customer_validation、order、mass_production、revenue；没有目标业务事实时为 none。
4. verdict=pass 只用于证据证明公司正在对目标方向进行实质、持续的发展，且 theme_fit=exact。
   单纯板块归属、泛化公司简介、未经落实的行业展望或只出现公司名称不能通过。
5. 证据足以证明不相关、仅服务其他场景或没有实质发展时返回 fail；项目来源缺失、事实过少或语义无法
   确认时返回 insufficient。不要为了凑数量降低标准。
6. 每条 evidence 只选择 evidence_documents 中真实存在的 source_id，并把 domain 逐字绑定到
   supplied_domains 之一。原文摘录由程序从该 source_id 确定性提取，模型不得复制或改写引文。
   pass 至少需要一条有效的来源绑定。
7. symbol 必须原样返回 requested_company.symbol，公司名称使用 requested_company.name。
8. 必须通过 submit_company_theme_analysis 返回唯一一家公司结果。\
"""


_PROJECT_BOARD_RANKING_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_ranked_project_boards",
        "description": "Select and rank only boards present in the supplied project catalog.",
        "strict": True,
        "parameters": RankedProjectBoardSubmission.model_json_schema(),
    },
}


_PROJECT_BOARD_RANKING_SYSTEM_PROMPT = """\
你是项目实时板块映射与排序器。用户的产业表达可能不是正式板块名，你必须理解其语义，并且只能从输入
project_boards 中选择真实存在的板块。

规则：
1. board_name 必须逐字等于 project_boards 中的 name，不得创造、改写或拼接板块名。
2. 围绕 requested_topic 选择最能代表受益环节的实际板块。tier=1 表示产业受益最直接，tier=2 至
   tier=4 依次降低；排序依据是产业关联和受益传导，不是当日涨跌或资金流。
3. 优先选择具体产品、部件、软件或服务板块；只在目录缺乏更具体板块时选择上位主题板块。
   当用户询问“哪些方向、领域或环节”时，不要用仅仅重复 requested_topic 的总板块充当受益子方向，
   除非目录中确实没有更具体且相关的实际板块。
4. 不得因为名称含有相同字词就选择产业含义无关的板块；不确定时宁可少选。
5. rationale 解释用户表达与真实板块之间的产业映射及受益机制，不得声称板块成员必然有主营、订单、
   客户或收入。
6. 必须遵守输入 result_selection：best_one 只选唯一最优板块，top_k 最多选择 max_items 个，
   all_relevant 才可返回全部相关板块。最多返回 16 个板块。
7. project_boards 是本轮完整项目板块名称数组，必须直接在全部目录中完成语义判断、全局比较和分层。
8. 必须通过 submit_ranked_project_boards 返回结构化结果。\
"""


_RANKED_DOMAIN_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_ranked_industry_domains",
        "description": "Return source-grounded industry domains grouped by benefit tier.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "domains": {
                    "type": "array",
                    "maxItems": 24,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "label": {"type": "string"},
                            "tier": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 4,
                            },
                            "rationale": {"type": "string"},
                            "support_quote": {"type": "string"},
                            "source_id": {"type": "string"},
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                        },
                        "required": [
                            "label",
                            "tier",
                            "rationale",
                            "support_quote",
                            "source_id",
                            "confidence",
                        ],
                    },
                },
            },
            "required": ["domains"],
        },
    },
}


_RANKED_DOMAIN_SYSTEM_PROMPT = """\
你是产业研究结果整理器。只能依据输入 sources，提取并排序用户问题中的受益细分领域。

规则：
1. tier=1 表示受益最直接、价值量或放量弹性最强；tier=2 至 tier=4 依次降低。
2. label 必须是可继续用于公司研究的具体产品、部件、软件或服务领域，不能写股票、公司或泛泛结论，
   也不要重复 requested topic 中的上位产业名称。
3. support_quote 必须是 source_id 对应原文中可逐字回查的连续短片段，不得改写或拼接。
4. rationale 只解释受益机制，不得加入原文没有的订单、收入、份额或公司事实。
5. 同义领域只保留一个；必须遵守输入 result_selection：best_one 只选唯一最优领域，top_k 最多选择
   max_items 个，all_relevant 才可返回全部相关领域。最多返回 24 个；没有来源支持的领域不得返回。
6. 必须通过 submit_ranked_industry_domains 返回结构化结果。\
"""


def _response_payload(response: Any, function_name: str) -> dict[str, Any]:
    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)

    for choice in field(response, "choices") or []:
        message = field(choice, "message")
        if message is None:
            continue
        for tool_call in field(message, "tool_calls") or []:
            function = field(tool_call, "function")
            if field(function, "name") != function_name:
                continue
            arguments = field(function, "arguments")
            if isinstance(arguments, dict):
                return arguments
            if arguments:
                value = json.loads(str(arguments))
                if isinstance(value, dict):
                    return value
        content = field(message, "content")
        if isinstance(content, str) and content.strip():
            value = json.loads(content)
            if isinstance(value, dict):
                return value
    raise ValueError(f"result processor returned no {function_name} payload")


def _normalized_text(value: str) -> str:
    return re.sub(r"[\s*_`#>]+", "", value).replace("／", "/").lower()


def _domain_labels(task: ResolvedTask) -> list[str]:
    values = task.parameters.get("domains")
    if not isinstance(values, list):
        return []
    labels: list[str] = []
    for value in values:
        label = value if isinstance(value, str) else value.get("label") if isinstance(value, Mapping) else ""
        text = str(label or "").strip()
        if text and text not in labels:
            labels.append(text)
    return labels


def _result_selection(task: ResolvedTask) -> ResultSelectionSpec:
    selection = task.result_selection
    if selection is None:
        raise ValueError(f"{task.kind.value} result processor requires result_selection")
    return selection


def _selection_payload(selection: ResultSelectionSpec) -> dict[str, Any]:
    return {
        "mode": selection.mode.value,
        "max_items": selection.max_items,
    }


def _apply_result_selection(
    items: list[dict[str, Any]],
    selection: ResultSelectionSpec,
) -> list[dict[str, Any]]:
    if selection.mode == ResultSelectionMode.ALL_RELEVANT:
        return items
    return items[: int(selection.max_items or 0)]


def _catalog_result(
    evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for packet in evidence:
        if (
            isinstance(packet, Mapping)
            and packet.get("tool") == "get_domain_board_catalog"
            and isinstance(packet.get("result"), Mapping)
        ):
            return dict(packet["result"])
    return None


def _project_board_snapshot_id(
    boards: list[dict[str, Any]],
    _catalog: Mapping[str, Any],
) -> str:
    payload = sorted(
        (
            {
                "name": board["name"],
                "sector_code": board["sector_code"],
            }
            for board in boards
        ),
        key=lambda item: (item["name"], item["sector_code"]),
    )
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return digest[:20]


def _project_board_cache_key(
    task: ResolvedTask,
    llm_cfg: Mapping[str, Any],
    snapshot_id: str,
) -> str | None:
    if not llm_cfg.get("api_base"):
        return None
    payload = {
        "version": PROJECT_BOARD_CACHE_VERSION,
        "model": str(llm_cfg.get("model") or ""),
        "snapshot_id": snapshot_id,
        "topic": _normalized_text(str(task.parameters.get("query") or task.candidate.objective)),
        "root_topics": [_normalized_text(value) for value in _domain_labels(task)],
        "result_selection": _selection_payload(_result_selection(task)),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"project_board_ranking:{PROJECT_BOARD_CACHE_VERSION}:{digest}"


def _load_project_board_ranking_cache(
    cache_key: str | None,
) -> dict[str, Any] | None:
    if not cache_key:
        return None
    try:
        from src.storage.manager import DatabaseManager

        cached = DatabaseManager.get_instance().get_tool_cache(cache_key)
        if not cached:
            return None
        payload = json.loads(bytes(cached["payload"]).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        if (
            payload.get("success") is not True
            or payload.get("coverage_complete") is not True
            or payload.get("ranking_complete") is not True
            or not str(payload.get("catalog_snapshot_id") or "")
        ):
            return None
        return {
            **payload,
            "cache_hit": True,
        }
    except Exception:
        logger.warning(
            "[ResultProcessor] ignored invalid project-board ranking cache",
            exc_info=True,
        )
        return None


def _save_project_board_ranking_cache(
    cache_key: str | None,
    result: Mapping[str, Any],
) -> None:
    if (
        not cache_key
        or result.get("success") is not True
        or result.get("coverage_complete") is not True
        or result.get("ranking_complete") is not True
    ):
        return
    try:
        from src.storage.manager import DatabaseManager

        payload = json.dumps(
            dict(result),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        DatabaseManager.get_instance().save_tool_cache(cache_key, payload)
    except Exception:
        logger.warning(
            "[ResultProcessor] failed to persist project-board ranking cache",
            exc_info=True,
        )


def _semantic_failure_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "semantic_provider_timeout"
    if isinstance(exc, (json.JSONDecodeError, ValidationError, ValueError)):
        return "semantic_invalid_response"
    return "semantic_provider_error"


async def _rank_project_board_domains_v1(
    task: ResolvedTask,
    catalog: dict[str, Any],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    selection = _result_selection(task)
    boards = [
        {
            "name": str(item.get("name") or "").strip(),
            "sector_code": str(item.get("sector_code") or "").strip(),
            "main_flow_rank": item.get("main_flow_rank"),
            "main_net_inflow": item.get("main_net_inflow"),
            "main_net_inflow_pct": item.get("main_net_inflow_pct"),
            "pct_chg": item.get("pct_chg"),
        }
        for item in catalog.get("boards") or []
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    ]
    if not boards:
        return {
            "success": False,
            "partial": False,
            "errors": ["项目实时板块目录为空，无法形成板块排序。"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {"domain_collection": []},
            "catalog_count": 0,
            "batch_total": 0,
            "batch_completed": 0,
            "coverage_complete": False,
            "ranking_complete": False,
        }

    by_name = {item["name"]: item for item in boards}
    snapshot_id = _project_board_snapshot_id(boards, catalog)
    cache_key = _project_board_cache_key(task, llm_cfg, snapshot_id)
    cached_result = _load_project_board_ranking_cache(cache_key)
    cached_names = {
        str(item.get("board_name") or item.get("label") or "").strip()
        for item in (cached_result or {}).get("items") or []
        if isinstance(item, Mapping)
    }
    if (
        cached_result is not None
        and cached_result.get("catalog_snapshot_id") == snapshot_id
        and bool(cached_names)
        and cached_names <= set(by_name)
    ):
        return cached_result

    async def request_ranking() -> list[RankedProjectBoard]:
        request = {
            "requested_topic": str(task.parameters.get("query") or task.candidate.objective),
            "root_topics": _domain_labels(task),
            "result_selection": _selection_payload(selection),
            "project_boards": [board["name"] for board in boards],
        }
        kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=False,
            messages=[
                {"role": "system", "content": _PROJECT_BOARD_RANKING_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            tools=[_PROJECT_BOARD_RANKING_TOOL],
            tool_choice={
                "type": "function",
                "function": {"name": "submit_ranked_project_boards"},
            },
            temperature=0,
            max_tokens=4_000,
        )
        response = await completion(**kwargs)
        payload = _response_payload(response, "submit_ranked_project_boards")
        raw_boards = payload.get("boards")
        if not isinstance(raw_boards, list):
            raise ValueError("ranked project boards response is not a list")
        ranked: list[RankedProjectBoard] = []
        for raw in raw_boards:
            try:
                candidate = RankedProjectBoard.model_validate(raw)
            except ValidationError:
                continue
            if candidate.board_name in by_name and candidate.confidence >= 0.55:
                ranked.append(candidate)
        return ranked

    ranked_candidates: list[RankedProjectBoard] | None = None
    model_failure: BaseException | None = None
    logger.info(
        ("[ResultProcessor] project-board direct model request " "snapshot=%s catalog_count=%s payload_count=%s"),
        snapshot_id,
        len(boards),
        len(boards),
    )
    try:
        ranked_candidates = await request_ranking()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        model_failure = exc
        logger.warning(
            ("[ResultProcessor] project-board direct model request failed " "snapshot=%s error_code=%s"),
            snapshot_id,
            _semantic_failure_code(exc),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    if ranked_candidates is not None and progress is not None:
        try:
            await progress(
                1,
                1,
                {
                    "stage": "complete_catalog",
                    "success": True,
                },
            )
        except Exception:
            logger.warning(
                "[ResultProcessor] project-board progress callback failed",
                exc_info=True,
            )

    selected: dict[str, dict[str, Any]] = {}
    for candidate in ranked_candidates or []:
        board = by_name.get(candidate.board_name)
        if board is None:
            continue
        item = {
            "label": candidate.board_name,
            "board_name": candidate.board_name,
            "board_code": board["sector_code"],
            "board_queries": [candidate.board_name],
            "mapping_type": "catalog_binding",
            "unresolved_parts": [],
            "tier": candidate.tier,
            "rationale": candidate.rationale,
            "confidence": candidate.confidence,
            "main_flow_rank": board.get("main_flow_rank"),
            "main_net_inflow": board.get("main_net_inflow"),
            "main_net_inflow_pct": board.get("main_net_inflow_pct"),
            "pct_chg": board.get("pct_chg"),
            "source_name": str(catalog.get("source") or "项目实时板块目录"),
            "source_date": str(catalog.get("data_time") or ""),
            "selection_basis": "project_live_board_catalog",
        }
        previous = selected.get(candidate.board_name)
        if previous is None or (
            int(item["tier"]),
            -float(item["confidence"]),
        ) < (
            int(previous["tier"]),
            -float(previous["confidence"]),
        ):
            selected[candidate.board_name] = item

    items = sorted(
        selected.values(),
        key=lambda item: (
            int(item["tier"]),
            -float(item["confidence"]),
            str(item["label"]),
        ),
    )
    items = _apply_result_selection(items, selection)
    coverage_complete = ranked_candidates is not None
    ranking_complete = coverage_complete
    batch_completed = 1 if coverage_complete else 0
    failed_batches = (
        []
        if model_failure is None
        else [
            {
                "batch": 1,
                "error_code": _semantic_failure_code(model_failure),
                "error_type": type(model_failure).__name__,
            }
        ]
    )
    diagnostics = {
        "source_scope": "project_live_board_catalog",
        "catalog_snapshot_id": snapshot_id,
        "catalog_count": len(boards),
        "batch_total": 1,
        "batch_completed": batch_completed,
        "coverage_complete": coverage_complete,
        "ranking_complete": ranking_complete,
        "failed_batches": failed_batches,
        "cache_hit": False,
        "result_selection": _selection_payload(selection),
    }
    if not coverage_complete:
        return {
            "success": False,
            "partial": bool(items),
            "errors": [
                "完整实时板块目录已经一次性交给模型，但本次模型调用未能"
                "返回有效结构化判断；因此没有发布可供后续找股使用的领域集合。"
            ],
            "warnings": [f"完整目录模型判断 {_semantic_failure_code(model_failure)}"],
            "items": items,
            "semantic_artifacts": [],
            "resource_outputs": {},
            **diagnostics,
        }
    if not items:
        return {
            "success": False,
            "partial": False,
            "errors": ["完整目录中没有选出通过结构校验的相关板块。"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {},
            **diagnostics,
        }

    groups = [
        {
            "tier": tier,
            "domains": [
                {
                    "label": item["label"],
                    "tier": item["tier"],
                    "board_queries": item["board_queries"],
                    "mapping_type": item["mapping_type"],
                    "rationale": item["rationale"],
                    "unresolved_parts": [],
                }
                for item in items
                if item["tier"] == tier
            ],
        }
        for tier in sorted({int(item["tier"]) for item in items})
    ]
    artifact = {
        "type": "ranked_domains",
        "topic": str(task.parameters.get("query") or task.candidate.objective),
        "root_topics": _domain_labels(task),
        "source_scope": "project_live_board_catalog",
        "catalog_count": len(boards),
        "catalog_snapshot_id": snapshot_id,
        "batch_total": 1,
        "batch_completed": batch_completed,
        "coverage_complete": True,
        "ranking_complete": True,
        "result_selection": _selection_payload(selection),
        "groups": groups,
    }
    result = {
        "success": True,
        "partial": bool(catalog.get("partial")),
        "errors": list(catalog.get("errors") or []),
        "warnings": list(catalog.get("warnings") or []),
        "items": items,
        "semantic_artifacts": [artifact],
        "resource_outputs": {
            "domain_collection": [
                {
                    "label": item["label"],
                    "board_queries": item["board_queries"],
                    "mapping_type": item["mapping_type"],
                    "rationale": item["rationale"],
                    "unresolved_parts": [],
                }
                for item in items
            ],
        },
        "source_scope": "project_live_board_catalog",
        "result_selection": _selection_payload(selection),
        "source_name": str(catalog.get("source") or "项目实时板块目录"),
        "source_date": str(catalog.get("data_time") or ""),
        **diagnostics,
    }
    _save_project_board_ranking_cache(cache_key, result)
    return result


def _industry_catalog_mapping_mode() -> str:
    value = str(os.getenv("AGENT_INDUSTRY_CATALOG_MAPPING_MODE", "v2")).strip().lower()
    return "v1" if value == "v1" else "v2"


async def _rank_project_board_domains(
    task: ResolvedTask,
    catalog: dict[str, Any],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    if _industry_catalog_mapping_mode() == "v1":
        return await _rank_project_board_domains_v1(
            task,
            catalog,
            llm_cfg,
            completion,
            progress,
        )
    return await rank_project_board_domains_v2(
        task,
        catalog,
        llm_cfg,
        completion,
        progress,
    )


async def _rank_public_industry_domains(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    completion: Completion,
) -> dict[str, Any]:
    selection = _result_selection(task)
    sources = collect_evidence_sources(evidence)[:10]
    if not sources:
        return {
            "success": False,
            "partial": False,
            "errors": ["产业研究来源为空，无法形成可复用的领域排序。"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {"domain_collection": []},
        }
    source_batches = [sources[index : index + 2] for index in range(0, len(sources), 2)]

    async def rank_batch(batch: list[dict[str, Any]]) -> list[Any]:
        request = {
            "research_objective": task.candidate.objective,
            "subjects": task.parameters.get("subjects") or [],
            "result_selection": _selection_payload(selection),
            "sources": batch,
        }
        kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=False,
            messages=[
                {"role": "system", "content": _RANKED_DOMAIN_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            tools=[_RANKED_DOMAIN_TOOL],
            tool_choice={
                "type": "function",
                "function": {"name": "submit_ranked_industry_domains"},
            },
            temperature=0,
            max_tokens=2_500,
        )
        response = await completion(**kwargs)
        payload = _response_payload(
            response,
            "submit_ranked_industry_domains",
        )
        domains = payload.get("domains")
        if not isinstance(domains, list):
            raise ValueError("ranked domain response is not a list")
        return domains

    tasks = [asyncio.create_task(rank_batch(batch)) for batch in source_batches]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)
    raw_domains: list[Any] = []
    failures: list[BaseException] = []
    for result in batch_results:
        if isinstance(result, BaseException):
            failures.append(result)
        else:
            raw_domains.extend(result)
    source_map = {str(source["source_id"]): source for source in sources}
    best_by_label: dict[str, dict[str, Any]] = {}
    for raw in raw_domains:
        try:
            candidate = RankedDomainCandidate.model_validate(raw)
        except ValidationError:
            continue
        source = source_map.get(candidate.source_id)
        if source is None or candidate.confidence < 0.65:
            continue
        quote = candidate.support_quote.strip()
        if _normalized_text(quote) not in _normalized_text(str(source.get("text") or "")):
            continue
        identity = _normalized_text(candidate.label)
        if not identity:
            continue
        item = {
            "label": candidate.label,
            "tier": candidate.tier,
            "rationale": candidate.rationale,
            "support_quote": quote,
            "source_id": candidate.source_id,
            "source_name": str(source.get("source_name") or "公开资料"),
            "source_url": str(source.get("source_url") or ""),
            "source_date": str(source.get("source_date") or ""),
            "confidence": candidate.confidence,
        }
        previous = best_by_label.get(identity)
        if previous is None or (
            int(item["tier"]),
            -float(item["confidence"]),
        ) < (
            int(previous["tier"]),
            -float(previous["confidence"]),
        ):
            best_by_label[identity] = item
    items = list(best_by_label.values())
    items.sort(key=lambda item: (int(item["tier"]), -float(item["confidence"]), item["label"]))
    items = _apply_result_selection(items, selection)
    if not items:
        return {
            "success": False,
            "partial": False,
            "errors": [
                "来源中没有通过原文校验的受益领域排序。"
                + (f" {len(failures) + len(pending)} 个语义批次未完成。" if failures or pending else "")
            ],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {"domain_collection": []},
        }
    groups = [
        {
            "tier": tier,
            "domains": [
                {
                    "label": item["label"],
                    "tier": item["tier"],
                }
                for item in items
                if item["tier"] == tier
            ],
        }
        for tier in sorted({int(item["tier"]) for item in items})
    ]
    artifact = {
        "type": "ranked_domains",
        "topic": str(task.parameters.get("query") or task.candidate.objective),
        "root_topics": _domain_labels(task),
        "result_selection": _selection_payload(selection),
        "groups": groups,
    }
    return {
        "success": True,
        "partial": bool(failures or pending),
        "errors": ([f"{len(failures) + len(pending)} 个语义批次未完成。"] if failures or pending else []),
        "items": items,
        "result_selection": _selection_payload(selection),
        "semantic_artifacts": [artifact],
        "resource_outputs": {
            "domain_collection": [
                {
                    "label": item["label"],
                    "tier": item["tier"],
                    "rationale": item["rationale"],
                }
                for item in items
            ],
        },
    }


async def _rank_industry_domains(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    catalog = _catalog_result(evidence)
    if catalog is not None:
        return await _rank_project_board_domains(
            task,
            catalog,
            llm_cfg,
            completion,
            progress,
        )
    return {
        "success": False,
        "partial": False,
        "error_code": "resource_unavailable",
        "errors": ["产业研究没有取得项目实时板块目录；" "程序未改用公开来源生成另一套不可复用的领域结果。"],
        "warnings": [],
        "items": [],
        "semantic_artifacts": [],
        "resource_outputs": {},
        "source_scope": "project_live_board_catalog",
        "catalog_count": 0,
        "batch_total": 0,
        "batch_completed": 0,
        "coverage_complete": False,
        "ranking_complete": False,
        "coverage": {
            "catalog_total": 0,
            "catalog_supplied": 0,
            "selected_count": 0,
            "binding_complete": False,
        },
        "failed_batches": [],
    }


def _deterministic_evidence_quote(
    document: Mapping[str, Any],
    *,
    labels: list[str],
) -> str:
    text = str(document.get("text") or "").strip()
    if not text:
        return ""
    chunks = [chunk.strip() for chunk in re.split(r"(?<=[。！？!?；;])|\n+", text) if chunk.strip()]
    if not chunks:
        return text[:800]
    development_terms = (
        "减速器",
        "量产",
        "批量",
        "供货",
        "交付",
        "订单",
        "客户",
        "收入",
        "营收",
        "产能",
        "投资",
        "布局",
        "研发",
        "产品",
    )

    def score(chunk: str) -> tuple[int, int]:
        normalized = _normalized_text(chunk)
        label_score = sum(5 for label in labels if _normalized_text(label) in normalized)
        term_score = sum(1 for term in development_terms if _normalized_text(term) in normalized)
        return label_score + term_score, -len(chunk)

    quote = max(chunks, key=score)
    return quote[:800]


def _validated_company_theme_result(
    raw: Any,
    *,
    symbol: str,
    name: str,
    labels: list[str],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        candidate = CompanyThemeAnalysisCandidate.model_validate(raw)
    except ValidationError as exc:
        return {
            "symbol": symbol,
            "company_name": name,
            "verdict": "error",
            "theme_fit": "none",
            "development_level": "none",
            "matched_domains": [],
            "reason": f"模型结果未通过结构校验：{exc}",
            "evidence": [],
            "confidence": 0.0,
        }
    if candidate.symbol != symbol:
        return {
            "symbol": symbol,
            "company_name": name,
            "verdict": "error",
            "theme_fit": "none",
            "development_level": "none",
            "matched_domains": [],
            "reason": "模型返回的证券代码与当前单股任务不一致。",
            "evidence": [],
            "confidence": 0.0,
        }

    source_map = {
        str(document.get("source_id") or ""): document for document in documents if isinstance(document, Mapping)
    }
    valid_evidence: list[dict[str, Any]] = []
    for reference in candidate.evidence:
        source = source_map.get(reference.source_id)
        if source is None or reference.domain not in labels:
            continue
        quote = _deterministic_evidence_quote(source, labels=labels)
        if not quote:
            continue
        valid_evidence.append(
            {
                "source_id": reference.source_id,
                "domain": reference.domain,
                "support_quote": quote,
                "source_type": str(source.get("source_type") or ""),
                "source_name": str(source.get("source_name") or "项目数据源"),
                "source_url": str(source.get("source_url") or ""),
                "source_date": str(source.get("source_date") or ""),
                "official": bool(source.get("official")),
            }
        )

    matched_domains = [label for label in candidate.matched_domains if label in labels]
    verdict = candidate.verdict
    reason = candidate.reason
    if verdict == "pass" and (
        candidate.theme_fit != "exact"
        or candidate.development_level == "none"
        or not valid_evidence
        or not matched_domains
    ):
        verdict = "insufficient"
        reason = "模型给出通过结论，但没有同时满足精确主题、发展阶段、" "子领域和逐字证据校验，程序已降级为证据不足。"
    return {
        "symbol": symbol,
        "company_name": name,
        "verdict": verdict,
        "theme_fit": candidate.theme_fit,
        "development_level": candidate.development_level,
        "matched_domains": list(dict.fromkeys(matched_domains)),
        "reason": reason,
        "evidence": valid_evidence,
        "confidence": candidate.confidence,
    }


def _validate_company_theme_contract(
    value: BaseModel,
    *,
    symbol: str,
    name: str,
    labels: list[str],
    documents: list[dict[str, Any]],
) -> None:
    candidate = CompanyThemeAnalysisCandidate.model_validate(value)
    issues: list[RepairIssueV2] = []
    if candidate.symbol != symbol:
        issues.append(
            RepairIssueV2(
                pointer="/symbol",
                code="company_symbol_mismatch",
                expected=f"const={symbol}",
                allowed=(symbol,),
                message="symbol must match requested_company.symbol",
            )
        )
    if candidate.company_name != name:
        issues.append(
            RepairIssueV2(
                pointer="/company_name",
                code="company_name_mismatch",
                expected=f"const={name}",
                allowed=(name,),
                message="company_name must match requested_company.name",
            )
        )

    for index, domain in enumerate(candidate.matched_domains):
        if domain not in labels:
            issues.append(
                RepairIssueV2(
                    pointer=f"/matched_domains/{index}",
                    code="unknown_domain",
                    expected="one supplied domain",
                    allowed=tuple(labels),
                    message="matched domain was not supplied by the program",
                )
            )

    source_map = {
        str(document.get("source_id") or ""): document for document in documents if isinstance(document, Mapping)
    }
    valid_evidence_count = 0
    for index, reference in enumerate(candidate.evidence):
        source = source_map.get(reference.source_id)
        if source is None:
            issues.append(
                RepairIssueV2(
                    pointer=f"/evidence/{index}/source_id",
                    code="unknown_source",
                    expected="source_id from evidence_documents",
                    allowed=tuple(source_map),
                    message="evidence source was not supplied by the program",
                )
            )
            continue
        if reference.domain not in labels:
            issues.append(
                RepairIssueV2(
                    pointer=f"/evidence/{index}/domain",
                    code="unknown_domain",
                    expected="one supplied domain",
                    allowed=tuple(labels),
                    message="evidence domain was not supplied by the program",
                )
            )
            continue
        if not str(source.get("text") or "").strip():
            issues.append(
                RepairIssueV2(
                    pointer=f"/evidence/{index}/source_id",
                    code="empty_source",
                    expected="a source containing non-empty evidence text",
                    message="selected evidence source contains no usable text",
                )
            )
            continue
        valid_evidence_count += 1

    if candidate.verdict == "pass":
        if candidate.theme_fit != "exact":
            issues.append(
                RepairIssueV2(
                    pointer="/theme_fit",
                    code="pass_requires_exact_theme",
                    expected="const=exact",
                    allowed=("exact",),
                    message="pass requires exact theme fit",
                )
            )
        if candidate.development_level == "none":
            issues.append(
                RepairIssueV2(
                    pointer="/development_level",
                    code="pass_requires_development",
                    expected="a proven development stage other than none",
                    allowed=(
                        "layout",
                        "investment",
                        "customer_validation",
                        "order",
                        "mass_production",
                        "revenue",
                    ),
                    message="pass requires a proven development stage",
                )
            )
        if not candidate.matched_domains:
            issues.append(
                RepairIssueV2(
                    pointer="/matched_domains",
                    code="pass_requires_domain",
                    expected="at least one supplied domain",
                    allowed=tuple(labels),
                    message="pass requires a matched supplied domain",
                )
            )
        if valid_evidence_count == 0:
            issues.append(
                RepairIssueV2(
                    pointer="/evidence",
                    code="pass_requires_bound_evidence",
                    expected="at least one valid program-bound evidence source",
                    message="pass requires program-verifiable source binding",
                )
            )

    if issues:
        raise ExactContractValidationError(tuple(issues))


async def _analyze_candidate_companies(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    evidence_context: ThemeEvidenceContext,
    labels: list[str],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    names = dict(task.entity_names)
    packets_by_symbol: dict[str, dict[str, Any]] = {}
    duplicate_packets: set[str] = set()
    for packet in evidence:
        if (
            not isinstance(packet, Mapping)
            or packet.get("tool") != "get_company_theme_evidence"
            or not isinstance(packet.get("arguments"), Mapping)
            or not isinstance(packet.get("result"), Mapping)
        ):
            continue
        symbol = str(packet["arguments"].get("symbol") or "").strip()
        if symbol in packets_by_symbol:
            duplicate_packets.add(symbol)
            continue
        packets_by_symbol[symbol] = dict(packet)

    domain_theses = [
        {
            "label": label,
            "rationale": (
                evidence_context.thesis_for(label).rationale if evidence_context.thesis_for(label) is not None else ""
            ),
            "tier": (
                evidence_context.thesis_for(label).tier if evidence_context.thesis_for(label) is not None else None
            ),
        }
        for label in labels
    ]
    semaphore = asyncio.Semaphore(8)

    async def analyze(symbol: str) -> dict[str, Any]:
        name = names.get(symbol, symbol)
        packet = packets_by_symbol.get(symbol)
        if packet is None:
            return {
                "symbol": symbol,
                "company_name": name,
                "verdict": "error",
                "theme_fit": "none",
                "development_level": "none",
                "matched_domains": [],
                "reason": "Executor 没有为该候选公司生成单股证据任务。",
                "evidence": [],
                "confidence": 0.0,
                "source_status": {},
                "fallback_used": False,
            }
        result = dict(packet["result"])
        documents = [
            dict(document) for document in result.get("evidence_documents") or [] if isinstance(document, Mapping)
        ]
        base = {
            "source_status": result.get("source_status") or {},
            "project_source_coverage_complete": bool(result.get("project_source_coverage_complete")),
            "fallback_attempted": bool(result.get("fallback_attempted")),
            "fallback_used": bool(result.get("fallback_used")),
            "tool_errors": [str(error) for error in result.get("errors") or [] if error],
        }
        if not documents:
            return {
                "symbol": symbol,
                "company_name": name,
                "verdict": "insufficient",
                "theme_fit": "none",
                "development_level": "none",
                "matched_domains": [],
                "reason": "该公司的项目数据源及逐股网络兜底均未形成可分析文本。",
                "evidence": [],
                "confidence": 0.0,
                **base,
            }

        request = {
            "requested_company": {
                "symbol": symbol,
                "name": name,
            },
            "target_topics": evidence_context.target_topics,
            "supplied_domains": labels,
            "domain_theses": domain_theses,
            "selection_objective": task.candidate.objective,
            "evidence_documents": documents,
            "source_coverage": {
                "project_source_count": result.get("project_source_count"),
                "project_source_success_count": result.get("project_source_success_count"),
                "project_source_coverage_complete": result.get("project_source_coverage_complete"),
                "fallback_used": result.get("fallback_used"),
            },
        }
        try:
            async with semaphore:
                candidate, _, repair = await call_model_exact_v2(
                    llm_cfg=llm_cfg,
                    completion=completion,
                    function_name="submit_company_theme_analysis",
                    description=("Submit the independent theme-development verdict " "for exactly one company."),
                    model=CompanyThemeAnalysisCandidate,
                    system_prompt=_COMPANY_THEME_ANALYSIS_SYSTEM_PROMPT,
                    semantic_context=request,
                    node_id=f"company_theme_analysis:{symbol}",
                    value_validator=lambda value: (
                        _validate_company_theme_contract(
                            value,
                            symbol=symbol,
                            name=name,
                            labels=labels,
                            documents=documents,
                        )
                    ),
                    provider_error_code=AgentErrorCode.SYNTHESIS_FAILED,
                    schema_error_code=AgentErrorCode.SYNTHESIS_FAILED,
                    max_tokens=4_000,
                )
            payload = candidate.model_dump(mode="json")
            validated = _validated_company_theme_result(
                payload,
                symbol=symbol,
                name=name,
                labels=labels,
                documents=documents,
            )
            if repair is not None:
                validated["model_repair"] = repair.model_dump(mode="json")
            return {**validated, **base}
        except Exception as exc:
            return {
                "symbol": symbol,
                "company_name": name,
                "verdict": "error",
                "theme_fit": "none",
                "development_level": "none",
                "matched_domains": [],
                "reason": f"单股模型判断失败：{type(exc).__name__}: {exc}",
                "evidence": [],
                "confidence": 0.0,
                **base,
            }

    completed_count = 0
    progress_lock = asyncio.Lock()

    async def analyze_with_progress(symbol: str) -> dict[str, Any]:
        nonlocal completed_count
        result = await analyze(symbol)
        if progress is not None:
            async with progress_lock:
                completed_count += 1
                await progress(completed_count, len(task.symbols), result)
        return result

    company_results = await asyncio.gather(*(analyze_with_progress(symbol) for symbol in task.symbols))
    counts = {
        status: sum(1 for item in company_results if item.get("verdict") == status)
        for status in ("pass", "fail", "insufficient", "error")
    }
    passed = [item for item in company_results if item.get("verdict") == "pass"]
    missing_symbols = [symbol for symbol in task.symbols if symbol not in packets_by_symbol]
    analyzed_count = len(company_results)
    candidate_count = len(task.symbols)
    coverage_complete = analyzed_count == candidate_count and not missing_symbols and not duplicate_packets
    domain_results = [
        {
            "domain": label,
            "analyzed_company_count": candidate_count,
            "passed_company_count": sum(1 for item in passed if label in item.get("matched_domains", [])),
        }
        for label in labels
    ]
    security_collection = [
        {
            "symbol": str(item["symbol"]),
            "name": str(item["company_name"]),
        }
        for item in passed
    ]
    warnings: list[str] = []
    if counts["insufficient"]:
        warnings.append(f"{counts['insufficient']} 家完成了独立分析但证据不足，未纳入通过名单。")
    if counts["error"]:
        warnings.append(f"{counts['error']} 家单股分析发生错误，未静默计入排除结果。")
    if duplicate_packets:
        warnings.append("检测到重复单股证据任务：" + "、".join(sorted(duplicate_packets)))
    artifact = {
        "type": "per_security_theme_analysis",
        "target_topics": evidence_context.target_topics,
        "domains": labels,
        "candidate_count": candidate_count,
        "analyzed_candidate_count": analyzed_count,
        "candidate_coverage_complete": coverage_complete,
        "verdict_counts": counts,
        "companies": company_results,
    }
    return {
        "success": coverage_complete,
        "partial": (not coverage_complete or counts["insufficient"] > 0 or counts["error"] > 0),
        "errors": (
            [f"缺少 {len(missing_symbols)} 家候选公司的单股证据任务：" + "、".join(missing_symbols[:20])]
            if missing_symbols
            else []
        ),
        "warnings": warnings,
        "candidate_scope": "candidate_collection",
        "screening_mode": "per_security_full_analysis",
        "candidate_count": candidate_count,
        "analyzed_candidate_count": analyzed_count,
        "candidate_coverage_complete": coverage_complete,
        "verdict_counts": counts,
        "positive_company_count": len(passed),
        "items": passed,
        "company_results": company_results,
        "domain_results": domain_results,
        "semantic_artifacts": [artifact],
        "resource_outputs": {
            "security_collection": security_collection,
        },
    }


async def _bind_theme_companies(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    labels = _domain_labels(task)
    candidate_scope = str(task.parameters.get("candidate_scope") or "").strip()
    allowed_symbols = {str(symbol).strip() for symbol in task.symbols if str(symbol).strip()}
    try:
        evidence_context = ThemeEvidenceContext.model_validate(task.parameters.get("evidence_context"))
    except ValidationError as exc:
        return {
            "success": False,
            "partial": False,
            "errors": [f"公司举证任务缺少有效的上位产业命题：{exc}"],
            "items": [],
            "domain_results": [],
            "semantic_artifacts": [],
            "resource_outputs": {"security_collection": []},
        }
    source_warnings = [
        str(error)
        for packet in evidence
        if isinstance(packet.get("result"), Mapping) and packet["result"].get("success") is False
        for error in packet["result"].get("errors") or ["部分公开来源查询失败"]
        if error
    ]
    if not labels:
        return {
            "success": False,
            "partial": False,
            "errors": ["没有取得可执行的语义领域。"],
            "items": [],
            "domain_results": [],
            "semantic_artifacts": [],
            "resource_outputs": {"security_collection": []},
        }
    if candidate_scope not in {"candidate_collection", "public_fallback"}:
        return {
            "success": False,
            "partial": False,
            "errors": ["公司举证任务缺少有效的候选范围契约。"],
            "items": [],
            "domain_results": [],
            "semantic_artifacts": [],
            "resource_outputs": {"security_collection": []},
        }
    if candidate_scope == "candidate_collection" and not allowed_symbols:
        return {
            "success": False,
            "partial": False,
            "errors": ["上游结构化候选公司集合为空，已阻止公开来源自行补股票。"],
            "items": [],
            "domain_results": [],
            "semantic_artifacts": [],
            "resource_outputs": {"security_collection": []},
        }
    if candidate_scope == "candidate_collection":
        return await _analyze_candidate_companies(
            task,
            evidence,
            evidence_context,
            labels,
            llm_cfg,
            completion,
            progress,
        )

    model_call_semaphore = asyncio.Semaphore(12)

    async def limited_completion(**kwargs: Any) -> Any:
        async with model_call_semaphore:
            return await completion(**kwargs)

    packets_by_domain: dict[str, list[dict[str, Any]]] = {}
    observed_symbols_by_domain: dict[str, set[str]] = {}
    source_count_by_domain: dict[str, int] = {}
    for label in labels:
        packets = [
            packet
            for packet in evidence
            if label
            in [
                str(value or "").strip()
                for value in (
                    packet.get("arguments", {}).get("subjects") or []
                    if isinstance(packet.get("arguments"), Mapping)
                    else []
                )
            ]
        ]
        packets_by_domain[label] = packets
        sources = collect_evidence_sources(packets)
        source_count_by_domain[label] = len(sources)
        observed: set[str] = set()
        for source in sources:
            source_text = "\n".join(
                (
                    str(source.get("title") or ""),
                    str(source.get("text") or ""),
                )
            )
            observed.update(
                str(item.get("symbol") or "")
                for item in find_securities_in_text(source_text, limit=100)
                if str(item.get("symbol") or "") in allowed_symbols
            )
        observed_symbols_by_domain[label] = observed

    target_topic = "、".join(evidence_context.target_topics)

    async def bind_domain(label: str) -> tuple[str, list[Any]]:
        packets = packets_by_domain[label]
        domain_thesis = evidence_context.thesis_for(label)
        rationale = domain_thesis.rationale if domain_thesis is not None else ""
        thesis_requirements = [
            (
                f"原文事实必须支持公司正在参与上位产业“{target_topic}”中的"
                f"“{label}”方向；只证明同类产品用于其他终端或无关应用场景，"
                "不能视为满足当前命题"
            ),
            f"公司主体必须是“{label}”相关产品或业务的研发、生产、销售或应用方",
        ]
        if rationale:
            thesis_requirements.append(f"公司事实必须符合该板块的受益逻辑：{rationale}")
        intent = MappingSelectionContext(
            topic=f"{target_topic}中的{label}",
            objective=(
                task.candidate.objective
                + (
                    f"；只核验程序已绑定的 {len(allowed_symbols)} 家候选公司，" "不得从公开来源扩大候选范围"
                    if candidate_scope == "candidate_collection"
                    else "；项目结构化板块无法覆盖，本轮允许从公开来源发现公司"
                )
            ),
            selection_mode="ranked_shortlist",
            thesis_requirements=thesis_requirements,
            research_dimensions=[
                "产品布局",
                "商业应用",
                "送样定点与客户验证",
                "订单",
                "量产与批量交付",
                "相关业务收入",
                "否认与尚未形成收入边界",
            ],
        )
        facts = await bind_company_evidence(
            packets,
            intent,
            llm_cfg,
            completion=limited_completion,
        )
        return label, facts

    semaphore = asyncio.Semaphore(4)

    async def bounded(label: str) -> tuple[str, list[Any]]:
        async with semaphore:
            return await bind_domain(label)

    domain_bindings = await asyncio.gather(
        *(bounded(label) for label in labels),
        return_exceptions=True,
    )
    domain_results: list[dict[str, Any]] = []
    positive_items: list[dict[str, Any]] = []
    boundary_items: list[dict[str, Any]] = []
    errors: list[str] = []
    rejected_outside_candidate_count = 0
    source_observed_symbols: set[str] = set()
    for label, binding in zip(labels, domain_bindings):
        domain_observed = observed_symbols_by_domain.get(label, set())
        source_observed_symbols.update(domain_observed)
        if isinstance(binding, BaseException):
            errors.append(f"{label}：{binding}")
            domain_results.append(
                {
                    "domain": label,
                    "success": False,
                    "company_count": 0,
                    "source_item_count": source_count_by_domain.get(label, 0),
                    "source_observed_candidate_count": len(domain_observed),
                    "errors": [str(binding)],
                }
            )
            continue
        _resolved_label, facts = binding
        accepted = 0
        rejected = 0
        for fact in facts:
            item = {
                **fact.model_dump(),
                "domain": label,
                "matched_domains": [label],
            }
            if candidate_scope == "candidate_collection" and fact.symbol not in allowed_symbols:
                rejected += 1
                rejected_outside_candidate_count += 1
                continue
            if fact.stage == "boundary" or fact.thesis_fit != "exact":
                boundary_items.append(item)
                continue
            positive_items.append(item)
            accepted += 1
        domain_results.append(
            {
                "domain": label,
                "success": True,
                "company_count": accepted,
                "source_item_count": source_count_by_domain.get(label, 0),
                "source_observed_candidate_count": len(domain_observed),
                "rejected_outside_candidate_count": rejected,
                "errors": [],
            }
        )

    stage_rank = {"L3": 3, "L2": 2, "L1": 1}
    companies: dict[str, dict[str, Any]] = {}
    for item in positive_items:
        symbol = str(item.get("symbol") or "")
        company = companies.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": str(item.get("company_name") or symbol),
                "matched_domains": [],
                "strongest_stage": "L1",
            },
        )
        for domain in item.get("matched_domains") or []:
            if domain not in company["matched_domains"]:
                company["matched_domains"].append(domain)
        if stage_rank.get(str(item.get("stage")), 0) > stage_rank.get(str(company["strongest_stage"]), 0):
            company["strongest_stage"] = item["stage"]
    security_collection = sorted(
        companies.values(),
        key=lambda item: (
            -stage_rank.get(str(item["strongest_stage"]), 0),
            -len(item["matched_domains"]),
            item["symbol"],
        ),
    )
    artifact = {
        "type": "theme_company_evidence",
        "domains": labels,
        "target_topics": evidence_context.target_topics,
        "companies": security_collection,
        "candidate_scope": candidate_scope,
    }
    candidate_count = len(allowed_symbols)
    source_observed_candidate_count = len(source_observed_symbols)
    candidate_coverage_complete = (
        candidate_scope != "candidate_collection" or source_observed_candidate_count == candidate_count
    )
    coverage_warning = (
        (
            f"公开来源只逐字提及候选池中的 {source_observed_candidate_count}/"
            f"{candidate_count} 家；当前结果是证据命中 shortlist，"
            "不是完整候选筛选后的唯一剩余公司。"
        )
        if candidate_scope == "candidate_collection" and not candidate_coverage_complete
        else ""
    )
    return {
        "success": bool(positive_items) or not errors,
        "partial": bool(errors or source_warnings or not candidate_coverage_complete),
        "errors": errors,
        "warnings": list(
            dict.fromkeys(
                [
                    *source_warnings,
                    *([coverage_warning] if coverage_warning else []),
                ]
            )
        ),
        "candidate_scope": candidate_scope,
        "screening_mode": "source_hit_shortlist",
        "candidate_count": candidate_count,
        "source_observed_candidate_count": source_observed_candidate_count,
        "not_observed_candidate_count": max(
            0,
            candidate_count - source_observed_candidate_count,
        ),
        "candidate_coverage_complete": candidate_coverage_complete,
        "positive_company_count": len(security_collection),
        "rejected_outside_candidate_count": rejected_outside_candidate_count,
        "items": positive_items,
        "boundary_items": boundary_items,
        "domain_results": domain_results,
        "semantic_artifacts": [artifact],
        "resource_outputs": {
            "security_collection": [{"symbol": item["symbol"], "name": item["name"]} for item in security_collection],
        },
    }


_PROCESSORS = {
    "ranked_domain_selection": _rank_industry_domains,
    "company_evidence_binding": _bind_theme_companies,
}


async def process_task_result(
    processor_name: str,
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    *,
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    processor = _PROCESSORS.get(processor_name)
    if processor is None:
        raise ValueError(f"unknown result processor: {processor_name}")
    if processor_name in {
        "company_evidence_binding",
        "ranked_domain_selection",
    }:
        result = await processor(
            task,
            evidence,
            llm_cfg,
            completion,
            progress,
        )
    else:
        result = await processor(task, evidence, llm_cfg, completion)
    if not isinstance(result, dict):
        raise ValueError(f"{processor_name} returned a non-object result")
    return result


__all__ = ["process_task_result"]
