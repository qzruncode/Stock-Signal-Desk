"""Domain-neutral claim-to-evidence ledger for final Agent answers.

The generic Agent loop does not need a second planner or a domain verifier to
keep factual output auditable.  Instead, every visible evidence-bearing answer
fragment, including factual tables and material conclusion/recommendation
sections, is projected into a durable claim record.  The record keeps the
exact supporting tool evidence, source provenance, requested entity scope and
time checks together.  Missing support is a normal model-loop observation:
middleware asks the model to revise in the same loop.

This module deliberately has no stock, industry, or provider vocabulary.  It
only understands the common result/evidence envelope produced by the atomic
tool executor.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from src.tools.base import evidence_record_is_eligible

from .evidence_identity import (
    EVIDENCE_REFERENCE as _EVIDENCE_REFERENCE,
    contains_evidence_reference,
    evidence_ids_in_text,
    citation_scoped_evidence_records,
    resolve_evidence_id,
)

_CITATION_ONLY_BLOCK = re.compile(
    r"^(?:(?:\*\*|__)\s*)?"
    r"(?:【\s*(?:证据\s*)?ev_[A-Za-z0-9_-]+\s*】\s*)+"
    r"(?:(?:\*\*|__)\s*)?$"
)
_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[。！？!?])\s*(?![【\[]\s*(?:证据\s*)?ev_)(?![*_])|(?<=[】\]])\s*(?=[^\n])|\n+"
)
_EXPLICIT_DATE = re.compile(
    r"(?<!\d)(?:(?:19|20)\d{2}(?:[-/.]\d{1,2}(?:[-/.]\d{1,2})?)?|"
    r"(?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}日?)?)?|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*日?)(?!\d)"
)
_RELATIVE_TIME = re.compile(
    # ``current-event awareness`` is a fixed capability term, not a claim
    # that a cited data point is current. Keep the generic freshness check
    # from treating that source wording as a relative timestamp.
    r"(?:最新|当前|今日|今天|截至|本周|本月|latest|"
    r"current(?![-‐‑‒–— ]events?\s+awareness\b)|today|as\s+of)",
    re.IGNORECASE,
)
_RELATIVE_NEGATION = re.compile(
    r"(?:不宜|不应|不能|不可|无法|未能|没有|无|尚未|尚无|并非|不是|不代表|不等于|"
    r"缺少|缺乏|未返回|未知|不确定).{0,20}(?:最新|当前|今日|今天|截至|本周|本月|"
    r"latest|current|today|as\s+of)"
    r"|(?:最新|当前|今日|今天|截至|本周|本月|latest|current|today|as\s+of).{0,20}"
    r"(?:无法|不能|不可|未能|没有|无|尚未|尚无|并非|不是|不代表|不等于|缺少|缺乏|"
    r"未返回|未知|不确定)",
    re.IGNORECASE,
)
_FRESHNESS_DISCLAIMER = re.compile(
    r"(?:无法确认|无法保证|时效(?:性)?(?:无法确认|未知|不明|有限|受限|限制|不足)|"
    r"未返回[^。！？\n]{0,32}(?:时间|时间戳|quote_time|trade_time)|"
    r"不宜(?:直接)?(?:表述|称|当作)|不能(?:直接)?(?:表述|称|当作))",
    re.IGNORECASE,
)
# Do not extract an integer prefix from a decimal-valued expression (for
# example ROE-15.17 -> ROE-15). This is a lexical boundary, not an allowlist of
# financial terms; whole identifiers such as ABC-15 and 600438 still require
# matching source evidence.
_COMPACT_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]+[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*|\d{6,})(?![A-Za-z0-9_]|\.\d)")
_NON_ENTITY_IDENTIFIER = re.compile(
    r"^(?:Q[1-4](?:-Q[1-4])?|[1-4]Q(?:-[1-4]Q)?|"
    r"(?:MA|EMA|SMA|RSI|ATR|PE|PB|PEG)\d*|FY\d{2,4}|H[12])$",
    re.IGNORECASE,
)
_INFERENCE_MARKER = re.compile(r"(?:^|[\s：:])(?:推断|推测|inference|inferred)(?:[：:]|\s)", re.IGNORECASE)
_MATERIAL_SECTION_TERM = re.compile(
    r"(?:结论|判断|建议|摘要|总结|积极因素|正面因素|支持因素|风险因素|利好|利空|"
    r"recommendation|assessment|decision|takeaway|summary|conclusion|"
    r"positive factors?|risk factors?|strengths?|weaknesses?|pros?|cons?)",
    re.IGNORECASE,
)
_FOLLOW_UP_QUESTION = re.compile(
    r"(?:需要我|是否需要|要不要|如需|如果需要|我可以|还可以).{0,80}[?？]$",
    re.IGNORECASE,
)
_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
_TRANSPORT_TIME_FIELDS = frozenset(
    {
        "_fetched_at",
        "fetched_at",
        "retrieved_at",
        "retrieval_time",
        "completed_at",
        "created_at",
        "updated_at",
        "_updated_at",
        "started_at",
        "finished_at",
        "requested_at",
        "observed_at",
        "cache_time",
        "cached_at",
        "last_sync_at",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {
        "source_refs",
        "reference_links",
        "referenceLinks",
        "url",
        "link",
        "source_url",
        "requested_url",
        "final_url",
    }
)
_INHERITABLE_DATE = re.compile(
    r"(?:(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|"
    r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日?|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*日?)"
)
_REPORTING_PERIOD_YEAR = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*年\s*"
    r"(?:年度|年报|半年度|半年报|半年|中期报告|"
    r"第[一二三四]季度|[一二三四]季度|季度报告)"
)
_CURRENT_REPORT_PERIOD = re.compile(
    r"(?:本报告期|报告期|本期|current\s+(?:reporting\s+)?period)",
    re.IGNORECASE,
)
_PREVIOUS_COMPARABLE_PERIOD = re.compile(
    r"(?:上年同期|上年度同期|去年同期|上一年度同期|"
    r"same\s+period\s+(?:of\s+)?(?:the\s+)?(?:previous|prior|last)\s+year)",
    re.IGNORECASE,
)
_OMITTED = object()
_SUPPORT_OMIT_FIELDS = frozenset(
    field.lower() for field in _TRANSPORT_TIME_FIELDS | _PROVENANCE_FIELDS
)


def _id(item: Mapping[str, Any]) -> str:
    return str(item.get("evidence_id") or item.get("id") or "").strip()


def _short(value: Any, limit: int = 1_000) -> str:
    return str(value or "").strip()[:limit]


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _actual_source_refs(item: Mapping[str, Any]) -> list[str]:
    """Return real provider/source references rather than local tool labels."""
    return [
        ref
        for ref in _unique([str(value) for value in list(item.get("source_refs") or [])])
        if not ref.startswith("tool:")
    ]


def _entity_fields(item: Mapping[str, Any]) -> list[str]:
    raw = item.get("entities")
    if not isinstance(raw, Mapping):
        return []
    return [
        str(key)[:96]
        for key, value in raw.items()
        if str(key).strip().lower() not in _SENSITIVE_FIELDS
        and value not in (None, "", [], {})
    ][:24]


def _sanitize_support_value(value: Any, *, key: str | None = None) -> Any:
    """Keep source content while removing transport/provenance timestamps.

    A fetched-at timestamp is an observation about this service, not a date
    contained in the source.  Likewise, dates embedded in a URL or source
    reference must not satisfy a claim's explicit-time check.  The raw result
    remains untouched in the checkpoint; this sanitizer only feeds the
    mechanical comparison view.
    """
    normalized_key = str(key or "").strip().lower()
    if normalized_key in _SUPPORT_OMIT_FIELDS:
        return _OMITTED
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            child = _sanitize_support_value(raw_value, key=str(raw_key))
            if child is not _OMITTED:
                sanitized[str(raw_key)] = child
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            child
            for raw_value in value
            for child in [_sanitize_support_value(raw_value)]
            if child is not _OMITTED
        ]
    return value


def _support_text(item: Mapping[str, Any]) -> str:
    """Build an internal comparison view; it is never emitted to the client."""
    payload = {
        "entities": item.get("entities"),
        "data_time": item.get("data_time"),
        "data_time_note": item.get("data_time_note"),
        "source_refs": item.get("source_refs"),
        "result": item.get("result"),
    }
    sanitized = _sanitize_support_value(payload)
    return json.dumps(sanitized, ensure_ascii=False, default=str).casefold()


def _compact_time(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


def _time_mentions(claim: str) -> tuple[list[str], bool]:
    explicit = _unique(_EXPLICIT_DATE.findall(claim))
    if _FRESHNESS_DISCLAIMER.search(claim):
        return explicit, False
    relative = any(
        not _RELATIVE_NEGATION.search(
            claim[max(0, match.start() - 24) : min(len(claim), match.end() + 24)]
        )
        for match in _RELATIVE_TIME.finditer(claim)
    )
    return explicit, relative


def _is_heading_only(candidate: str) -> bool:
    """Do not audit a Markdown section label as if it were a data claim."""
    lines = [line.strip() for line in str(candidate or "").splitlines() if line.strip()]
    return bool(
        len(lines) == 1
        and re.match(r"^#{1,6}\s+", candidate)
        and not contains_evidence_reference(candidate)
    )


def _has_visible_claim_content(candidate: str) -> bool:
    """Ignore formatting-only fragments left by sentence splitting."""
    return bool(
        re.sub(
            r"[\s*_`#|:：,，。！？!?；;、()（）\[\]{}<>《》…—–-]+",
            "",
            str(candidate or ""),
        )
    )


def _supports_explicit_time(value: str, evidence: Sequence[Mapping[str, Any]]) -> bool:
    token = _compact_time(value)
    if not token:
        return True
    for item in evidence:
        support = _support_text(item)
        support_digits = _compact_time(support)
        if token in support_digits:
            return True
        if (
            len(token) == 4
            and token.isdigit()
            and _supports_previous_period_year(token, item)
        ):
            return True
    return False


def _supports_previous_period_year(year: str, item: Mapping[str, Any]) -> bool:
    """Resolve a prior-year label only from one source hit's own period metadata.

    Some filings label comparison columns ``上年同期`` instead of printing the
    calendar year in every table.  The exact retrieved hit may still establish
    that year when its document title identifies the reporting period.  Keep
    this separate from source ``data_time``: it does not assert freshness.
    """
    raw_result = item.get("result")
    if not isinstance(raw_result, Mapping):
        return False
    raw_hits = raw_result.get("results")
    hits = (
        [hit for hit in raw_hits if isinstance(hit, Mapping)]
        if isinstance(raw_hits, Sequence)
        and not isinstance(raw_hits, (str, bytes, bytearray))
        else [raw_result]
    )
    try:
        requested_year = int(year)
    except ValueError:
        return False

    for hit in hits:
        document_label = " ".join(
            str(hit.get(field) or "")
            for field in ("filename", "document_title", "title")
        )
        period_years = {
            int(match)
            for match in _REPORTING_PERIOD_YEAR.findall(document_label)
        }
        if requested_year + 1 not in period_years:
            continue

        source_text = " ".join(
            str(hit.get(field) or "")
            for field in ("text", "snippet", "page_content", "content")
        )
        if (
            _CURRENT_REPORT_PERIOD.search(source_text)
            and _PREVIOUS_COMPARABLE_PERIOD.search(source_text)
        ):
            return True
    return False


def _supports_relative_time(evidence: Sequence[Mapping[str, Any]]) -> bool:
    for item in evidence:
        if item.get("data_time_applicable") is False:
            return True
        if not item.get("data_time"):
            continue
        if item.get("freshness_unknown") is True or item.get("is_stale") is True:
            continue
        return True
    return False


def _is_inheritable_date(value: str) -> bool:
    """Only inherit a complete date, never a bare year or quarter token."""
    return bool(_INHERITABLE_DATE.fullmatch(str(value or "").strip()))


def _explicit_identifiers(claim: str) -> list[str]:
    """Extract mechanically checkable identifiers, not ordinary quoted prose.

    Quotation marks frequently carry a source's wording or a model's label.
    Treating every quoted phrase as an entity makes the validator reject valid
    prose for a lexical mismatch rather than a provenance problem.  Compact
    alphanumeric IDs and numeric codes remain checkable across all domains.
    """
    without_citations = _EVIDENCE_REFERENCE.sub("", claim)
    compact = _COMPACT_IDENTIFIER.findall(without_citations)
    return _unique(
        [value for value in compact if not _NON_ENTITY_IDENTIFIER.fullmatch(value)]
    )[:16]


def _is_source_note_block(block: str) -> bool:
    """Whether a block is an attribution that can support the block above it.

    Models commonly put one source line below an entire Markdown table or a
    short section instead of repeating the same citation on every row.  That
    is still an explicit citation boundary.  Do not treat arbitrary prose
    containing an evidence ID as a source note; doing so would let an
    unrelated paragraph borrow a later citation.
    """
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines or not contains_evidence_reference(block):
        return False
    citation_only = " ".join(lines)
    if len(lines) == 1 and _CITATION_ONLY_BLOCK.fullmatch(citation_only):
        return True
    first = lines[0]
    return bool(
        re.match(r"^(?:>|来源|资料来源|数据来源|source|citation)\s*", first, re.IGNORECASE)
    )


def _material_label_text(line: str) -> str | None:
    """Return the text of a standalone Markdown heading or bold label."""
    candidate = str(line or "").strip()
    heading = re.match(r"^#{1,6}\s+(?P<label>.+?)\s*$", candidate)
    if heading:
        return heading.group("label").strip()
    bold = re.match(
        r"^(?:\*\*|__)(?P<label>.+?)(?:\*\*|__)\s*:?[ \t]*$",
        candidate,
    )
    if bold:
        return bold.group("label").strip()
    return None


def _has_material_section_marker(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        return False
    label = _material_label_text(lines[0])
    return bool(label and _MATERIAL_SECTION_TERM.search(label))


def _is_material_section_label(block: str) -> bool:
    """Whether a block is a section label whose following block is in scope."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) != 1:
        return False
    label = _material_label_text(lines[0])
    if not label or not _MATERIAL_SECTION_TERM.search(label):
        return False
    # A full bold sentence such as ``**结论：当前估值...。**`` is itself a
    # claim, not a heading.  Audit it, but do not make the next section inherit
    # its evidence scope.
    if re.search(r"[。！？!?]", label):
        return False
    return label.rstrip().endswith((":", "：")) or len(label) <= 64


