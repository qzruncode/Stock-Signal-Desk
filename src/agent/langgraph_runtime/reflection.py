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
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.tools.base import citation_scoped_evidence_records, evidence_record_is_eligible

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

    block_index: int | None = Field(default=None, ge=1)
    category: ReflectionCategory = "other"
    severity: ReflectionSeverity = "medium"
    reason: str = Field(min_length=1, description="完整说明当前区块与现有证据的具体矛盾，不得只给半句话。")
    repair_instruction: str = Field(default="", description="revise 时必须给出完整、可执行的修订要求，保留未受影响的事实和引用。")


class ReflectionReview(BaseModel):
    """The only output accepted from the review model."""

    model_config = ConfigDict(extra="forbid")

    verdict: ReflectionVerdict
    summary: str = ""
    issues: list[ReflectionIssue] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def require_actionable_revisions(self) -> ReflectionReview:
        if self.verdict == "revise":
            if not self.issues:
                raise ValueError("revise 结果必须包含至少一个可执行问题")
            for issue in self.issues:
                if issue.block_index is None or not issue.repair_instruction.strip():
                    raise ValueError("revise 问题必须提供 block_index 和完整 repair_instruction")
        return self


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


def _safe_text(value: Any, *, limit: int | None = 1_600) -> str:
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
    """Keep the complete cited PDF body in the critic packet.

    The generic redactor intentionally limits every string to 1,200
    characters. That is appropriate for metadata, but not for a report body
    whose tables can carry the very figures Reflection is checking.
    """
    raw_result = item.get("result")
    if (
        item.get("citation_item") is True
        and str(item.get("tool_name") or "") == "search_knowledge_base"
        and isinstance(raw_result, Mapping)
    ):
        # The same page-level body used by the answering model must reach
        # the reviewer. Generic metadata previews silently cut table rows
        # and made the critic reject values present in the retrieved hit.
        return {
            "document_title": _safe_text(raw_result.get("filename")),
            "page_start": raw_result.get("page_start"),
            "page_end": raw_result.get("page_end") or raw_result.get("page_start"),
            "section": _safe_text(raw_result.get("section")),
            "text": _safe_text(raw_result.get("text") or raw_result.get("snippet"), limit=None),
        }
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
    eligible = [
        dict(item)
        for item in state.get("evidence") or []
        if isinstance(item, Mapping)
        and evidence_record_is_eligible(item)
        and str(item.get("effect") or "read") != "side_effect"
    ]
    # Reuse the citation boundary already used by answer resolution. Search
    # envelopes are not the page-level IDs the answer actually references.
    evidence_by_id = {
        str(item.get("evidence_id") or item.get("id") or ""): item
        for item in citation_scoped_evidence_records(eligible)
    }
    return list(evidence_by_id.values())


