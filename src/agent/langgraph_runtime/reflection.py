"""Bounded semantic review for evidence-backed answers.

Reflection is deliberately narrower than evidence validation.  The existing
runtime remains responsible for source eligibility, citation integrity, time
scope, and content access.  This module only gives a reviewer a redacted
candidate/evidence packet and normalizes the review decision before the answer
can be published.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from src.tools.base import evidence_record_is_eligible

from .answer_contract import (
    structured_answer_blocks,
    structured_answer_mapping,
    structured_answer_profile,
)
from .content_access import DOCUMENT_BODY_PREVIEW_CHARACTERS


ReflectionVerdict = Literal["pass", "revise", "block"]
ReflectionCategory = Literal[
    "reasoning",
    "evidence_scope",
    "entity_scope",
    "time_scope",
    "risk",
    "completeness",
    "clarity",
    "other",
]
ReflectionSeverity = Literal["low", "medium", "high"]


class ReflectionIssue(BaseModel):
    """One bounded, actionable issue found by the semantic reviewer."""

    model_config = ConfigDict(extra="forbid")

    block_index: int | None = Field(default=None, ge=1, le=80)
    category: ReflectionCategory = "other"
    severity: ReflectionSeverity = "medium"
    reason: str = Field(min_length=1, max_length=600)
    repair_instruction: str = Field(default="", max_length=600)


class ReflectionReview(BaseModel):
    """The only output accepted from the review model."""

    model_config = ConfigDict(extra="forbid")

    verdict: ReflectionVerdict
    summary: str = Field(default="", max_length=800)
    issues: list[ReflectionIssue] = Field(default_factory=list, max_length=12)


REFLECTION_MAX_CRITIC_CALLS = 2
REFLECTION_MAX_REVISIONS = 1
# Fact-only answers are already covered by the deterministic evidence ledger.
# Reflection is useful when the answer makes a judgment or states a risk.
REFLECTION_MATERIAL_KINDS = frozenset({"inference", "recommendation", "risk"})

_URL_PATTERN = re.compile(r"https?://[^\s)\]}>,]+", re.IGNORECASE)
_LOCAL_PATH_PATTERN = re.compile(
    r"(?:/(?:Users|private|var|tmp|Volumes)/[^\s)\]}>,]+|[A-Za-z]:\\[^\s)\]}>,]+)",
    re.IGNORECASE,
)
_EVIDENCE_ID_PATTERN = re.compile(r"\bev_[A-Za-z0-9_.:-]+\b")
_MARKDOWN_TABLE = re.compile(
    r"(?m)^\s*\|[^\n]*\|\s*\r?\n\s*\|(?:\s*:?-{3,}:?\s*\|)+"
)
_TABLE_FORMAT_REQUEST = re.compile(
    r"(?:用|以|按|整理成|做成|制成|列成|给我(?:一个|一张|个)?)\s*"
    r"(?:markdown\s*)?(?:对比|比较|对照)?(?:表格|表)"
    r"|(?:对比|比较|对照)表(?:格)?(?:形式|展示|呈现|输出|列出)?"
    r"|\b(?:in|as)\s+(?:a\s+)?table\b|\btable\s+format\b",
    re.IGNORECASE,
)
_HIDDEN_KEYS = frozenset(
    {
        "url",
        "urls",
        "source_refs",
        "reference_links",
        "reference_urls",
        "download_url",
        "preview_url",
        "path",
        "file_path",
        "filename",
        "file_name",
        "command",
        "script",
        "artifact_refs",
        "chart_refs",
        "action_refs",
        "source_id",
        "evidence_id",
    }
)


def _safe_text(value: Any, *, limit: int = 1_600) -> str:
    text = str(value or "")
    text = _URL_PATTERN.sub("[链接已隐藏]", text)
    text = _LOCAL_PATH_PATTERN.sub("[路径已隐藏]", text)
    text = _EVIDENCE_ID_PATTERN.sub("[证据编号已隐藏]", text)
    return text[:limit]


def _safe_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Keep useful observations while removing transport and executable data."""
    if depth >= 5:
        return "[详情已截断]"
    normalized_key = str(key or "").strip().lower()
    if normalized_key in _HIDDEN_KEYS or normalized_key.endswith("_url"):
        return "[已隐藏]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value, limit=1_200)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in list(value.items())[:24]:
            child_key = str(raw_key)[:96]
            if child_key.lower() in _HIDDEN_KEYS or child_key.lower().endswith("_url"):
                continue
            result[child_key] = _safe_value(child, key=child_key, depth=depth + 1)
        if len(value) > 24:
            result["_truncated"] = True
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [_safe_value(item, depth=depth + 1) for item in list(value)[:16]]
        if len(value) > 16:
            items.append("[其余内容已截断]")
        return items
    return _safe_text(value, limit=600)


