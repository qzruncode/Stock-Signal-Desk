"""Citation checks for page-addressable PDF retrieval evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from src.tools.base import citation_scoped_evidence_records, evidence_record_is_eligible


_PAGE_REFERENCE_PATTERNS = (
    re.compile(r"第\s*(\d+)\s*(?:(?:-|–|—|~|～|至|到)\s*(\d+)\s*)?页"),
    re.compile(r"\bpages?\s+(\d+)(?:\s*(?:-|–|—|~|to)\s*(\d+))?\b", re.IGNORECASE),
    re.compile(r"\bp\.\s*(\d+)(?:\s*(?:-|–|—|~)\s*(\d+))?\b", re.IGNORECASE),
)
_MAX_PAGE_RANGE_SIZE = 200


def _pages_in_text(value: Any) -> set[int]:
    pages: set[int] = set()
    text = str(value or "")
    for pattern in _PAGE_REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            start = int(match.group(1))
            end = int(match.group(2) or start)
            if start < 1 or end < start or end - start > _MAX_PAGE_RANGE_SIZE:
                continue
            pages.update(range(start, end + 1))
    return pages


def _result_pages(evidence: Mapping[str, Any]) -> set[int]:
    result = evidence.get("result")
    if not isinstance(result, Mapping):
        return set()
    pages: set[int] = set()
    if result.get("page_start") is not None:
        try:
            start = int(result.get("page_start") or 0)
            end = int(result.get("page_end") or start)
        except (TypeError, ValueError):
            start = end = 0
        if 1 <= start <= end and end - start <= _MAX_PAGE_RANGE_SIZE:
            return set(range(start, end + 1))
    hits = result.get("results")
    if not isinstance(hits, (list, tuple)):
        return pages
    for hit in hits:
        if not isinstance(hit, Mapping):
            continue
        try:
            start = int(hit.get("page_start") or 0)
            end = int(hit.get("page_end") or start)
        except (TypeError, ValueError):
            continue
        if start < 1 or end < start or end - start > _MAX_PAGE_RANGE_SIZE:
            continue
        pages.update(range(start, end + 1))
    return pages


def _hit_pages(evidence: Mapping[str, Any]) -> set[int]:
    result = evidence.get("result")
    if not isinstance(result, Mapping):
        return set()
    try:
        start = int(result.get("page_start") or 0)
        end = int(result.get("page_end") or start)
    except (TypeError, ValueError):
        return set()
    if start < 1 or end < start or end - start > _MAX_PAGE_RANGE_SIZE:
        return set()
    return set(range(start, end + 1))


def _exact_text_match_score(claim: str, passage: str) -> int:
    """Find a long, verbatim run shared by a claim and one retrieved PDF hit."""
    claim_words = re.findall(r"[a-z0-9]+", claim.casefold())
    passage_words = re.findall(r"[a-z0-9]+", passage.casefold())
    max_width = min(len(claim_words), len(passage_words))
    for width in range(max_width, 5, -1):
        passage_windows = {
            tuple(passage_words[index : index + width])
            for index in range(len(passage_words) - width + 1)
        }
        if any(
            tuple(claim_words[index : index + width]) in passage_windows
            for index in range(len(claim_words) - width + 1)
        ):
            return width

    claim_cjk = "".join(re.findall(r"[\u3400-\u9fff]+", claim))
    passage_cjk = "".join(re.findall(r"[\u3400-\u9fff]+", passage))
    for width in range(min(len(claim_cjk), len(passage_cjk), 64), 9, -1):
        passage_windows = {
            passage_cjk[index : index + width]
            for index in range(len(passage_cjk) - width + 1)
        }
        if any(
            claim_cjk[index : index + width] in passage_windows
            for index in range(len(claim_cjk) - width + 1)
        ):
            return width
    return 0


def repair_pdf_citations_from_exact_text(
    blocks: Iterable[Any],
    evidence: Iterable[Any],
) -> tuple[list[dict[str, Any]], int]:
    """Bind a PDF claim to the retrieved hit containing its exact quoted text.

    The model's numbered source choice remains the default. This narrowly
    repairs it only when a substantial verbatim passage identifies a stronger
    hit in this run; page-only blocks may inherit that exact hit on the same
    page. No embedding score or model judgment is treated as citation proof.
    """
    normalized_blocks = [dict(item) for item in blocks if isinstance(item, Mapping)]
    hits = [
        item
        for item in citation_scoped_evidence_records(evidence)
        if item.get("citation_item") is True
        and str(item.get("tool_name") or "").strip() == "search_knowledge_base"
        and evidence_record_is_eligible(item)
        and str(item.get("evidence_id") or item.get("id") or "").strip()
    ]
    if not normalized_blocks or not hits:
        return normalized_blocks, 0

    passages = {
        str(item.get("evidence_id") or item.get("id") or ""): "\n".join(
            str((item.get("result") or {}).get(key) or "")
            for key in ("snippet", "summary", "text", "section")
            if isinstance(item.get("result"), Mapping)
        )
        for item in hits
    }
    hit_by_id = {
        str(item.get("evidence_id") or item.get("id") or ""): item
        for item in hits
    }
    exact_matches: dict[int, list[tuple[int, str]]] = {}
    trusted_page_ids: dict[int, set[str]] = {}

    for index, block in enumerate(normalized_blocks):
        claim = "\n".join(str(block.get(key) or "") for key in ("section", "content"))
        referenced_pages = _pages_in_text(claim)
        scored: list[tuple[int, str]] = []
        for evidence_id, passage in passages.items():
            score = _exact_text_match_score(claim, passage)
            if not score:
                continue
            pages = _hit_pages(hit_by_id[evidence_id])
            if referenced_pages and not referenced_pages.issubset(pages):
                continue
            scored.append((score, evidence_id))
        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]))
            exact_matches[index] = scored
            for _score, evidence_id in scored:
                for page in _hit_pages(hit_by_id[evidence_id]):
                    trusted_page_ids.setdefault(page, set()).add(evidence_id)

    remapped = 0
    for index, block in enumerate(normalized_blocks):
        selected: list[str] = []
        if exact_matches.get(index):
            selected = [exact_matches[index][0][1]]
        else:
            claim = "\n".join(str(block.get(key) or "") for key in ("section", "content"))
            referenced_pages = _pages_in_text(claim)
            if referenced_pages:
                matching_pages = [
                    evidence_id
                    for page in sorted(referenced_pages)
                    for evidence_id in sorted(trusted_page_ids.get(page, set()))
                    if referenced_pages.issubset(_hit_pages(hit_by_id[evidence_id]))
                ]
                selected = list(dict.fromkeys(matching_pages[:1]))
        if not selected:
            continue
        raw_ids = block.get("evidence_ids")
        current_ids = (
            [str(value).strip() for value in raw_ids if str(value).strip()]
            if isinstance(raw_ids, (list, tuple))
            else []
        )
        if current_ids == selected and not block.get("source_ids"):
            continue
        block["evidence_ids"] = selected
        # Numeric source slots would otherwise re-resolve to the model's old,
        # mismatched hit when the structured answer is rendered again.
        block.pop("source_ids", None)
        remapped += 1
    return normalized_blocks, remapped


def has_current_knowledge_base_search(tool_results: Iterable[Any]) -> bool:
    """Return true only after this Agent turn actually called the PDF tool.

    Native LangChain tool results carry ``model_tool_call_id``, including
    bounded failed observations.
    """
    return any(
        isinstance(item, Mapping)
        and str(item.get("tool_name") or "").strip() == "search_knowledge_base"
        and bool(str(item.get("model_tool_call_id") or "").strip())
        for item in tool_results
    )


def validate_pdf_page_references(
    blocks: Iterable[Any],
    evidence: Iterable[Any],
) -> list[dict[str, Any]]:
    """Check explicit page references against the cited PDF search results.

    ``blocks`` must contain canonical ``evidence_ids`` (the answer contract
    resolves its numeric source slots before calling this function).
    """
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for item in citation_scoped_evidence_records(evidence):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("tool_name") or "").strip() != "search_knowledge_base":
            continue
        evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
        if evidence_id:
            evidence_by_id[evidence_id] = item

    issues: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            continue
        referenced_pages = _pages_in_text(
            "\n".join(
                str(block.get(key) or "")
                for key in ("section", "content")
            )
        )
        if not referenced_pages:
            continue
        raw_ids = block.get("evidence_ids")
        cited_ids = (
            [str(value).strip() for value in raw_ids if str(value).strip()]
            if isinstance(raw_ids, (list, tuple))
            else []
        )
        cited_pdf = [evidence_by_id[item] for item in cited_ids if item in evidence_by_id]
        available_pages = set().union(*(_result_pages(item) for item in cited_pdf)) if cited_pdf else set()
        missing_pages = sorted(referenced_pages - available_pages)
        if missing_pages:
            issues.append(
                {
                    "block_index": index,
                    "referenced_pages": sorted(referenced_pages),
                    "available_pages": sorted(available_pages),
                    "missing_pages": missing_pages,
                    "evidence_ids": cited_ids,
                }
            )
    return issues


__all__ = [
    "has_current_knowledge_base_search",
    "repair_pdf_citations_from_exact_text",
    "validate_pdf_page_references",
]
