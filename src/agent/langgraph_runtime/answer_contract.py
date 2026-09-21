"""Structured answer contract for the native LangChain Agent loop.

The model produces a typed collection of answer blocks instead of a free-form
Markdown document that the runtime must parse back into claims.  Markdown is
still the client-facing representation, but the block metadata is the source
of truth for claim scope, kind, and evidence references.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from src.tools.base import evidence_record_is_eligible

from .evidence_identity import canonicalize_evidence_markers, resolve_evidence_id


AnswerProfile = Literal["general", "research"]


AnswerBlockPresentation = Literal[
    "markdown",
    "table",
    "code",
    "json",
    "list",
    "quote",
]


AnswerChartType = Literal["line", "bar", "area"]


AnswerBlockKind = Literal[
    "context",
    "answer",
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
                "Block semantics: answer is a general response; context/disclaimer may be used "
                "without external evidence; fact/inference/recommendation/risk are material claims "
                "and require supporting source_ids under the applicable answer profile."
            ),
        ),
    ]
    presentation_type: Annotated[
        AnswerBlockPresentation,
        Field(
            default="markdown",
            description=(
                "Client presentation only, independent from kind. Use markdown for prose, table for a Markdown "
                "table, code for source code, json for raw valid JSON, list for a Markdown list, and quote for "
                "quoted text. The server renders code and JSON safely; do not put evidence markers in content."
            ),
        ),
    ]
    language: Annotated[
        str,
        Field(
            default="",
            max_length=32,
            description="Optional code language for presentation_type=code, such as python, typescript, or sql.",
        ),
    ]
    chart_type: Annotated[
        AnswerChartType,
        Field(
            default="line",
            description=(
                "Optional chart style for chart_source_ids. Only line, bar, or area are allowed; "
                "this is a display hint and never executes an operation."
            ),
        ),
    ]
    chart_series_keys: Annotated[
        list[Annotated[str, Field(max_length=64)]],
        Field(
            default_factory=list,
            max_length=3,
            description=(
                "Optional series field names from the selected chart catalog, such as main_net_inflow. "
                "Choose only keys listed in that catalog and keep one compatible unit/metric family; "
                "never write URLs, paths, scripts, or arbitrary data."
            ),
        ),
    ]
    chart_title: Annotated[
        str,
        Field(
            default="",
            max_length=160,
            description=(
                "Optional short, user-facing chart title. Do not put URLs, paths, tool arguments, or executable "
                "content here; the server still owns the chart data and reference identity."
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
            max_length=80,
            description=(
                "Integer source_ids from the current run's source catalog supporting this block. "
                "Cite only relevant sources, up to 80 across the selected experts. "
                "The server resolves these numbers to durable evidence IDs. Never write ev_ hashes."
            ),
        ),
    ]
    artifact_source_ids: Annotated[
        list[Annotated[int, Field(strict=True, ge=1)]],
        Field(
            default_factory=list,
            max_length=8,
            description=(
                "Integer ids from the current run's output artifact catalog. These ids can only refer to "
                "server-generated files/documents. Never write a local path, URL, filename, or file content."
            ),
        ),
    ]
    chart_source_ids: Annotated[
        list[Annotated[int, Field(strict=True, ge=1)]],
        Field(
            default_factory=list,
            max_length=8,
            description=(
                "Integer ids from the current run's chart catalog. The server builds chart data from trusted "
                "tool results; never write chart URLs, JavaScript, or executable code here."
            ),
        ),
    ]
    action_source_ids: Annotated[
        list[Annotated[int, Field(strict=True, ge=1)]],
        Field(
            default_factory=list,
            max_length=8,
            description=(
                "Integer ids from the current run's observed action catalog. These are read-only audit/display "
                "references and never request a new tool call or execute an action."
            ),
        ),
    ]


class StructuredAgentAnswer(TypedDict):
    """The only model-owned payload accepted as a new run's final answer."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    profile: Annotated[
        AnswerProfile,
        Field(
            default="research",
            description=(
                "Answer profile. Use general for explanation/translation/coding guidance and research "
                "for evidence-driven stock analysis. The research default preserves compatibility with "
                "historical answers that predate this field."
            ),
        ),
    ]
    title: Annotated[
        str,
        Field(
            default="",
            max_length=240,
            description="Optional short title. Do not put factual claims only in the title.",
        ),
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

_ANSWER_BLOCK_PRESENTATIONS = frozenset(
    {"markdown", "table", "code", "json", "list", "quote"}
)
_ANSWER_CHART_TYPES = frozenset({"line", "bar", "area"})
_CODE_LANGUAGE_PATTERN = re.compile(r"[A-Za-z0-9_+#.-]{1,32}")
_SAFE_EXPORT_FILE_ID_PATTERN = re.compile(
    r"(?:stock-screen(?:-financial)?|atr-volatility)-\d{8}-\d{6}-[0-9a-f]{8}\.csv"
)
_SAFE_RESOURCE_ID_PATTERN = re.compile(r"textdoc_[0-9a-f]{40}")
_SAFE_REFERENCE_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
_SAFE_REFERENCE_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_MACHINE_CHART_TITLE_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{2,120}数据")
_SECTION_NUMBER_PREFIX_PATTERN = re.compile(
    r"^(?P<number>[0-9]+|[一二三四五六七八九十百千万零〇两]+)[、.．:：)）\-][ \t]*"
)
_STRUCTURED_ANSWER_CLIENT_TEXT_LIMIT = 12_000
_STRUCTURED_ANSWER_REFERENCE_TEXT_LIMIT = 160
_STRUCTURED_ANSWER_CHART_ROW_LIMIT = 120


def _presentation_type(value: Any) -> str:
    normalized = str(value or "markdown").strip().lower()
    return normalized if normalized in _ANSWER_BLOCK_PRESENTATIONS else "markdown"


def _code_language(value: Any) -> str:
    language = str(value or "").strip()
    return language if _CODE_LANGUAGE_PATTERN.fullmatch(language) else ""


def _chart_type(value: Any) -> str:
    chart_type = str(value or "line").strip().lower()
    return chart_type if chart_type in _ANSWER_CHART_TYPES else "line"


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _section_label_for_render(
    value: Any,
    seen_section_numbers: set[str],
) -> str:
    """Keep model section labels readable when numbering is repeated.

    A typed answer can contain independently generated blocks. If two blocks
    both arrive as ``三、...``, preserving both headings makes the final report
    look malformed even though their subjects are different. Keep the first
    heading and merge later content into that section; the raw typed answer
    remains unchanged for audit and repair purposes.
    """
    section = str(value or "").strip().lstrip("# ").strip()
    if not section:
        return ""
    match = _SECTION_NUMBER_PREFIX_PATTERN.match(section)
    if not match:
        return section[:160]
    number = match.group("number")
    if number in seen_section_numbers:
        return ""
    seen_section_numbers.add(number)
    return section[:160]


def _display_title(value: Any, fallback: str) -> str:
    title = _bounded_text(value, _STRUCTURED_ANSWER_REFERENCE_TEXT_LIMIT)
    # A tool may return a filename or a path-like label.  The reference UI
    # needs a human label, never the directory portion of an artifact path.
    title = title.replace("\\", "/").rsplit("/", 1)[-1]
    return title or fallback


def _chart_title_for_block(block: Mapping[str, Any], current: Any) -> str:
    requested = _display_title(block.get("chart_title"), "")
    if requested:
        return requested
    section = _display_title(block.get("section"), "")
    current_title = _bounded_text(current, _STRUCTURED_ANSWER_REFERENCE_TEXT_LIMIT)
    if section and _MACHINE_CHART_TITLE_PATTERN.fullmatch(current_title):
        return section
    return ""


def _finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _safe_export_file_id(value: Any) -> str:
    file_id = str(value or "").strip()
    return file_id if _SAFE_EXPORT_FILE_ID_PATTERN.fullmatch(file_id) else ""


def _safe_resource_id(value: Any) -> str:
    resource_id = str(value or "").strip()
    return resource_id if _SAFE_RESOURCE_ID_PATTERN.fullmatch(resource_id) else ""


def _safe_reference_id(value: Any) -> str:
    reference_id = str(value or "").strip()
    return reference_id if _SAFE_REFERENCE_ID_PATTERN.fullmatch(reference_id) else ""


def _strict_integer_list(value: Any, *, limit: int) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value[:limit] if type(item) is int and item >= 1]