def _is_cited_scope_intro(block: str) -> bool:
    """Only a standalone heading may scope the structured block below it."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) != 1 or not contains_evidence_reference(lines[0]):
        return False
    line = lines[0]
    if re.match(r"^#{1,6}\s+", line):
        return True
    return bool(
        re.match(
            r"^(?:\*\*|__)[^*_]+(?:\*\*|__)\s*(?:【\s*(?:证据\s*)?ev_[^】]+】)?\s*$",
            line,
        )
    )


def _is_structured_block(block: str) -> bool:
    """Keep tables/lists together while auditing their shared source note."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        return False
    table_lines = sum(
        1
        for line in lines
        if line.startswith("|") and line.endswith("|")
    )
    list_lines = sum(
        1
        for line in lines
        if re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", line)
    )
    return table_lines >= 1 or list_lines >= 1 or _is_source_note_block(block)


def _logical_answer_blocks(answer: str) -> list[tuple[str, bool, bool]]:
    """Build citation-aware blocks before sentence-level auditing.

    A source note placed below a table/list/paragraph is a normal Markdown
    presentation pattern.  The old line-level splitter treated every dated
    table row as an independent uncited claim, even though the following
    source note clearly covered the table.  Merge only an explicit attribution
    block with its immediate predecessor; unrelated prose still cannot borrow
    a citation from later in the answer.
    """
    raw_blocks: list[str] = []
    current: list[str] = []
    for line in answer.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            raw_blocks.append("\n".join(current))
            current = []
    if current:
        raw_blocks.append("\n".join(current))

    blocks: list[tuple[str, bool, bool]] = []
    for block in raw_blocks:
        source_note = _is_source_note_block(block)
        if source_note and blocks:
            previous, _previous_structured, requires_evidence = blocks[-1]
            blocks[-1] = (
                previous + "\n" + block,
                True,
                requires_evidence,
            )
            continue
        table_or_material = any(
            line.strip().startswith("|") and line.strip().endswith("|")
            for line in block.splitlines()
        ) or _has_material_section_marker(block)
        blocks.append((block, _is_structured_block(block), table_or_material))

    # A material section label scopes only its immediate body block.  This
    # catches conclusions/recommendations even when the model omitted a
    # citation, while keeping the scope local to that section.
    scoped: list[tuple[str, bool, bool]] = []
    index = 0
    while index < len(blocks):
        block, structured, requires_evidence = blocks[index]
        if _is_material_section_label(block) and index + 1 < len(blocks):
            following, following_structured, _following_requires_evidence = blocks[index + 1]
            scoped.append((block + "\n" + following, following_structured, True))
            index += 2
            continue
        if (
            structured
            and scoped
            and not scoped[-1][1]
            and contains_evidence_reference(scoped[-1][0])
            and _is_cited_scope_intro(scoped[-1][0])
        ):
            previous, _previous_structured, previous_requires_evidence = scoped[-1]
            scoped[-1] = (
                previous + "\n" + block,
                True,
                previous_requires_evidence or requires_evidence,
            )
        else:
            scoped.append((block, structured, requires_evidence))
        index += 1
    return scoped


