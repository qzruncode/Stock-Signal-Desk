"""Server-owned evidence merge primitives for the research team.

Worker results are deliberately small handoffs.  This module turns their
evidence references and the parent reducer's records into one canonical,
bounded catalog before any reviewer is allowed to compare claims.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.agent.langgraph_runtime.evidence_identity import evidence_id_from_record
from src.tools.base import evidence_record_is_eligible

from .contracts import EvidenceMerge


def _record_key(record: Mapping[str, Any]) -> str:
    return evidence_id_from_record(record)


def _merge_record(existing: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Merge duplicate observations without allowing worker text to win over metadata."""
    merged = dict(existing)
    for key, value in incoming.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    owners = list(dict.fromkeys([*(existing.get("team_agent_ids") or []), *(incoming.get("team_agent_ids") or [])]))
    if owners:
        merged["team_agent_ids"] = owners[:8]
    return merged


def _canonical_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the metadata reviewers use for entity, time, and source scope."""
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    entity = next(
        (
            str(record.get(key) or result.get(key) or "").strip()
            for key in ("entity", "entity_id", "symbol", "stock_code", "ticker", "company")
            if str(record.get(key) or result.get(key) or "").strip()
        ),
        "",
    )
    source_value = record.get("source") or result.get("source")
    source = dict(source_value) if isinstance(source_value, Mapping) else {}
    source_id = str(
        record.get("source_id")
        or source.get("id")
        or source.get("source_id")
        or ""
    ).strip()
    provider = str(record.get("provider") or source.get("provider") or "").strip()
    source_refs = [
        str(value).strip()
        for value in [*(record.get("source_refs") or []), *(source.get("refs") or [])]
        if str(value).strip()
    ]
    data_time = str(
        record.get("data_time")
        or record.get("content_time")
        or result.get("data_time")
        or result.get("content_time")
        or ""
    ).strip()
    return {
        "entity": entity or None,
        "time": data_time or None,
        "time_provenance": str(record.get("data_time_provenance") or "unknown").strip() or "unknown",
        "source": {
            "id": source_id or None,
            "provider": provider or None,
            "refs": list(dict.fromkeys(source_refs))[:12],
        },
    }


def _canonicalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    projected = dict(record)
    metadata = _canonical_metadata(projected)
    projected["team_canonical_metadata"] = metadata
    projected.setdefault("entity_scope", metadata["entity"])
    projected.setdefault("source_scope", metadata["source"])
    projected.setdefault("time_scope", metadata["time"])
    return projected


def merge_worker_evidence(
    evidence: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> tuple[EvidenceMerge, list[dict[str, Any]]]:
    """Build a deterministic evidence catalog and typed merge summary.

    The function never invents an evidence id.  Result references that do not
    exist in the parent evidence reducer are treated as missing handoff data,
    which makes later conflict/review nodes fail closed.
    """

    by_id: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for raw in evidence:
        if not isinstance(raw, Mapping):
            continue
        evidence_id = _record_key(raw)
        if not evidence_id:
            continue
        candidate = _canonicalize_record(raw)
        if evidence_id in by_id:
            duplicate_count += 1
            by_id[evidence_id] = _merge_record(by_id[evidence_id], candidate)
        else:
            by_id[evidence_id] = candidate

    catalog = list(by_id.values())[:80]
    known_ids = set(by_id)
    worker_evidence: dict[str, list[str]] = {}
    finding_evidence: dict[str, list[str]] = {}
    missing_task_ids: list[str] = []
    invalid_evidence_ids: list[str] = []
    limitations: list[str] = []
    for raw_result in results:
        if not isinstance(raw_result, Mapping):
            continue
        task_id = str(raw_result.get("task_id") or raw_result.get("id") or "").strip()
        requested_ids = [str(value).strip() for value in raw_result.get("evidence_ids") or [] if str(value).strip()]
        resolved_ids = list(dict.fromkeys(value for value in requested_ids if value in known_ids))
        invalid_ids = [value for value in requested_ids if value not in known_ids]
        invalid_evidence_ids.extend(invalid_ids)
        if task_id:
            worker_evidence[task_id] = resolved_ids[:80]
        for index, refs in enumerate(raw_result.get("finding_evidence_refs") or []):
            finding_key = f"{task_id}:{index + 1}" if task_id else f"unknown:{index + 1}"
            finding_refs = [str(value).strip() for value in refs or [] if str(value).strip()]
            finding_evidence[finding_key] = list(dict.fromkeys(value for value in finding_refs if value in known_ids))[:24]
            invalid_evidence_ids.extend(value for value in finding_refs if value not in known_ids)
        if invalid_ids:
            limitations.append(f"{task_id or '未知任务'}引用了不存在的证据编号，已拒绝这些引用。")
        if str(raw_result.get("status") or "") != "completed":
            if task_id:
                missing_task_ids.append(task_id)
            continue
        if not resolved_ids:
            if task_id:
                missing_task_ids.append(task_id)
            limitations.append(f"{task_id or '未知任务'}没有返回可关联的证据编号。")

    eligible_ids = [evidence_id for evidence_id, record in by_id.items() if evidence_record_is_eligible(record)]
    if not catalog:
        status = "failed"
        limitations.append("本轮没有形成可供复核的 canonical evidence catalog。")
    elif missing_task_ids or invalid_evidence_ids or len(eligible_ids) != len(catalog):
        status = "partial"
        if invalid_evidence_ids:
            limitations.append("部分 worker 证据引用无法与服务端 canonical catalog 对齐。")
        if len(eligible_ids) != len(catalog):
            limitations.append("部分观察没有通过证据可用性校验，不能支持最终事实结论。")
    else:
        status = "completed"

    summary = (
        f"已合并 {len(catalog)} 条证据，{len(eligible_ids)} 条可用于结论，" f"覆盖 {len(worker_evidence)} 个 worker。"
    )
    merge = EvidenceMerge(
        status=status,
        summary=summary,
        evidence_ids=eligible_ids[:80],
        worker_evidence=worker_evidence,
        finding_evidence=finding_evidence,
        missing_task_ids=list(dict.fromkeys(missing_task_ids))[:8],
        invalid_evidence_ids=list(dict.fromkeys(invalid_evidence_ids))[:24],
        limitations=list(dict.fromkeys(limitations))[:8],
        duplicate_count=duplicate_count,
    )
    for record in catalog:
        record.setdefault("team_canonical", True)
        record.setdefault("team_agent_ids", [str(record.get("agent_id") or "")])
    return merge, catalog


__all__ = ["merge_worker_evidence"]