def structured_answer_mapping(value: Any) -> dict[str, Any]:
    """Convert LangChain's Pydantic response or a checkpoint dict to plain data."""
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="python")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def structured_answer_profile(value: Any) -> str:
    """Return a safe profile for current and historical structured answers.

    Answers produced before the profile field was introduced are treated as
    ``research`` so the old evidence gate remains the compatibility default.
    Unknown values are also fail-closed to the strict profile when this helper
    is used on checkpoint data that has not gone through schema validation.
    """
    answer = structured_answer_mapping(value)
    profile = str(answer.get("profile") or "research").strip().lower()
    return profile if profile in {"general", "research"} else "research"


def structured_answer_contract_issues(value: Any) -> list[str]:
    """Validate cross-field rules that a TypedDict cannot express."""
    answer = structured_answer_mapping(value)
    if not answer:
        return ["结构化回答不是有效的对象"]
    profile = structured_answer_profile(answer)
    raw_profile = str(answer.get("profile") or "research").strip().lower()
    issues: list[str] = []
    if raw_profile not in {"general", "research"}:
        issues.append("结构化回答的 profile 必须是 general 或 research")
    for index, block in enumerate(structured_answer_blocks(answer), start=1):
        if "presentation_type" in block:
            raw_presentation = str(block.get("presentation_type") or "").strip().lower()
            if raw_presentation not in _ANSWER_BLOCK_PRESENTATIONS:
                issues.append(
                    f"第 {index} 个回答区块的 presentation_type 必须是 "
                    "markdown、table、code、json、list 或 quote"
                )
        presentation = _presentation_type(block.get("presentation_type"))
        if presentation == "code" and block.get("language") and not _code_language(block.get("language")):
            issues.append(f"第 {index} 个 code 区块的 language 不是安全的语言标识")
        if block.get("chart_type") and str(block.get("chart_type")).strip().lower() not in _ANSWER_CHART_TYPES:
            issues.append(f"第 {index} 个区块的 chart_type 必须是 line、bar 或 area")
        if presentation == "json":
            try:
                json.loads(str(block.get("content") or ""))
            except (TypeError, ValueError):
                issues.append(f"第 {index} 个 json 区块的 content 必须是有效 JSON")
    return issues


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