def _safe_evidence_observation(item: Mapping[str, Any]) -> Any:
    """Keep a complete bounded PDF body in the critic packet.

    The generic redactor intentionally limits every string to 1,200
    characters. That is appropriate for metadata, but not for a report body
    whose tables can carry the very figures Reflection is checking.
    """
    raw_result = item.get("result")
    observation = _safe_value(raw_result or {}, key="result")
    if (
        str(item.get("tool_name") or "") != "read_web_source"
        or not isinstance(raw_result, Mapping)
        or not isinstance(observation, dict)
    ):
        return observation
    content = raw_result.get("content")
    if not isinstance(content, str):
        return observation
    truncated = len(content) > DOCUMENT_BODY_PREVIEW_CHARACTERS
    observation["content"] = _safe_text(
        content,
        limit=DOCUMENT_BODY_PREVIEW_CHARACTERS,
    ) + ("…[正文预览已截断]" if truncated else "")
    observation["content_length"] = len(content)
    observation["content_preview_truncated"] = truncated
    return observation


def _eligible_evidence(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in state.get("evidence") or []
        if isinstance(item, Mapping)
        and evidence_record_is_eligible(item)
        and str(item.get("effect") or "read") != "side_effect"
    ]


def reflection_eligibility(
    state: Mapping[str, Any],
    answer: Any,
) -> tuple[bool, dict[str, Any]]:
    """Return whether a validated candidate needs semantic review."""
    candidate = structured_answer_mapping(answer)
    if not candidate:
        return False, {"reason": "no_structured_answer"}
    profile = structured_answer_profile(candidate)
    evidence = _eligible_evidence(state)
    blocks = structured_answer_blocks(candidate)
    knowledge_base_answer = bool(state.get("knowledge_base_ids"))
    if profile != "research" and not knowledge_base_answer:
        return False, {"reason": "general_profile", "profile": profile}
    status = str(state.get("status") or "").strip().lower()
    if status and status != "completed":
        return False, {"reason": "not_completed", "status": status}
    if state.get("work_budget_exhausted"):
        return False, {"reason": "work_budget_exhausted"}
    pending = [
        item
        for item in state.get("pending_content_reads") or []
        if isinstance(item, Mapping)
    ]
    if pending or str(state.get("content_access_feedback") or "").strip():
        return False, {"reason": "content_access_pending", "pending_count": len(pending)}
    if not evidence:
        return False, {"reason": "no_eligible_evidence", "evidence_count": 0}
    material_kinds = REFLECTION_MATERIAL_KINDS | (
        frozenset({"answer", "fact"}) if knowledge_base_answer else frozenset()
    )
    material_indices = [
        index
        for index, block in enumerate(blocks, start=1)
        if str(block.get("kind") or "fact").strip().lower() in material_kinds
    ]
    if not material_indices:
        return False, {
            "reason": "no_judgment_blocks",
            "evidence_count": len(evidence),
            "material_block_indices": [],
        }
    return True, {
        "reason": "knowledge_base_answer_blocks_present" if knowledge_base_answer else "judgment_blocks_present",
        "profile": profile,
        "evidence_count": len(evidence),
        "material_block_indices": material_indices,
        "pending_count": 0,
        "knowledge_base_answer": knowledge_base_answer,
    }


def unrequested_knowledge_table_review(
    state: Mapping[str, Any],
    answer: Any,
) -> dict[str, Any] | None:
    """Require prose for PDF answers unless the user asks for a table.

    Markdown comparison tables make it easy to imply a symmetric capability
    that the document never states. The bounded revision keeps the answer
    source-grounded without banning tables when the user explicitly requests
    that presentation.
    """
    candidate = structured_answer_mapping(answer)
    evidence = _eligible_evidence(state)
    if not state.get("knowledge_base_ids"):
        return None
    question = str(state.get("user_text") or "")
    if _TABLE_FORMAT_REQUEST.search(question):
        return None
    block_indexes = [
        index
        for index, block in enumerate(structured_answer_blocks(candidate), start=1)
        if _MARKDOWN_TABLE.search(str(block.get("content") or ""))
    ]
    if not block_indexes:
        return None
    return {
        "verdict": "revise",
        "summary": "用户未要求表格；将回答改为平行要点，避免表格补齐文档未说明的比较维度。",
        "issues": [
            {
                "block_index": index,
                "category": "reasoning",
                "severity": "medium",
                "reason": "PDF 回答包含用户未要求的 Markdown 表格，行标题可能暗示来源未说明的对称能力。",
                "repair_instruction": "删除表格，改用对齐的分点描述；只陈述 PDF 明确说明的能力，未说明的一侧标为书中未说明，不要据此推断。",
            }
            for index in block_indexes[:12]
        ],
    }


