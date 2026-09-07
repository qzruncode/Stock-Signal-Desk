"""Structured answer contract for the native LangChain Agent loop.

The model produces a typed collection of answer blocks instead of a free-form
Markdown document that the runtime must parse back into claims.  Markdown is
still the client-facing representation, but the block metadata is the source
of truth for claim scope, kind, and evidence references.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from src.tools.base import evidence_record_is_eligible

from .evidence_identity import canonicalize_evidence_markers, resolve_evidence_id


AnswerBlockKind = Literal[
    "context",
    "fact",
    "inference",
    "recommendation",
    "risk",
    "disclaimer",
]


class StructuredAnswerBlock(TypedDict):
    """One independently scoped block in the final user-facing answer."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    section: Annotated[
        str,
        Field(
            default="",
            max_length=160,
            description="Optional section label. This is presentation metadata, not a factual claim.",
        ),
    ]
    kind: Annotated[
        AnswerBlockKind,
        Field(
            default="fact",
            description=(
                "Block semantics: context/disclaimer may be used without external evidence; "
                "fact/inference/recommendation/risk require supporting source_ids."
            ),
        ),
    ]
    content: Annotated[
        str,
        Field(
            min_length=1,
            max_length=24_000,
            description=(
                "Markdown body for this block. Keep one coherent evidence scope per block; "
                "put source references in source_ids instead of embedding evidence markers here."
            ),
        ),
    ]
    source_ids: Annotated[
        list[Annotated[int, Field(strict=True, ge=1)]],
        Field(
            default_factory=list,
            max_length=24,
            description=(
                "Integer source_ids from the current run's source catalog supporting this block. "
                "The server resolves these numbers to durable evidence IDs. Never write ev_ hashes."
            ),
        ),
    ]


class ResearchConclusion(TypedDict):
    """Optional business annotation of an explicit, already visible judgment."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    symbol: Annotated[str, Field(min_length=1, max_length=16)]
    verdict: Literal["buy", "not_buy", "watch", "avoid"]
    block_indices: Annotated[list[Annotated[int, Field(strict=True, ge=1)]], Field(min_length=1, max_length=12)]


class StructuredAgentAnswer(TypedDict):
    """The only model-owned payload accepted as a new run's final answer."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    title: Annotated[
        str,
        Field(
            default="",
            max_length=240,
            description="Optional short title. Do not put factual claims only in the title.",
        ),
    ]
    research: Annotated[
        list[ResearchConclusion],
        Field(default_factory=list, max_length=30, description=(
            "Archive each stock research judgment explicitly present in the answer. Empty for general questions. "
            "Never invent a trading recommendation just to create an annotation. "
            "symbol is the exact security code. block_indices are one-based indices of the supporting "
            "inference/recommendation/risk blocks, including invalidation conditions where available. "
            "Use watch unless the visible answer explicitly states buy, not_buy, or avoid."
        )),
    ]
    blocks: Annotated[
        list[StructuredAnswerBlock],
        Field(
            min_length=1,
            max_length=80,
            description=(
                "Complete ordered answer blocks. Include every material fact, conclusion, "
                "risk, and recommendation here; do not leave material content only in title text."
            ),
        ),
    ]


# ``ToolStrategy`` registers this typed schema as a normal model tool.  Keep
# the name derived from the schema rather than duplicating a string in the
# middleware, so call-id tracking remains aligned with LangChain's binding.
STRUCTURED_OUTPUT_TOOL_NAME = StructuredAgentAnswer.__name__


def structured_answer_mapping(value: Any) -> dict[str, Any]:
    """Convert LangChain's Pydantic response or a checkpoint dict to plain data."""
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="python")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def structured_answer_blocks(value: Any) -> list[dict[str, Any]]:
    """Return valid block mappings without exposing Pydantic objects downstream."""
    answer = structured_answer_mapping(value)
    raw_blocks = answer.get("blocks")
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in raw_blocks if isinstance(item, Mapping)]


def evidence_source_catalog(evidence: Iterable[Any]) -> list[dict[str, Any]]:
    """Number append-only checkpoint slots, not hashes authored by the model.

    Slots are assigned before eligibility filtering so an updated/rejected
    observation cannot renumber another source during repair or resume.
    No secondary counter or parallel-tool state channel is needed.
    """
    catalog = []
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, Mapping) or not evidence_record_is_eligible(item):
            continue
        if str(item.get("effect") or "read") == "side_effect":
            continue
        evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
        if evidence_id:
            catalog.append({
                "source_id": index,
                "evidence_id": evidence_id,
                "tool_name": item.get("tool_name"),
                "data_time": item.get("data_time"),
                "source_refs": item.get("source_refs") or [],
            })
    return catalog


def resolve_answer_sources(value: Any, evidence: Iterable[Any]) -> dict[str, Any]:
    """Resolve native typed citations once at the application boundary.

    Historical/checkpoint answers already contain canonical ``evidence_ids``.
    Unknown numbers remain explicit unresolved references for the existing
    claim validator; they are never guessed, dropped, or treated as evidence.
    """
    answer = structured_answer_mapping(value)
    if not answer:
        return answer
    sources = {item["source_id"]: item["evidence_id"] for item in evidence_source_catalog(evidence)}
    blocks = structured_answer_blocks(answer)
    for block in blocks:
        if "source_ids" in block:
            block["evidence_ids"] = [
                sources.get(source_id, f"source:{source_id}")
                for source_id in block["source_ids"]
            ]
    return {**answer, "blocks": blocks}