def reflection_eligibility(
    state: Mapping[str, Any],
    answer: Any,
) -> tuple[bool, dict[str, Any]]:
    """Return whether a validated candidate needs semantic review."""
    candidate = structured_answer_mapping(answer)
    if not candidate:
        return False, {"reason": "no_structured_answer"}
    if state.get("orchestrator_mode") == "multi_agent_worker":
        # The worker produces an internal typed handoff. Team's independent
        # coverage/critic review owns its semantic assessment; the shared
        # final-answer reflector runs only at the publication boundary.
        return False, {"reason": "team_worker_handoff"}
    profile = structured_answer_profile(candidate)
    evidence = _eligible_evidence(state)
    blocks = structured_answer_blocks(candidate)
    knowledge_base_answer = any(item.get("tool_name") == "search_knowledge_base" for item in evidence)
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
    material_indices = [
        index
        for index, block in enumerate(blocks, start=1)
        if str(block.get("kind") or "fact").strip().lower() in REFLECTION_MATERIAL_KINDS
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
    return [aliases[str(value)] for value in raw_ids if str(value) in aliases]


def build_reflection_packet(
    *,
    state: Mapping[str, Any],
    answer: Any,
) -> dict[str, Any]:
    """Build the complete candidate and citation-scoped critic packet."""
    candidate = structured_answer_mapping(answer)
    evidence = _eligible_evidence(state)
    aliases = _evidence_aliases(evidence)
    blocks = structured_answer_blocks(candidate)
    packet_blocks = []
    for index, block in enumerate(blocks, start=1):
        packet_blocks.append(
            {
                "index": index,
                "section": _safe_text(block.get("section"), limit=160),
                "kind": _safe_text(block.get("kind"), limit=32),
                "content": _safe_text(block.get("content"), limit=None),
                "evidence_aliases": _block_evidence_aliases(block, aliases),
            }
        )
    packet_evidence = []
    for item in evidence:
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
                "text": _safe_text(claim.get("text"), limit=None),
                "kind": _safe_text(claim.get("kind"), limit=32),
                "evidence_aliases": claim_aliases,
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
        "claim_audit": claims,
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
        "revise 的每项问题都必须填写候选中的 block_index，并完整说明原表述为何不成立以及如何修正；"
        "不得在引述、原因或修订要求中间停笔，不得仅以‘将’或‘补充’结束。字符串中的引号必须按 JSON 协议转义。"
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
        if review.verdict == "revise" and (index is None or index > block_count):
            raise ValueError("revise 问题必须指向当前答案中实际存在的区块")
        projected_issues.append(
            {
                "block_index": index if index is not None and index <= block_count else None,
                "category": issue.category,
                "severity": issue.severity,
                "reason": _safe_text(issue.reason, limit=None),
                "repair_instruction": _safe_text(issue.repair_instruction, limit=None),
            }
        )
    summary = _safe_text(review.summary, limit=None)
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
        "尤其不得删除已核验的事实、数字、表格数据行、页码或来源引用；只改动修订要求明确指出的部分。"
        "未被问题指出的 blocks 必须逐字保留；若只要求修订一句话，只替换该句，不要重写整份答案或只输出表头。"
    ]
    for issue in list(review.get("issues") or [])[:12]:
        if not isinstance(issue, Mapping):
            continue
        block = issue.get("block_index")
        location = f"第 {block} 个区块" if type(block) is int else "相关区块"
        category = _CATEGORY_LABELS.get(str(issue.get("category") or "other"), "其他")
        severity = str(issue.get("severity") or "medium")
        reason = _safe_text(issue.get("reason"), limit=None)
        instruction = _safe_text(issue.get("repair_instruction"), limit=None)
        lines.append(f"- {location} [{category}/{severity}] {reason}。修订要求：{instruction or '收窄表述并保留证据边界。'}")
    return "\n".join(lines)


_EXACT_DELETE_PHRASE = re.compile(
    r"(?:删除|去掉|移除|删去)\s*[“\"「『](.{1,80}?)[”\"」』]\s*(?:字样|一词|词语)"
)
_EXACT_LITERAL_REPLACEMENT = re.compile(
    r"(?:将|把).{0,120}?[“「『\"](?P<old>[^“”「」『』\"']{1,100})[”」』\"]"
    r"\s*(?:改为|改成|替换为)\s*[“「『\"](?P<new>[^“”「」『』\"']{1,100})[”」』\"]"
)
_EXACT_UNSUPPORTED_TABLE_LABEL = re.compile(
    r"(?:命名为|称为|表名为)\s*[“「『](.{1,80}?)[”」』]\s*表"
)
_EXACT_NEUTRAL_PAGE_TABLE = re.compile(
    r"(?:直接写|例如|如)\s*[“「『]第\s*(\d+)\s*页表格[”」』]"
)


