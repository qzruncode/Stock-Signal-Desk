# -*- coding: utf-8 -*-
"""Shared contracts and deterministic helpers for semantic result processors."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Callable, Awaitable, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.task_workflows import ResolvedTask, ResultSelectionMode, ResultSelectionSpec


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
    evidence: list[CompanyThemeEvidenceReference] = Field(default_factory=list, max_length=6)
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
                            "tier": {"type": "integer", "minimum": 1, "maximum": 4},
                            "rationale": {"type": "string"},
                            "support_quote": {"type": "string"},
                            "source_id": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["label", "tier", "rationale", "support_quote", "source_id", "confidence"],
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


def response_payload(response: Any, function_name: str) -> dict[str, Any]:
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


def normalized_text(value: str) -> str:
    return re.sub(r"[\s*_`#>]+", "", value).replace("／", "/").lower()


def domain_labels(task: ResolvedTask) -> list[str]:
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


def result_selection(task: ResolvedTask) -> ResultSelectionSpec:
    selection = task.result_selection
    if selection is None:
        raise ValueError(f"{task.kind.value} result processor requires result_selection")
    return selection


def selection_payload(selection: ResultSelectionSpec) -> dict[str, Any]:
    return {"mode": selection.mode.value, "max_items": selection.max_items}


def apply_result_selection(items: list[dict[str, Any]], selection: ResultSelectionSpec) -> list[dict[str, Any]]:
    if selection.mode == ResultSelectionMode.ALL_RELEVANT:
        return items
    return items[: int(selection.max_items or 0)]


def catalog_result(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    for packet in evidence:
        if (
            isinstance(packet, Mapping)
            and packet.get("tool") == "get_domain_board_catalog"
            and isinstance(packet.get("result"), Mapping)
        ):
            return dict(packet["result"])
    return None


def project_board_snapshot_id(boards: list[dict[str, Any]], _catalog: Mapping[str, Any]) -> str:
    payload = sorted(
        ({"name": board["name"], "sector_code": board["sector_code"]} for board in boards),
        key=lambda item: (item["name"], item["sector_code"]),
    )
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:20]


def project_board_cache_key(task: ResolvedTask, llm_cfg: Mapping[str, Any], snapshot_id: str) -> str | None:
    if not llm_cfg.get("api_base"):
        return None
    payload = {
        "version": PROJECT_BOARD_CACHE_VERSION,
        "model": str(llm_cfg.get("model") or ""),
        "snapshot_id": snapshot_id,
        "topic": normalized_text(str(task.parameters.get("query") or task.candidate.objective)),
        "root_topics": [normalized_text(value) for value in domain_labels(task)],
        "result_selection": selection_payload(result_selection(task)),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"project_board_ranking:{PROJECT_BOARD_CACHE_VERSION}:{digest}"


def load_project_board_ranking_cache(cache_key: str | None) -> dict[str, Any] | None:
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
        return {**payload, "cache_hit": True}
    except Exception:
        logger.warning("[ResultProcessor] ignored invalid project-board ranking cache", exc_info=True)
        return None


def save_project_board_ranking_cache(cache_key: str | None, result: Mapping[str, Any]) -> None:
    if (
        not cache_key
        or result.get("success") is not True
        or result.get("coverage_complete") is not True
        or result.get("ranking_complete") is not True
    ):
        return
    try:
        from src.storage.manager import DatabaseManager

        payload = json.dumps(dict(result), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        DatabaseManager.get_instance().save_tool_cache(cache_key, payload)
    except Exception:
        logger.warning("[ResultProcessor] failed to persist project-board ranking cache", exc_info=True)


def semantic_failure_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "semantic_provider_timeout"
    if isinstance(exc, (json.JSONDecodeError, ValidationError, ValueError)):
        return "semantic_invalid_response"
    return "semantic_provider_error"


__all__ = [
    "Completion",
    "ProcessorProgress",
    "PROJECT_BOARD_CACHE_VERSION",
    "CompanyThemeAnalysisCandidate",
    "RankedDomainCandidate",
    "RankedProjectBoard",
    "RankedProjectBoardSubmission",
    "_COMPANY_THEME_ANALYSIS_SYSTEM_PROMPT",
    "_PROJECT_BOARD_RANKING_SYSTEM_PROMPT",
    "_PROJECT_BOARD_RANKING_TOOL",
    "_RANKED_DOMAIN_SYSTEM_PROMPT",
    "_RANKED_DOMAIN_TOOL",
    "apply_result_selection",
    "catalog_result",
    "domain_labels",
    "load_project_board_ranking_cache",
    "normalized_text",
    "project_board_cache_key",
    "project_board_snapshot_id",
    "response_payload",
    "result_selection",
    "save_project_board_ranking_cache",
    "selection_payload",
    "semantic_failure_code",
]