def _evidence_aliases(evidence: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
        if evidence_id and evidence_id not in aliases:
            aliases[evidence_id] = f"e{len(aliases) + 1}"
    return aliases


def _block_evidence_aliases(block: Mapping[str, Any], aliases: Mapping[str, str]) -> list[str]:
    raw_ids = block.get("evidence_ids") or []
    if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes, bytearray)):
        return []
    return [aliases[str(value)] for value in raw_ids if str(value) in aliases][:24]


def build_reflection_packet(
    *,
    state: Mapping[str, Any],
    answer: Any,
) -> dict[str, Any]:
    """Build the bounded packet supplied to the critic/refiner."""
    candidate = structured_answer_mapping(answer)
    evidence = _eligible_evidence(state)
    aliases = _evidence_aliases(evidence)
    blocks = structured_answer_blocks(candidate)
    packet_blocks = []
    for index, block in enumerate(blocks[:80], start=1):
        packet_blocks.append(
            {
                "index": index,
                "section": _safe_text(block.get("section"), limit=160),
                "kind": _safe_text(block.get("kind"), limit=32),
                "content": _safe_text(block.get("content"), limit=2_400),
                "evidence_aliases": _block_evidence_aliases(block, aliases),
            }
        )
    packet_evidence = []
    for item in evidence[:32]:
        evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
        packet_evidence.append(
            {
                "alias": aliases.get(evidence_id, f"e{len(packet_evidence) + 1}"),
                "tool_name": _safe_text(item.get("tool_name"), limit=120),
                "data_time": _safe_text(item.get("data_time"), limit=80),
                "data_time_provenance": _safe_text(item.get("data_time_provenance"), limit=80),
                "entities": _safe_value(item.get("entities") or {}),
                "observation": _safe_evidence_observation(item),
            }
        )
    claims = []
    for index, claim in enumerate(state.get("claim_evidence") or [], start=1):
        if not isinstance(claim, Mapping):
            continue
        raw_ids = claim.get("evidence_ids") or claim.get("evidenceIds") or []
        claim_aliases = (
            [aliases[str(value)] for value in raw_ids if str(value) in aliases]
            if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes, bytearray))
            else []
        )
        claims.append(
            {
                "index": index,
                "text": _safe_text(claim.get("text"), limit=1_200),
                "kind": _safe_text(claim.get("kind"), limit=32),
                "evidence_aliases": claim_aliases[:24],
                "checks": _safe_value(claim.get("checks") or {}),
            }
        )
    return {
        "user_question": _safe_text(state.get("user_text"), limit=2_400),
        "candidate": {
            "profile": structured_answer_profile(candidate),
            "title": _safe_text(candidate.get("title"), limit=240),
            "blocks": packet_blocks,
        },
        "evidence": packet_evidence,
        "claim_audit": claims[:80],
        "rules": {
            "evidence_aliases_are_not_real_ids": True,
            "review_only_no_new_research": True,
            "material_block_kinds": sorted(REFLECTION_MATERIAL_KINDS),
        },
    }


def reflection_messages(packet: Mapping[str, Any]) -> list[Any]:
    """Return the critic prompt without exposing raw evidence identities."""
    system = (
        "你是通用证据驱动回答的语义复核器，不是回答者。"
        "只检查候选 StructuredAgentAnswer 与提供的证据观察是否一致，重点检查事实是否超出原文、推断是否越过证据边界、"
        "实体和时间范围是否一致，以及回答是否完整清楚。比较层级或方案时，不得从一方未提及某项能力反推出另一方具备该能力；"
        "如果候选含表格，必须逐行逐格核对：每个单元格的主体与能力都要有独立的原文支持，同一行另一侧的证据不能代替该单元格的证据。"
        "证据没有描述的维度应标为来源未说明，不得用常识补成文档事实。现有证据与硬校验已经通过；不要重新取证、不要调用工具、不要写最终答案。"
        "证据只使用 e1、e2 等匿名别名，禁止输出真实 evidence_id、URL、本机路径、文件名、命令或可执行内容。"
        "如果候选可以发布，返回 pass；如果可以基于已有证据修正，返回 revise 并给出具体区块和修订要求；"
        "如果关键结论无法由现有证据支持，返回 block。只能返回符合 ReflectionReview 结构的结果。"
    )
    return [
        SystemMessage(content=system),
        HumanMessage(
            content=json.dumps(packet, ensure_ascii=False, default=str, separators=(",", ":"))
        ),
    ]


