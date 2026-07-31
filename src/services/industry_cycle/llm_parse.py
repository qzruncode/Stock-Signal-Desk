# -*- coding: utf-8 -*-
"""LLM response parsing and partial-streaming JSON extraction helpers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from .normalization import _compact_text, _list_of_dicts

logger = logging.getLogger(__name__)


def _extract_json_object_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        json.loads(candidate)
        return candidate
    except Exception:
        logger.error("[IndustryCycle] _extract_exact_json_segment failed", exc_info=True)
        return None


def _strip_markdown_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_llm_json_payload(raw_text: str, response_text: str) -> Optional[dict[str, Any]]:
    candidates: list[str] = []
    for text in (raw_text, response_text):
        normalized = _strip_markdown_code_fences(text)
        if normalized:
            candidates.append(normalized)
        extracted = _extract_json_object_from_text(normalized)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
        wrapped = normalized.strip().strip(",")
        if wrapped.startswith('"') and ":" in wrapped:
            repaired = "{" + wrapped + "}"
            if repaired not in candidates:
                candidates.append(repaired)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _pick_theme_detail(themes: Any, match: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not themes or not match:
        return None
    target = _compact_text(match.get("name"))
    if not target:
        return None
    for item in _list_of_dicts(themes):
        name = _compact_text(item.get("name"))
        if not name:
            continue
        if name == target or target in name or name in target:
            return item
    return None


def _extract_partial_json_string_field(raw_text: str, field_name: str) -> Optional[str]:
    marker = f'"{field_name}"'
    field_pos = raw_text.find(marker)
    if field_pos < 0:
        return None
    colon_pos = raw_text.find(":", field_pos + len(marker))
    if colon_pos < 0:
        return None

    quote_pos = None
    for index in range(colon_pos + 1, len(raw_text)):
        if raw_text[index] == '"':
            quote_pos = index
            break
        if not raw_text[index].isspace():
            return None
    if quote_pos is None:
        return None

    chars: list[str] = []
    escaping = False
    closed = False
    for index in range(quote_pos + 1, len(raw_text)):
        ch = raw_text[index]
        if escaping:
            chars.append("\\" + ch)
            escaping = False
            continue
        if ch == "\\":
            escaping = True
            continue
        if ch == '"':
            closed = True
            break
        chars.append(ch)

    fragment = "".join(chars)
    if not fragment and not closed:
        return None

    try:
        if closed:
            return json.loads(f'"{fragment}"')
        repaired = fragment.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
        return repaired.strip() or None
    except Exception:
        logger.error("[IndustryCycle] _repair_malformed_json failed", exc_info=True)
        return None


def _extract_partial_json_number_field(raw_text: str, field_name: str) -> Optional[float]:
    pattern = rf'"{re.escape(field_name)}"\s*:\s*(-?\d+(?:\.\d+)?)'
    match = re.search(pattern, raw_text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None