def _claim_fragments(answer: str) -> list[str]:
    """Return auditable answer units without letting time claims borrow evidence.

    Citation-bearing fragments are always material claims.  Factual tables and
    material conclusion/recommendation sections are material even when the
    model omitted a citation.  A fragment that makes an explicit or relative
    time assertion is material as well.  Keeping those fragments in the ledger
    is what prevents an uncited introduction such as ``基于最新资料`` from
    being accidentally validated by an unrelated dated citation later in the
    answer.
    """
    fragments: list[str] = []
    for block, structured, requires_evidence in _logical_answer_blocks(answer):
        if structured:
            candidates = [
                "\n".join(line.strip() for line in block.splitlines() if line.strip())
            ]
        else:
            candidates = _SENTENCE_BOUNDARY.split(block)
            # An inline citation at the end of a prose paragraph is the
            # paragraph's source boundary.  Preserve that scope instead of
            # making every preceding sentence appear uncited.  A citation in
            # a later, separate block is still not inherited.
            if (
                len(candidates) > 1
                and contains_evidence_reference(block)
                and not any(contains_evidence_reference(item) for item in candidates[:-1])
                and contains_evidence_reference(candidates[-1])
            ):
                candidates = [block]
        for raw in candidates:
            candidate = raw.strip()
            explicit_times, relative_time = _time_mentions(candidate)
            if (
                candidate
                and _has_visible_claim_content(candidate)
                and not _is_heading_only(candidate)
                and not _FOLLOW_UP_QUESTION.search(candidate)
                and (
                    requires_evidence
                    or contains_evidence_reference(candidate)
                    or explicit_times
                    or relative_time
                )
            ):
                fragments.append(candidate)
    # A malformed answer can place a citation immediately after a heading or
    # in a line without a sentence boundary.  Preserve it as one auditable
    # fragment rather than silently dropping the citation from the ledger.
    if not fragments and contains_evidence_reference(answer):
        fragments.append(answer.strip())
    return fragments