def normalize_reflection_review(value: Any, *, block_count: int) -> dict[str, Any]:
    """Validate and project a review result; malformed reviews fail closed."""
    if isinstance(value, ReflectionReview):
        review = value
    elif isinstance(value, Mapping):
        review = ReflectionReview.model_validate(value)
    else:
        raise ValueError("ReflectionReview 不是有效对象")
    projected_issues: list[dict[str, Any]] = []
    for issue in review.issues[:12]:
        index = issue.block_index
        projected_issues.append(
            {
                "block_index": index if index is not None and index <= block_count else None,
                "category": issue.category,
                "severity": issue.severity,
                "reason": _safe_text(issue.reason, limit=600),
                "repair_instruction": _safe_text(issue.repair_instruction, limit=600),
            }
        )
    summary = _safe_text(review.summary, limit=800)
    if review.verdict == "revise" and not projected_issues:
        raise ValueError("revise 结果必须包含至少一个可执行问题")
    if review.verdict == "block" and not projected_issues and not summary:
        raise ValueError("block 结果必须说明阻断原因")
    return {"verdict": review.verdict, "summary": summary, "issues": projected_issues}


_CATEGORY_LABELS = {
    "reasoning": "推理范围",
    "evidence_scope": "证据范围",
    "entity_scope": "实体范围",
    "time_scope": "时间范围",
    "risk": "风险表达",
    "completeness": "完整性",
    "clarity": "清晰度",
    "other": "其他",
}


def reflection_feedback(review: Mapping[str, Any]) -> str:
    """Turn a review into a bounded no-tool revision instruction."""
    lines = [
        "语义复核要求修订当前 StructuredAgentAnswer。只基于已有证据重写完整 blocks，"
        "不要调用工具、不要新增事实、不要输出 URL/路径/命令，并保留仍然正确的内容和 source_ids。"
    ]
    for issue in list(review.get("issues") or [])[:12]:
        if not isinstance(issue, Mapping):
            continue
        block = issue.get("block_index")
        location = f"第 {block} 个区块" if type(block) is int else "相关区块"
        category = _CATEGORY_LABELS.get(str(issue.get("category") or "other"), "其他")
        severity = str(issue.get("severity") or "medium")
        reason = _safe_text(issue.get("reason"), limit=500)
        instruction = _safe_text(issue.get("repair_instruction"), limit=500)
        lines.append(f"- {location} [{category}/{severity}] {reason}。修订要求：{instruction or '收窄表述并保留证据边界。'}")
    return "\n".join(lines)[:7_000]


def reflection_review_projection(review: Any) -> dict[str, Any]:
    """Project review data for traces and clients without raw model payloads."""
    if isinstance(review, ReflectionReview):
        value = review.model_dump(mode="python")
    elif isinstance(review, Mapping):
        value = dict(review)
    else:
        return {}
    issues = []
    raw_issues = value.get("issues")
    if isinstance(raw_issues, Sequence) and not isinstance(raw_issues, (str, bytes, bytearray)):
        for item in list(raw_issues)[:12]:
            if not isinstance(item, Mapping):
                continue
            issues.append(
                {
                    "block_index": item.get("block_index") if type(item.get("block_index")) is int else None,
                    "category": str(item.get("category") or "other")[:32],
                    "severity": str(item.get("severity") or "medium")[:16],
                    "reason": _safe_text(item.get("reason"), limit=600),
                    "repair_instruction": _safe_text(item.get("repair_instruction"), limit=600),
                }
            )
    return {
        "verdict": str(value.get("verdict") or "")[:16],
        "summary": _safe_text(value.get("summary"), limit=800),
        "issues": issues,
    }


__all__ = [
    "REFLECTION_MATERIAL_KINDS",
    "REFLECTION_MAX_CRITIC_CALLS",
    "REFLECTION_MAX_REVISIONS",
    "ReflectionIssue",
    "ReflectionReview",
    "build_reflection_packet",
    "normalize_reflection_review",
    "reflection_eligibility",
    "reflection_feedback",
    "reflection_messages",
    "reflection_review_projection",
    "unrequested_knowledge_table_review",
]