def _replace_exact_page_table_label(
    content: str,
    issue: Mapping[str, Any],
) -> str | None:
    """Use an explicit reviewer-approved neutral page label for one table."""
    unsupported = _EXACT_UNSUPPORTED_TABLE_LABEL.search(str(issue.get("reason") or ""))
    neutral = _EXACT_NEUTRAL_PAGE_TABLE.search(str(issue.get("repair_instruction") or ""))
    if unsupported is None or neutral is None:
        return None
    old_label = unsupported.group(1).strip()
    page = neutral.group(1)
    reason_page = re.search(r"第\s*(\d+)\s*页", str(issue.get("reason") or ""))
    if not old_label or not reason_page or reason_page.group(1) != page:
        return None

    # Replace only references to this exact, unsupported table title on the
    # page named by the reviewer. Keep values, citations, and all other prose
    # byte-for-byte; reject partial/ambiguous matches.
    pattern = re.compile(
        rf"(?:第\s*{re.escape(page)}\s*页\s*)?"
        rf"[“「『]{re.escape(old_label)}[”」』]\s*表格?"
        rf"(?:\s*[（(]\s*第\s*{re.escape(page)}\s*页\s*[）)])?"
    )
    matches = list(pattern.finditer(content))
    quoted_mentions = re.findall(
        rf"[“「『]{re.escape(old_label)}[”」』]\s*表格?",
        content,
    )
    if not matches or len(matches) != len(quoted_mentions):
        return None
    revised, count = pattern.subn(f"第{page}页表格", content)
    return revised if count == len(matches) else None


def apply_exact_low_severity_repairs(
    answer: Any,
    review: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Apply only exact, low-risk wording repairs requested by the reviewer.

    Full-answer rewrites can damage already validated tables and citations. A
    narrowly quoted deletion or an exact neutral table-label replacement can
    instead preserve every other block byte for byte. A literal replacement is
    accepted only when the reviewer names both exact phrases and the old phrase
    occurs once in the targeted block; ambiguous instructions still use the
    normal bounded revision path.
    """
    candidate = structured_answer_mapping(answer)
    issues = review.get("issues")
    if (
        not candidate
        or str(review.get("verdict") or "") != "revise"
        or not isinstance(issues, Sequence)
        or isinstance(issues, (str, bytes, bytearray))
        or not issues
    ):
        return None

    blocks = structured_answer_blocks(candidate)
    for issue in issues:
        if (
            not isinstance(issue, Mapping)
            or str(issue.get("severity") or "").strip().lower() != "low"
            or str(issue.get("category") or "").strip().lower() != "evidence_scope"
        ):
            return None
        block_index = issue.get("block_index")
        if type(block_index) is not int or not 1 <= block_index <= len(blocks):
            return None
        match = _EXACT_DELETE_PHRASE.search(str(issue.get("repair_instruction") or ""))
        content = blocks[block_index - 1].get("content")
        if not isinstance(content, str):
            return None
        if match is not None:
            phrase = match.group(1).strip()
            if not phrase or content.count(phrase) != 1:
                return None
            revised_content = content.replace(phrase, "", 1)
        else:
            literal = _EXACT_LITERAL_REPLACEMENT.search(
                str(issue.get("repair_instruction") or "")
            )
            if literal is not None:
                old = literal.group("old").strip()
                new = literal.group("new").strip()
                if not old or not new or old == new or content.count(old) != 1:
                    return None
                revised_content = content.replace(old, new, 1)
            else:
                revised_content = _replace_exact_page_table_label(content, issue)
            if revised_content is None:
                return None
        if not revised_content.strip():
            return None
        blocks[block_index - 1]["content"] = revised_content

    revised = dict(candidate)
    revised["blocks"] = blocks
    return revised if revised != candidate else None


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
                    "reason": _safe_text(item.get("reason"), limit=None),
                    "repair_instruction": _safe_text(item.get("repair_instruction"), limit=None),
                }
            )
    return {
        "verdict": str(value.get("verdict") or "")[:16],
        "summary": _safe_text(value.get("summary"), limit=None),
        "issues": issues,
    }


__all__ = [
    "REFLECTION_MATERIAL_KINDS",
    "REFLECTION_MAX_CRITIC_CALLS",
    "REFLECTION_MAX_REVISIONS",
    "ReflectionIssue",
    "ReflectionReview",
    "apply_exact_low_severity_repairs",
    "build_reflection_packet",
    "normalize_reflection_review",
    "reflection_eligibility",
    "reflection_feedback",
    "reflection_messages",
    "reflection_review_projection",
]