def _inherited_evidence_ids(
    explicit_times: Sequence[str],
    prior_cited_ids: Sequence[str],
    successful: Mapping[str, Mapping[str, Any]],
    *,
    relative_time: bool = False,
) -> list[str]:
    """Reuse a prior visible citation for an exact repeated date claim.

    A later caution paragraph often repeats a date already cited in the
    preceding source table.  Requiring the model to repeat the same ID on
    every warning makes otherwise traceable answers fail, while borrowing an
    unrelated later citation is unsafe.  Therefore inheritance is limited to
    dates that are mechanically present in an evidence item whose ID has
    already appeared earlier in the answer.
    """
    inheritable_times = [value for value in explicit_times if _is_inheritable_date(value)]
    if not inheritable_times or not prior_cited_ids:
        return []

    candidate_ids = list(prior_cited_ids)
    if relative_time:
        # A month/day-only phrase such as "7 月 13 日" can occur in an older
        # IPO date and in a newer announcement.  For a relative claim such as
        # "最新公告", prefer the already-cited evidence that carries a valid
        # source timestamp instead of the first lexical match.
        candidate_ids.sort(
            key=lambda evidence_id: 0
            if (
                successful.get(evidence_id, {}).get("data_time")
                and successful.get(evidence_id, {}).get("freshness_unknown") is not True
                and successful.get(evidence_id, {}).get("is_stale") is not True
            )
            else 1
        )

    selected: list[str] = []
    remaining = list(inheritable_times)
    for evidence_id in candidate_ids:
        item = successful.get(evidence_id)
        if item is None:
            continue
        supported = [
            value
            for value in remaining
            if _supports_explicit_time(value, [item])
        ]
        if not supported:
            continue
        selected.append(evidence_id)
        remaining = [value for value in remaining if value not in supported]
        if not remaining:
            break
    return selected if not remaining else []