def _evidence_ids_by_action(evidence: Iterable[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in evidence:
        if not isinstance(item, Mapping) or not evidence_record_is_eligible(item):
            continue
        action_id = _bounded_text(item.get("action_id"), 128)
        evidence_id = _bounded_text(item.get("evidence_id") or item.get("id"), 96)
        if action_id and evidence_id:
            result.setdefault(action_id, evidence_id)
    return result


def _artifact_candidates(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    nested = result.get("resource")
    if isinstance(nested, Mapping):
        candidates.append(dict(nested))
    resources = result.get("resources")
    if isinstance(resources, Sequence) and not isinstance(resources, (str, bytes, bytearray)):
        candidates.extend(dict(item) for item in resources if isinstance(item, Mapping))
    candidates.append(dict(result))
    return candidates


def _chart_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("data", "items", "rows", "records", "results"):
        value = result.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            rows = [dict(item) for item in value if isinstance(item, Mapping)]
            if rows:
                return rows[:240]
        if isinstance(value, Mapping):
            rows = []
            for row_key, row_value in value.items():
                if isinstance(row_value, Mapping):
                    row = dict(row_value)
                    row.setdefault("series", row_key)
                    rows.append(row)
            if rows:
                return rows[:240]
    return []


def _chart_reference_from_result(
    *,
    record: Mapping[str, Any],
    result: Mapping[str, Any],
    action_id: str,
    evidence_id: str | None,
    chart_index: int,
) -> dict[str, Any] | None:
    rows = _chart_rows(result)
    if not rows:
        return None
    first_keys = list(rows[0].keys())
    x_key = next(
        (
            key
            for preferred in (
                "date",
                "trade_date",
                "report_date",
                "datetime",
                "timestamp",
                "time",
                "period",
                "x",
            )
            for key in first_keys
            if str(key).lower() == preferred
        ),
        "",
    )
    if not x_key:
        # Ranking/table tools often return one row per symbol rather than a
        # time series.  A server-owned code/name label is still a valid chart
        # axis for a bar chart; it never lets the model author the data.
        x_key = next(
            (
                key
                for preferred in ("code", "symbol", "name")
                for key in first_keys
                if str(key).lower() == preferred
                and any(_bounded_text(row.get(key), 80) for row in rows)
            ),
            "",
        )
    if not x_key:
        return None

    preferred_y_keys = (
        "close",
        "price",
        "value",
        "amount",
        "volume",
        "open",
        "high",
        "low",
    )
    excluded = {
        x_key,
        "code",
        "name",
        "symbol",
        "series",
        "id",
        "date",
        "trade_date",
        "report_date",
        "datetime",
        "timestamp",
        "time",
        "period",
        "x",
    }
    numeric_keys: list[str] = []
    for key in first_keys:
        key_text = str(key)
        if key_text.lower() in excluded or not _SAFE_REFERENCE_KEY_PATTERN.fullmatch(key_text):
            continue
        if any(_finite_number(row.get(key)) is not None for row in rows):
            numeric_keys.append(key_text)
    numeric_keys.sort(
        key=lambda key: (
            preferred_y_keys.index(key.lower()) if key.lower() in preferred_y_keys else len(preferred_y_keys),
            first_keys.index(key),
        )
    )
    numeric_keys = numeric_keys[:3]
    if not numeric_keys:
        return None

    series = [
        {"key": key, "label": _bounded_text(key, 80)}
        for key in numeric_keys
    ]
    chart_data: list[dict[str, Any]] = []
    for row in rows:
        x_value = _bounded_text(row.get(x_key), 80)
        if not x_value:
            continue
        point: dict[str, Any] = {"x": x_value}
        for key in numeric_keys:
            number = _finite_number(row.get(key))
            if number is not None:
                point[key] = number
        if len(point) > 1:
            chart_data.append(point)
        if len(chart_data) >= _STRUCTURED_ANSWER_CHART_ROW_LIMIT:
            break
    if not chart_data:
        return None

    chart_type = _chart_type(result.get("chart_type"))
    # A single quote/lookup row is not a useful line chart.  Some quote tools
    # expose their tabular payload through ``data`` and would otherwise render
    # a misleading one-point chart alongside the real K-line series.  Keep a
    # one-row result only when the tool explicitly requested a bar chart,
    # where a single ranked item is still meaningful.
    if chart_type != "bar" and len(chart_data) < 2:
        return None

    chart_id = _safe_reference_id(
        f"chart-{_bounded_text(action_id or record.get('tool_name'), 72)}-{chart_index}"
    ) or f"chart-{chart_index}"
    title = _bounded_text(
        result.get("chart_title") or result.get("title") or result.get("name")
        or f"{_bounded_text(record.get('tool_name'), 80) or '工具'}数据",
        _STRUCTURED_ANSWER_REFERENCE_TEXT_LIMIT,
    )
    return {
        "source_id": chart_index,
        "chart_id": chart_id,
        "chart_type": chart_type,
        "title": title,
        "x_key": "x",
        "series": series,
        "data": chart_data,
        "action_id": action_id or None,
        "tool_call_id": _bounded_text(record.get("tool_call_id"), 128) or None,
        "evidence_id": evidence_id,
    }


def _chart_fingerprint(chart: Mapping[str, Any]) -> str:
    """Identify the visual chart payload independently of a retry attempt.

    A retry may have a new action/tool-call id while returning the same chart
    rows.  Those calls remain separate audit actions, but publishing the same
    visual twice makes the final answer look duplicated.  Keep only fields
    that define the rendered chart; source/action identity is deliberately
    excluded so retries can collapse to one display reference.
    """
    return json.dumps(
        {
            "chart_type": chart.get("chart_type"),
            "title": chart.get("title"),
            "x_key": chart.get("x_key"),
            "series": chart.get("series"),
            "data": chart.get("data"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _restrict_chart_reference_series(
    value: Mapping[str, Any],
    requested: Any,
) -> dict[str, Any]:
    """Keep only model-selected, server-catalogued chart series.

    The model may select semantic fields (for example ``main_net_inflow``),
    but it never supplies chart values.  The values remain the trusted,
    server-built projection and unknown keys are ignored here.
    """
    if not isinstance(requested, Sequence) or isinstance(requested, (str, bytes, bytearray)):
        return dict(value)
    requested_keys = [
        str(item).strip()
        for item in requested[:3]
        if _SAFE_REFERENCE_KEY_PATTERN.fullmatch(str(item).strip())
    ]
    if not requested_keys:
        return dict(value)

    available = {
        str(item.get("key")): item
        for item in value.get("series", [])
        if isinstance(item, Mapping)
    }
    selected_keys = [key for key in requested_keys if key in available]
    if not selected_keys:
        return dict(value)

    data: list[dict[str, Any]] = []
    raw_data = value.get("data")
    if isinstance(raw_data, Sequence) and not isinstance(raw_data, (str, bytes, bytearray)):
        for item in raw_data[:_STRUCTURED_ANSWER_CHART_ROW_LIMIT]:
            if not isinstance(item, Mapping):
                continue
            point: dict[str, Any] = {"x": _bounded_text(item.get("x"), 80)}
            if not point["x"]:
                continue
            for key in selected_keys:
                number = _finite_number(item.get(key))
                if number is not None:
                    point[key] = number
            if len(point) > 1:
                data.append(point)
    if not data:
        return dict(value)

    return {
        **value,
        "series": [available[key] for key in selected_keys],
        "data": data,
    }


def output_reference_catalogs(
    tool_results: Iterable[Any],
    evidence: Iterable[Any] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Build server-owned output catalogs for one run.

    The model sees only numeric slots from ``output_reference_catalog_for_model``.
    IDs, URLs, chart rows, and action details are resolved here from trusted
    tool records and are never authored by the model.
    """
    records = [item for item in tool_results if isinstance(item, Mapping)]
    evidence_by_action = _evidence_ids_by_action(evidence)
    artifacts: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()
    seen_charts: set[str] = set()
    for record in records[:80]:
        action_id = _bounded_text(record.get("action_id") or record.get("id"), 128)
        tool_name = _bounded_text(record.get("tool_name"), 128)
        effect = str(record.get("effect") or "read").strip().lower()
        if action_id:
            actions.append(
                {
                    "source_id": len(actions) + 1,
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "effect": effect if effect in {"read", "side_effect"} else "read",
                    "status": "completed" if record.get("success") is True else "failed",
                    "success": record.get("success") is True,
                    "reused": bool(record.get("reused")),
                    "evidence_id": evidence_by_action.get(action_id),
                }
            )

        raw_result = record.get("result")
        if not isinstance(raw_result, Mapping) or record.get("success") is not True:
            continue
        evidence_id = evidence_by_action.get(action_id)
        for candidate in _artifact_candidates(raw_result):
            file_id = _safe_export_file_id(candidate.get("file_id"))
            resource_id = _safe_resource_id(candidate.get("resource_id"))
            if not file_id and not resource_id:
                continue
            artifact_id = file_id or resource_id
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)
            is_document = bool(resource_id)
            artifacts.append(
                {
                    "source_id": len(artifacts) + 1,
                    "artifact_id": artifact_id,
                    "artifact_type": "document" if is_document else "file",
                    "title": _display_title(
                        candidate.get("filename")
                        or candidate.get("file_name")
                        or candidate.get("title")
                        or ("原始文档" if is_document else "生成文件"),
                        "原始文档" if is_document else "生成文件",
                    ),
                    "mime_type": _bounded_text(
                        candidate.get("mime_type") or ("text/plain" if is_document else "text/csv"),
                        96,
                    ),
                    "download_url": (
                        f"/api/v1/agent/resources/{resource_id}/content?disposition=attachment"
                        if is_document
                        else f"/api/v1/agent/exports/{file_id}"
                    ),
                    "preview_url": (
                        f"/api/v1/agent/resources/{resource_id}/content?disposition=inline"
                        if is_document
                        else None
                    ),
                    "action_id": action_id or None,
                    "evidence_id": evidence_id,
                    "tool_name": tool_name or None,
                }
            )
        chart = _chart_reference_from_result(
            record=record,
            result=raw_result,
            action_id=action_id,
            evidence_id=evidence_id,
            chart_index=len(charts) + 1,
        )
        if chart:
            fingerprint = _chart_fingerprint(chart)
            if fingerprint in seen_charts:
                continue
            seen_charts.add(fingerprint)
            charts.append(chart)

    return {"artifacts": artifacts[:24], "charts": charts[:24], "actions": actions[:80]}


def output_reference_catalog_for_model(
    tool_results: Iterable[Any],
    evidence: Iterable[Any] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Expose reference slots without leaking IDs, URLs, rows, or arguments."""
    catalogs = output_reference_catalogs(tool_results, evidence)
    return {
        "artifacts": [
            {
                "source_id": item["source_id"],
                "artifact_type": item["artifact_type"],
                "title": item["title"],
                "mime_type": item["mime_type"],
                "action_id": item.get("action_id"),
            }
            for item in catalogs["artifacts"]
        ],
        "charts": [
            {
                "source_id": item["source_id"],
                "chart_type": item["chart_type"],
                "title": item["title"],
                "x_key": item["x_key"],
                "series": item["series"],
                "action_id": item.get("action_id"),
            }
            for item in catalogs["charts"]
        ],
        "actions": [
            {
                key: item.get(key)
                for key in (
                    "source_id",
                    "action_id",
                    "tool_name",
                    "effect",
                    "status",
                    "success",
                    "reused",
                )
            }
            for item in catalogs["actions"]
        ],
    }


def _safe_artifact_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    file_id = _safe_export_file_id(value.get("artifact_id") or value.get("file_id"))
    resource_id = _safe_resource_id(value.get("artifact_id") or value.get("resource_id"))
    if not file_id and not resource_id:
        return None
    is_document = bool(resource_id)
    artifact_id = resource_id or file_id
    return {
        "artifact_id": artifact_id,
        "artifact_type": "document" if is_document else "file",
        "title": _display_title(
            value.get("title"),
            "原始文档" if is_document else "生成文件",
        ),
        "mime_type": _bounded_text(value.get("mime_type") or ("text/plain" if is_document else "text/csv"), 96),
        "download_url": (
            f"/api/v1/agent/resources/{resource_id}/content?disposition=attachment"
            if is_document
            else f"/api/v1/agent/exports/{file_id}"
        ),
        "preview_url": (
            f"/api/v1/agent/resources/{resource_id}/content?disposition=inline"
            if is_document
            else None
        ),
        "action_id": _bounded_text(value.get("action_id"), 128) or None,
        "evidence_id": _bounded_text(value.get("evidence_id"), 96) or None,
        "tool_name": _bounded_text(value.get("tool_name"), 128) or None,
    }


def _safe_chart_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    chart_id = _safe_reference_id(value.get("chart_id"))
    if not chart_id:
        return None
    raw_series = value.get("series")
    series: list[dict[str, str]] = []
    if isinstance(raw_series, Sequence) and not isinstance(raw_series, (str, bytes, bytearray)):
        for item in raw_series[:3]:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("key") or "").strip()
            if not _SAFE_REFERENCE_KEY_PATTERN.fullmatch(key):
                continue
            series.append({"key": key, "label": _bounded_text(item.get("label") or key, 80)})
    if not series:
        return None
    keys = {item["key"] for item in series}
    data: list[dict[str, Any]] = []
    raw_data = value.get("data")
    if isinstance(raw_data, Sequence) and not isinstance(raw_data, (str, bytes, bytearray)):
        for item in raw_data[:_STRUCTURED_ANSWER_CHART_ROW_LIMIT]:
            if not isinstance(item, Mapping):
                continue
            x = _bounded_text(item.get("x"), 80)
            if not x:
                continue
            point: dict[str, Any] = {"x": x}
            for key in keys:
                number = _finite_number(item.get(key))
                if number is not None:
                    point[key] = number
            if len(point) > 1:
                data.append(point)
    if not data:
        return None
    chart_type = _chart_type(value.get("chart_type"))
    action_id = _bounded_text(value.get("action_id"), 128) or None
    tool_call_id = _bounded_text(value.get("tool_call_id"), 128) or None
    # A server-generated one-point line chart is a quote lookup rendered as a
    # chart by an older trace, not a useful time series. Keep explicitly
    # model-authored/manual chart data intact when it has no server identity.
    if chart_type != "bar" and len(data) < 2 and (action_id or tool_call_id):
        return None
    return {
        "chart_id": chart_id,
        "chart_type": chart_type,
        "title": _bounded_text(value.get("title") or "数据图表", _STRUCTURED_ANSWER_REFERENCE_TEXT_LIMIT),
        "x_key": "x",
        "series": series,
        "data": data,
        "action_id": action_id,
        "tool_call_id": tool_call_id,
        "evidence_id": _bounded_text(value.get("evidence_id"), 96) or None,
    }


def _safe_action_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    action_id = _bounded_text(value.get("action_id"), 128)
    if not action_id:
        return None
    effect = str(value.get("effect") or "read").strip().lower()
    return {
        "action_id": action_id,
        "tool_name": _bounded_text(value.get("tool_name"), 128) or None,
        "effect": effect if effect in {"read", "side_effect"} else "read",
        "status": "completed" if str(value.get("status") or "") == "completed" else "failed",
        "success": value.get("success") is True,
        "reused": bool(value.get("reused")),
        "evidence_id": _bounded_text(value.get("evidence_id"), 96) or None,
    }


def resolve_structured_answer_references(
    value: Any,
    *,
    evidence: Iterable[Any] = (),
    tool_results: Iterable[Any] = (),
) -> dict[str, Any]:
    """Resolve model-selected output slots to safe server-owned references."""
    evidence_records = list(evidence)
    answer = resolve_answer_sources(value, evidence_records)
    if not answer:
        return answer
    catalogs = output_reference_catalogs(tool_results, evidence_records)
    catalog_maps = {
        key: {item["source_id"]: item for item in items}
        for key, items in catalogs.items()
    }
    blocks = structured_answer_blocks(answer)
    for block in blocks:
        references = (
            ("artifact_source_ids", "artifact_refs", "artifacts", _safe_artifact_reference),
            ("chart_source_ids", "chart_refs", "charts", _safe_chart_reference),
            ("action_source_ids", "action_refs", "actions", _safe_action_reference),
        )
        for source_key, reference_key, catalog_key, sanitizer in references:
            resolved: list[dict[str, Any]] = []
            for source_id in _strict_integer_list(block.get(source_key), limit=8):
                candidate = catalog_maps[catalog_key].get(source_id)
                safe = sanitizer(candidate)
                if safe and safe not in resolved:
                    if reference_key == "chart_refs":
                        requested_chart_type = str(block.get("chart_type") or "").strip().lower()
                        if requested_chart_type in _ANSWER_CHART_TYPES:
                            safe["chart_type"] = requested_chart_type
                        requested_chart_title = _chart_title_for_block(block, safe.get("title"))
                        if requested_chart_title:
                            safe["title"] = requested_chart_title
                    resolved.append(safe)
            if not resolved:
                raw_existing = block.get(reference_key)
                if isinstance(raw_existing, Sequence) and not isinstance(
                    raw_existing, (str, bytes, bytearray)
                ):
                    resolved = [
                        safe
                        for item in raw_existing[:8]
                        if (safe := sanitizer(item)) is not None
                    ]
            if reference_key == "chart_refs":
                resolved = [
                    _restrict_chart_reference_series(safe, block.get("chart_series_keys"))
                    for safe in resolved
                ]
                for safe in resolved:
                    requested_chart_title = _chart_title_for_block(block, safe.get("title"))
                    if requested_chart_title:
                        safe["title"] = requested_chart_title
            if resolved:
                block[reference_key] = resolved
    return {**answer, "blocks": blocks}


def project_structured_answer(
    value: Any,
    evidence: Iterable[Any] = (),
    tool_results: Iterable[Any] = (),
) -> dict[str, Any]:
    """Build the bounded, client-safe projection of a typed answer.

    The terminal trace is a rendering input, not an audit export.  Keep only
    fields the client needs to choose a renderer, and expose canonical
    evidence IDs rather than the model's numeric source slots.  Unknown or
    stale references are omitted from this projection; the claim ledger still
    retains them for repair diagnostics.
    """
    evidence_records = list(evidence)
    answer = resolve_structured_answer_references(
        value,
        evidence=evidence_records,
        tool_results=tool_results,
    )
    if not answer:
        return {}

    available = _canonical_ids(
        item
        for item in evidence_records
        if isinstance(item, Mapping)
        and evidence_record_is_eligible(item)
        and str(item.get("effect") or "read") != "side_effect"
    )
    projected_blocks: list[dict[str, Any]] = []
    valid_kinds = {
        "context",
        "answer",
        "fact",
        "inference",
        "recommendation",
        "risk",
        "disclaimer",
    }
    for block in structured_answer_blocks(answer)[:80]:
        kind = str(block.get("kind") or "fact").strip().lower()
        raw_evidence_ids = block.get("evidence_ids")
        evidence_ids = (
            _resolved_ids(raw_evidence_ids, available)
            if isinstance(raw_evidence_ids, Sequence)
            and not isinstance(raw_evidence_ids, (str, bytes, bytearray))
            else []
        )
        projected_block: dict[str, Any] = {
            "section": str(block.get("section") or "").strip()[:160],
            "kind": kind if kind in valid_kinds else "answer",
            "presentation_type": _presentation_type(block.get("presentation_type")),
            "language": _code_language(block.get("language"))
            if _presentation_type(block.get("presentation_type")) == "code"
            else "",
            "content": str(block.get("content") or "").strip()[:_STRUCTURED_ANSWER_CLIENT_TEXT_LIMIT],
            "evidence_ids": evidence_ids,
        }
        for reference_key, sanitizer in (
            ("artifact_refs", _safe_artifact_reference),
            ("chart_refs", _safe_chart_reference),
            ("action_refs", _safe_action_reference),
        ):
            raw_references = block.get(reference_key)
            if not isinstance(raw_references, Sequence) or isinstance(
                raw_references, (str, bytes, bytearray)
            ):
                continue
            safe_references = [
                safe
                for item in raw_references[:8]
                if (safe := sanitizer(item)) is not None
            ]
            if safe_references:
                projected_block[reference_key] = safe_references
        projected_blocks.append(projected_block)

    projected: dict[str, Any] = {
        "profile": structured_answer_profile(answer),
        "title": str(answer.get("title") or "").strip()[:240],
        "blocks": projected_blocks,
    }
    return projected


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


def _render_code_block(content: str, language: Any = "") -> str:
    """Fence code without allowing its body to close the Markdown fence."""
    max_backtick_run = max(
        (len(run) for run in re.findall(r"`+", content)),
        default=0,
    )
    fence = "`" * max(3, max_backtick_run + 1)
    safe_language = _code_language(language)
    return f"{fence}{safe_language}\n{content}\n{fence}"


def _render_block_content(block: Mapping[str, Any], content: str) -> str:
    presentation = _presentation_type(block.get("presentation_type"))
    if presentation == "code":
        return _render_code_block(content, block.get("language"))
    if presentation == "json":
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            # Contract validation reports this as a repairable issue.  Keep a
            # readable fallback for historical/partial traces that still need
            # to be displayed without crashing the terminal publisher.
            return content
        return _render_code_block(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            "json",
        )
    if presentation == "quote":
        return "\n".join(f"> {line}" if line else ">" for line in content.splitlines())
    return content


def _markdown_label(value: Any) -> str:
    """Escape the small Markdown surface used by generated reference labels."""
    return re.sub(r"([\\\[\]\(\)])", r"\\\1", _bounded_text(value, 160))


def _render_output_references(
    block: Mapping[str, Any],
    *,
    include_charts: bool = True,
    include_actions: bool = True,
) -> list[str]:
    lines: list[str] = []
    artifacts = block.get("artifact_refs")
    if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes, bytearray)):
        for item in artifacts[:8]:
            safe = _safe_artifact_reference(item)
            if not safe:
                continue
            title = _markdown_label(safe.get("title") or safe.get("artifact_id"))
            url = str(safe.get("download_url") or "").strip()
            if url:
                lines.append(f"- [下载文件：{title}]({url})")
    charts = block.get("chart_refs")
    if include_charts and isinstance(charts, Sequence) and not isinstance(charts, (str, bytes, bytearray)):
        for item in charts[:8]:
            safe = _safe_chart_reference(item)
            if safe:
                lines.append(f"- 图表：{_markdown_label(safe.get('title') or '数据图表')}（已根据本轮工具数据生成）")
    actions = block.get("action_refs")
    if include_actions and isinstance(actions, Sequence) and not isinstance(actions, (str, bytes, bytearray)):
        for item in actions[:8]:
            safe = _safe_action_reference(item)
            if not safe:
                continue
            status = "已完成" if safe["status"] == "completed" else "失败"
            tool_name = _markdown_label(safe.get("tool_name") or "工具动作")
            lines.append(f"- 动作记录：{tool_name}（{status}，仅展示，不会再次执行）")
    return lines


def render_structured_answer(
    value: Any,
    evidence: Iterable[Any] = (),
    tool_results: Iterable[Any] = (),
    *,
    include_chart_fallback: bool = True,
) -> str:
    """Render the typed answer while making block evidence visible to the client.

    The renderer does not infer claims.  It only adds canonical markers for the
    IDs explicitly attached to each structured block and silently omits IDs
    that cannot be resolved against this run's successful evidence ledger.
    """
    evidence_records = list(evidence)
    answer = resolve_structured_answer_references(
        value,
        evidence=evidence_records,
        tool_results=tool_results,
    )
    answer = structured_answer_mapping(answer)
    title = str(answer.get("title") or "").strip().lstrip("# ").strip()
    available = _canonical_ids(evidence_records)
    lines: list[str] = []
    if title:
        lines.append(f"# {title[:240]}")

    previous_section = ""
    seen_section_numbers: set[str] = set()
    for block in structured_answer_blocks(answer):
        section = _section_label_for_render(
            block.get("section"),
            seen_section_numbers,
        )
        if section and section != previous_section:
            if lines:
                lines.append("")
            lines.append(f"## {section[:160]}")
            previous_section = section

        content = str(block.get("content") or "").strip()
        output_references = _render_output_references(
            block,
            include_charts=include_chart_fallback,
        )
        if not content and not output_references:
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
        rendered = _render_block_content(block, normalized)
        if missing_markers:
            rendered = rendered.rstrip() + " " + " ".join(missing_markers)
        if lines and lines[-1] and not lines[-1].startswith("## "):
            lines.append("")
        if content:
            lines.append(rendered)
        if output_references:
            if lines and lines[-1]:
                lines.append("")
            lines.extend(output_references)

    return "\n".join(lines).strip()


def structured_answer_display_parts(
    value: Any,
    evidence: Iterable[Any] = (),
    tool_results: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Project a typed answer into the ordered chat parts used by the UI.

    The durable Markdown renderer intentionally keeps textual chart fallback
    lines for exports and old clients.  The live assistant-ui projection has a
    stronger contract: each answer block is emitted as text, followed by its
    own trusted chart data parts, before the next block is emitted.  This keeps
    narrative, evidence-backed content, and charts in the same chronological
    stream without asking the browser to reconstruct block ownership.
    """
    evidence_records = list(evidence)
    projected = project_structured_answer(value, evidence_records, tool_results)
    if not projected:
        return []

    available = _canonical_ids(evidence_records)
    title = str(projected.get("title") or "").strip().lstrip("# ").strip()
    previous_section = ""
    seen_section_numbers: set[str] = set()
    parts: list[dict[str, Any]] = []
    emitted_text = False

    for block in structured_answer_blocks(projected):
        section = _section_label_for_render(
            block.get("section"),
            seen_section_numbers,
        )
        content = str(block.get("content") or "").strip()
        # Native tool-call parts already occupy their original positions in
        # the ordered stream.  Do not duplicate them as an action list inside
        # the terminal answer; the Markdown renderer keeps that fallback for
        # exports and legacy clients.
        output_references = _render_output_references(
            block,
            include_charts=False,
            include_actions=False,
        )
        raw_charts = block.get("chart_refs")
        charts = (
            [safe for item in raw_charts[:8] if (safe := _safe_chart_reference(item)) is not None]
            if isinstance(raw_charts, Sequence)
            and not isinstance(raw_charts, (str, bytes, bytearray))
            else []
        )
        if not content and not output_references and not charts:
            continue

        lines: list[str] = []
        if not emitted_text and title:
            lines.append(f"# {title[:240]}")
        if section and section != previous_section:
            if lines:
                lines.append("")
            lines.append(f"## {section[:160]}")
            previous_section = section

        if content:
            normalized, _ = canonicalize_evidence_markers(content, ())
            raw_ids = (
                block.get("evidence_ids")
                if isinstance(block.get("evidence_ids"), Sequence)
                and not isinstance(block.get("evidence_ids"), (str, bytes, bytearray))
                else []
            )
            ids = _resolved_ids(raw_ids, available)
            rendered = _render_block_content(block, normalized)
            if ids:
                rendered = rendered.rstrip() + " " + " ".join(
                    f"【证据 {evidence_id}】" for evidence_id in ids
                )
            if lines and lines[-1]:
                lines.append("")
            lines.append(rendered)

        if output_references:
            if lines and lines[-1]:
                lines.append("")
            lines.extend(output_references)

        if lines:
            parts.append({
                "type": "text",
                "text": "\n".join(lines).strip(),
                "display_kind": "answer",
            })
            emitted_text = True
        for chart in charts:
            parts.append({
                "type": "data",
                "name": "stock-chart",
                "data": chart,
            })

    return parts


_PARTIAL_DIAGNOSTIC_PREFIX = "[本轮结果存在未完成的核验："
_INTERNAL_TERMINAL_DETAIL_MARKERS = (
    "PlanningPlan",
    "PlanningRoute",
    "PlanningStepReport",
    "PlanningContractError",
    "planning_",
    "ValidationError",
    "Input should",
    "failed after",
    "function_calling",
)


def _user_safe_terminal_detail(detail: Any, error_code: str | None) -> str:
    """Keep provider/contract diagnostics out of the conversational answer."""
    raw = str(detail or "").strip()
    if not raw or not any(marker in raw for marker in _INTERNAL_TERMINAL_DETAIL_MARKERS):
        return raw
    return {
        "planning_incomplete": "计划尚未完整结束，以下回答仅保留已核验结果，并明确说明未完成的步骤",
        "planning_generation_failed": "研究计划未能完整生成，本轮没有绕过计划直接执行工具",
        "planning_contract_validation_failed": "计划步骤的结果校验未通过，以下回答仅保留已核验部分",
    }.get(
        str(error_code or ""),
        "本轮执行未完整结束，以下回答仅保留已核验部分，并明确说明执行缺口",
    )


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
        "planning_incomplete": "计划尚未完整结束，以下回答仅保留已核验结果，并明确说明未完成的步骤",
        "planning_generation_failed": "研究计划未能完整生成，本轮没有绕过计划直接执行工具",
        "planning_contract_validation_failed": "计划步骤的结果校验未通过，以下回答仅保留已核验部分",
        "cancelled": "本轮任务已停止",
    }
    reason = _user_safe_terminal_detail(detail, error_code) or defaults.get(
        error_code or "",
        "本轮核验未完整结束",
    )
    marker = f"{_PARTIAL_DIAGNOSTIC_PREFIX}{reason}]"
    return f"{normalized}\n\n{marker}".strip()


__all__ = [
    "AnswerProfile",
    "AnswerBlockKind",
    "AnswerBlockPresentation",
    "AnswerChartType",
    "STRUCTURED_OUTPUT_TOOL_NAME",
    "StructuredAgentAnswer",
    "StructuredAnswerBlock",
    "evidence_source_catalog",
    "finalize_terminal_answer",
    "output_reference_catalog_for_model",
    "output_reference_catalogs",
    "project_structured_answer",
    "render_structured_answer",
    "structured_answer_display_parts",
    "resolve_answer_sources",
    "resolve_structured_answer_references",
    "structured_answer_blocks",
    "structured_answer_mapping",
    "structured_answer_profile",
    "structured_answer_contract_issues",
]