def _canonical_ids(evidence: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        if isinstance(item, Mapping):
            value = str(item.get("evidence_id") or item.get("id") or "").strip()
        else:
            value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _resolved_ids(values: Iterable[Any], available: Sequence[str]) -> list[str]:
    resolved: list[str] = []
    for value in values:
        canonical = resolve_evidence_id(value, available)
        if canonical and canonical not in resolved:
            resolved.append(canonical)
    return resolved


def render_structured_answer(
    value: Any,
    evidence: Iterable[Any] = (),
) -> str:
    """Render the typed answer while making block evidence visible to the client.

    The renderer does not infer claims.  It only adds canonical markers for the
    IDs explicitly attached to each structured block and silently omits IDs
    that cannot be resolved against this run's successful evidence ledger.
    """
    answer = structured_answer_mapping(value)
    title = str(answer.get("title") or "").strip().lstrip("# ").strip()
    evidence_records = list(evidence)
    available = _canonical_ids(evidence_records)
    lines: list[str] = []
    if title:
        lines.append(f"# {title[:240]}")

    previous_section = ""
    for block in structured_answer_blocks(answer):
        section = str(block.get("section") or "").strip().lstrip("# ").strip()
        if section and section != previous_section:
            if lines:
                lines.append("")
            lines.append(f"## {section[:160]}")
            previous_section = section

        content = str(block.get("content") or "").strip()
        if not content:
            continue
        # Evidence markers are a renderer concern.  Remove any model-emitted
        # marker first so an invalid id cannot bypass the typed field contract.
        normalized, _ = canonicalize_evidence_markers(content, ())
        raw_ids = (
            block.get("evidence_ids")
            if isinstance(block.get("evidence_ids"), Sequence)
            and not isinstance(block.get("evidence_ids"), (str, bytes, bytearray))
            else []
        )
        ids = _resolved_ids(raw_ids, available)
        missing_markers = [
            f"【证据 {evidence_id}】"
            for evidence_id in ids
        ]
        rendered = normalized
        if missing_markers:
            rendered = rendered.rstrip() + " " + " ".join(missing_markers)
        if lines and lines[-1] and not lines[-1].startswith("## "):
            lines.append("")
        lines.append(rendered)

    return "\n".join(lines).strip()


_PARTIAL_DIAGNOSTIC_PREFIX = "[本轮结果存在未完成的核验："


def finalize_terminal_answer(
    answer: Any,
    *,
    status: str,
    error_code: str | None = None,
    detail: str | None = None,
) -> str:
    """Apply one idempotent user-facing terminal contract to incomplete runs."""
    normalized = str(answer or "").strip()
    # A provider failure before any candidate answer must remain answer-less;
    # the run status/error fields are the diagnostic in that case.  Adding a
    # synthetic assistant sentence would make the UI treat a failed run as a
    # completed answer.
    if not normalized:
        return ""
    if status == "completed":
        return normalized
    if any(
        marker in normalized
        for marker in (
            _PARTIAL_DIAGNOSTIC_PREFIX,
            "[正文取证未完成：",
            "[本轮未完成全部取证：",
            "[本轮外部证据关联未能完整通过：",
        )
    ):
        return normalized
    if error_code == "cancelled" and "[已停止]" in normalized:
        return normalized

    defaults = {
        "tool_call_budget_exceeded": "本轮未完成全部取证，后续工具调用已达到运行上限",
        "evidence_link_incomplete": "证据关联未完整通过，部分回答不能直接核对",
        "content_access_incomplete": "正文取证未完成，部分内容仅基于来源索引或摘要",
        "content_access_budget_exceeded": "正文取证未完成，工具调用预算已用尽",
        "model_provider_timeout": "模型服务超时，回答可能只覆盖已完成的部分",
        "model_provider_unavailable": "模型服务不可用，回答可能只覆盖已完成的部分",
        "agent_loop_budget_exceeded": "Agent 循环未完成，回答只覆盖已完成的部分",
        "agent_runtime_failed": "运行过程中断，回答只覆盖已完成的部分",
        "structured_output_incomplete": "模型未能在有限修订次数内提交符合要求的结构化回答",
        "source_fallback_incomplete": "来源恢复后仍未取得支持回答的有效证据",
        "cancelled": "本轮任务已停止",
    }
    reason = str(detail or "").strip() or defaults.get(error_code or "", "本轮核验未完整结束")
    marker = f"{_PARTIAL_DIAGNOSTIC_PREFIX}{reason}]"
    return f"{normalized}\n\n{marker}".strip()


__all__ = [
    "AnswerBlockKind",
    "STRUCTURED_OUTPUT_TOOL_NAME",
    "StructuredAgentAnswer",
    "StructuredAnswerBlock",
    "evidence_source_catalog",
    "finalize_terminal_answer",
    "render_structured_answer",
    "resolve_answer_sources",
    "structured_answer_blocks",
    "structured_answer_mapping",
]