def _resolve_evidence_ids(
    raw_ids: Sequence[Any],
    successful: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    """Resolve model references against the run-local successful evidence set."""
    resolved: list[str] = []
    unresolved: list[str] = []
    for raw_id in raw_ids:
        value = _short(raw_id, 160)
        if not value:
            continue
        canonical = resolve_evidence_id(value, successful)
        if canonical is None:
            unresolved.append(value)
        else:
            resolved.append(canonical)
    return _unique(resolved), _unique(unresolved)


def _validate_claim(
    *,
    fragment: str,
    evidence_ids: Sequence[str],
    unresolved_ids: Sequence[str],
    successful: Mapping[str, Mapping[str, Any]],
    results_by_action: Mapping[str, Mapping[str, Any]],
    issues: list[str],
    claim_kind: str,
    citation_mode: str,
    requires_evidence: bool,
    claim_id: str,
    section: str | None = None,
) -> dict[str, Any]:
    """Validate one claim after its source of truth has been identified.

    Both the legacy Markdown adapter and the structured answer path use this
    validator.  Only the former has to discover claim boundaries or citations
    from text; the latter supplies both explicitly.
    """
    aggregate_issues = issues
    issues = []
    normalized_evidence_ids = _unique(list(evidence_ids))
    normalized_unresolved_ids = _unique(list(unresolved_ids))
    if normalized_unresolved_ids:
        issues.append(
            "引用了不存在或失败的 evidence_id: "
            + ", ".join(normalized_unresolved_ids)
        )

    explicit_times, relative_time = _time_mentions(fragment)
    supporting = [
        successful[value]
        for value in normalized_evidence_ids
        if value in successful
    ]
    source_refs = _unique(
        [ref for item in supporting for ref in _actual_source_refs(item)]
    )
    entity_fields = _unique(
        [field for item in supporting for field in _entity_fields(item)]
    )
    related_results = [
        results_by_action.get(str(item.get("action_id") or "").strip())
        for item in supporting
    ]
    tool_success = bool(supporting) and all(
        result is not None and result.get("success") is True
        for result in related_results
    )
    source_ok = bool(source_refs)
    entity_scope_ok = bool(entity_fields) or bool(source_refs)
    temporal_claim = bool(explicit_times) or relative_time

    if requires_evidence and not normalized_evidence_ids:
        issues.append("回答片段没有关联有效 evidence_id: " + _short(fragment, 240))
    if requires_evidence and temporal_claim and not normalized_evidence_ids:
        issues.append("带有明确时间口径的结论没有紧邻的 evidence_id")

    # Context/disclaimer blocks are allowed to explain the run's reference
    # date or a freshness limitation without external evidence.  Only blocks
    # that require evidence (or explicitly cite one) perform source-time
    # matching; otherwise a harmless context line such as "截至今天" becomes
    # a false evidence failure.
    if requires_evidence or normalized_evidence_ids:
        unsupported_times = [
            value
            for value in explicit_times
            if not _supports_explicit_time(value, supporting)
        ]
        time_ok = True
        if requires_evidence:
            time_ok = bool(normalized_evidence_ids) if temporal_claim else True
        time_ok = time_ok and not unsupported_times and (
            not relative_time or _supports_relative_time(supporting)
        )
    else:
        unsupported_times = []
        time_ok = True

    identifiers = _explicit_identifiers(fragment)
    support_text = "\n".join(_support_text(item) for item in supporting)
    unsupported_identifiers = [
        value
        for value in identifiers
        if value.casefold() not in support_text
    ]
    entity_ok = (
        entity_scope_ok and not unsupported_identifiers
        if requires_evidence or normalized_evidence_ids
        else True
    )

    if supporting and not source_ok:
        issues.append("引用证据缺少真实来源: " + ", ".join(normalized_evidence_ids))
    if supporting and not tool_success:
        issues.append("引用证据未关联到成功工具结果: " + ", ".join(normalized_evidence_ids))
    if supporting and not entity_scope_ok:
        issues.append("引用证据缺少实体范围: " + ", ".join(normalized_evidence_ids))
    if unsupported_identifiers and (requires_evidence or normalized_evidence_ids):
        issues.append(
            "结论中的显式标识未出现在引用证据中: "
            + ", ".join(unsupported_identifiers)
        )
    if unsupported_times:
        issues.append("结论中的时间无法在引用证据中核对: " + ", ".join(unsupported_times))
    if (
        (requires_evidence or normalized_evidence_ids)
        and relative_time
        and not _supports_relative_time(supporting)
    ):
        issues.append("结论使用了当前/最新时间口径，但引用证据没有可用数据时间")

    claim = {
        "claim_id": claim_id,
        "text": fragment[:2_000],
        "kind": claim_kind,
        "evidence_ids": normalized_evidence_ids,
        "unresolved_evidence_ids": normalized_unresolved_ids,
        "citation_mode": citation_mode,
        "requires_evidence": requires_evidence,
        "entity_fields": entity_fields,
        "time_references": explicit_times,
        "uses_relative_time": relative_time,
        "checks": {
            "reference_integrity": not normalized_unresolved_ids,
            "tool_success": tool_success if requires_evidence or supporting else True,
            "source": source_ok if requires_evidence or supporting else True,
            "entity_scope": entity_ok,
            "time": time_ok,
        },
        "evidence": [
            {
                "evidence_id": _id(item),
                "tool_name": _short(item.get("tool_name"), 120),
                "data_time": _short(item.get("data_time"), 160) or None,
                "source_refs": _actual_source_refs(item)[:8],
            }
            for item in supporting
        ],
    }
    if section:
        claim["section"] = _short(section, 160)
    claim["issues"] = _unique(issues)
    aggregate_issues.extend(claim["issues"])
    return claim


def build_claim_evidence_ledger(
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
    tool_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project auditable answer fragments and validate their support.

    The validation is intentionally mechanical and provider-neutral:

    * cited IDs must come from successful factual evidence;
    * every cited evidence item must retain a real source and a successful
      matching tool result;
    * the evidence must retain request/entity scope;
    * explicit dates in the claim must occur in supporting evidence; and
    * current/latest claims require a non-stale source data timestamp.

    It does not attempt to infer a financial thesis or force an answer into a
    fixed domain template.  The mapping is retained for audit and passed back
    to the model only as concise repair feedback when one of these invariants
    is broken.
    """
    successful = {
        _id(item): item
        for item in citation_scoped_evidence_records(evidence)
        if evidence_record_is_eligible(item) and _id(item)
    }
    results_by_action = {
        str(item.get("action_id") or item.get("id") or "").strip(): item
        for item in tool_results
        if str(item.get("action_id") or item.get("id") or "").strip()
    }
    issues: list[str] = []
    claims: list[dict[str, Any]] = []
    unresolved_evidence_ids: list[str] = []

    cited_evidence_ids, unresolved_in_answer = _resolve_evidence_ids(
        evidence_ids_in_text(answer),
        successful,
    )
    unresolved_evidence_ids = _unique(unresolved_in_answer)
    if unresolved_in_answer:
        issues.append(
            "引用了不存在或失败的 evidence_id: "
            + ", ".join(unresolved_in_answer)
        )
    prior_cited_ids: list[str] = []
    for index, fragment in enumerate(_claim_fragments(answer), start=1):
        direct_evidence_ids, unresolved_in_fragment = _resolve_evidence_ids(
            evidence_ids_in_text(fragment),
            successful,
        )
        unresolved_evidence_ids = _unique(
            [*unresolved_evidence_ids, *unresolved_in_fragment]
        )
        if unresolved_in_fragment:
            issues.append(
                "引用了不存在或失败的 evidence_id: "
                + ", ".join(unresolved_in_fragment)
            )
        explicit_times, relative_time = _time_mentions(fragment)
        inherited_ids = _inherited_evidence_ids(
            explicit_times,
            prior_cited_ids,
            successful,
            relative_time=relative_time,
        )
        evidence_ids = _unique([*direct_evidence_ids, *inherited_ids])
        prior_cited_ids = _unique([*prior_cited_ids, *direct_evidence_ids])
        claims.append(
            _validate_claim(
                fragment=fragment,
                evidence_ids=evidence_ids,
                unresolved_ids=unresolved_in_fragment,
                successful=successful,
                results_by_action=results_by_action,
                issues=issues,
                claim_kind="inference" if _INFERENCE_MARKER.search(fragment) else "fact",
                citation_mode=(
                    "direct" if direct_evidence_ids else "inherited" if inherited_ids else "missing"
                ),
                requires_evidence=True,
                claim_id=f"claim_{index}",
            )
        )

    return {
        "claims": claims,
        "issues": _unique(issues),
        "cited_evidence_ids": cited_evidence_ids,
        "unresolved_evidence_ids": unresolved_evidence_ids,
        "fact_claim_count": sum(1 for item in claims if item["kind"] == "fact"),
        "inference_claim_count": sum(1 for item in claims if item["kind"] == "inference"),
    }


def build_structured_claim_evidence_ledger(
    blocks: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    tool_results: Sequence[Mapping[str, Any]],
    *,
    profile: str = "research",
) -> dict[str, Any]:
    """Validate claims supplied by the structured final-answer contract.

    Block boundaries and evidence IDs are model output fields validated by the
    Pydantic response schema.  This path never parses Markdown to discover
    headings, tables, lists, or follow-up questions.  The default strict
    ``research`` profile preserves the pre-profile contract for historical
    callers and checkpoint data.
    """
    successful = {
        _id(item): item
        for item in citation_scoped_evidence_records(evidence)
        if evidence_record_is_eligible(item) and _id(item)
    }
    results_by_action = {
        str(item.get("action_id") or item.get("id") or "").strip(): item
        for item in tool_results
        if str(item.get("action_id") or item.get("id") or "").strip()
    }
    issues: list[str] = []
    claims: list[dict[str, Any]] = []
    cited_evidence_ids: list[str] = []
    unresolved_evidence_ids: list[str] = []

    normalized_profile = str(profile or "research").strip().lower()
    if normalized_profile not in {"general", "research"}:
        normalized_profile = "research"

    for index, block in enumerate(blocks, start=1):
        content = _short(block.get("content"), 2_000)
        if not content:
            continue
        kind = _short(block.get("kind"), 32).lower() or "fact"
        action_refs = block.get("action_refs")
        referenced_actions = (
            [item for item in action_refs if isinstance(item, Mapping)]
            if isinstance(action_refs, Sequence)
            and not isinstance(action_refs, (str, bytes, bytearray))
            else []
        )
        supported_action_ids = []
        for reference in referenced_actions:
            action_id = str(reference.get("action_id") or "").strip()
            record = results_by_action.get(action_id)
            if not action_id or not isinstance(record, Mapping):
                continue
            record_success = record.get("success") is True
            expected_status = "completed" if record_success else "failed"
            if (
                str(record.get("effect") or "read").strip().lower() == "side_effect"
                and str(reference.get("effect") or "read").strip().lower() == "side_effect"
                and reference.get("success") is record_success
                and str(reference.get("status") or "").strip().lower() == expected_status
            ):
                supported_action_ids.append(action_id)
        raw_ids = (
            block.get("evidence_ids")
            if isinstance(block.get("evidence_ids"), Sequence)
            and not isinstance(block.get("evidence_ids"), (str, bytes, bytearray))
            else []
        )
        # Action-result blocks are supported by the server-owned action record,
        # not by retrieved document text. They must never borrow PDF citations.
        direct_ids, unresolved = (
            ([], [])
            if kind == "action_result"
            else _resolve_evidence_ids(raw_ids, successful)
        )
        cited_evidence_ids = _unique([*cited_evidence_ids, *direct_ids])
        unresolved_evidence_ids = _unique([*unresolved_evidence_ids, *unresolved])
        # ``research`` retains the old fail-closed rule: every content block
        # other than context/disclaimer is material. Action-result blocks use
        # a separately validated server-owned action reference. ``general``
        # adds a neutral ``answer`` block for explanations that do not rely on
        # external evidence. Once a general run has produced readable
        # evidence, an answer block must cite it as well; this prevents the
        # profile field from hiding tool-backed facts.
        if kind == "action_result":
            requires_evidence = False
        elif normalized_profile == "research":
            requires_evidence = kind not in {"context", "disclaimer"}
        elif kind in {"context", "disclaimer"}:
            requires_evidence = False
        elif kind == "answer":
            requires_evidence = bool(successful)
        else:
            requires_evidence = True
        claim = _validate_claim(
            fragment=content,
            evidence_ids=direct_ids,
            unresolved_ids=unresolved,
            successful=successful,
            results_by_action=results_by_action,
            issues=issues,
            claim_kind=kind,
            citation_mode="structured" if raw_ids else "missing",
            requires_evidence=requires_evidence,
            claim_id=f"claim_{index}",
            section=_short(block.get("section"), 160) or None,
        )
        if kind == "action_result":
            action_reference_valid = bool(supported_action_ids)
            claim["action_ids"] = _unique(supported_action_ids)
            claim["checks"]["action_reference"] = action_reference_valid
            if not action_reference_valid:
                action_issue = (
                    f"第 {index} 个动作结果区块没有引用本轮成功或失败的副作用动作记录"
                )
                claim["issues"] = _unique([*claim.get("issues", []), action_issue])
                issues.append(action_issue)
        claims.append(claim)

    return {
        "claims": claims,
        "issues": _unique(issues),
        "cited_evidence_ids": cited_evidence_ids,
        "unresolved_evidence_ids": unresolved_evidence_ids,
        "fact_claim_count": sum(1 for item in claims if item["kind"] == "fact"),
        "inference_claim_count": sum(1 for item in claims if item["kind"] == "inference"),
    }


__all__ = ["build_claim_evidence_ledger", "build_structured_claim_evidence_ledger"]
