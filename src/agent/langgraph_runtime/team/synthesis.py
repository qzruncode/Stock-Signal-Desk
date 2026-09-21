"""Internal research receipts and model-owned answer coverage checks.

Worker handoffs belong to the review disclosure, never to a final-answer
fallback. Coverage checks qualify publication without manufacturing prose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.tools.base import evidence_record_is_eligible

from ..answer_contract import structured_answer_blocks


_ROLE_LABELS = {
    "market": "行情",
    "fundamental": "基本面",
    "news": "新闻",
}

_ROLE_SECTION_LABELS = {
    "market": "行情核验",
    "fundamental": "基本面核验",
    "news": "新闻核验",
}

_ROLE_KEYWORDS = {
    "market": ("行情", "市场", "技术", "价格", "交易", "k线", "均线", "资金流", "quote", "market"),
    "fundamental": ("基本面", "财务", "估值", "经营", "业绩", "利润", "营收", "分红", "资产", "fundamental"),
    "news": ("新闻", "公告", "研报", "事件", "资讯", "渠道", "监管", "research", "news"),
}


def _text(value: Any, limit: int = 2_400) -> str:
    return str(value or "").strip()[:limit]


def _required_experts(state: Mapping[str, Any]) -> list[str]:
    plan = state.get("team_plan") if isinstance(state.get("team_plan"), Mapping) else {}
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else []
    expert_ids: list[str] = []
    for raw_task in tasks or []:
        if not isinstance(raw_task, Mapping):
            continue
        expert_id = str(raw_task.get("agent_id") or "").strip().lower()
        if expert_id and expert_id not in expert_ids:
            expert_ids.append(expert_id)
    return expert_ids


def team_synthesis_contract_issues(
    answer: Any,
    *,
    required_experts: Sequence[str],
) -> list[str]:
    """Return publication-blocking omissions in a Team reviewer answer.

    StructuredAgentAnswer validates shape and evidence references, but it does
    not know the Team plan's required domain coverage.  Coverage is therefore
    checked here without judging or inventing the underlying facts.
    """
    blocks = structured_answer_blocks(answer)
    if not blocks:
        return ["综合器没有返回任何可发布区块。"]

    searchable = [
        f"{_text(block.get('section'), 180)}\n{_text(block.get('content'), 2_000)}".lower()
        for block in blocks
    ]
    issues: list[str] = []
    for expert_id in required_experts:
        keywords = _ROLE_KEYWORDS.get(expert_id, (_ROLE_LABELS.get(expert_id, expert_id), expert_id.replace('_', ' ')))
        if not any(any(keyword.lower() in text for keyword in keywords) for text in searchable):
            issues.append(f"综合器没有保留{_ROLE_LABELS.get(expert_id, expert_id)}方向的实质性区块。")
    return issues


def _known_evidence_ids(evidence: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        str(item.get("evidence_id") or item.get("id") or "").strip()
        for item in evidence
        if isinstance(item, Mapping)
        and evidence_record_is_eligible(item)
        and str(item.get("evidence_id") or item.get("id") or "").strip()
    }


def _worker_evidence_ids(result: Mapping[str, Any], known: set[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for value in (
                str(item).strip()
                for item in result.get("evidence_ids") or []
                if str(item).strip()
            )
            if value in known
        )
    )[:24]


def _worker_content(result: Mapping[str, Any]) -> tuple[str, str]:
    status = str(result.get("status") or "partial").strip().lower()
    status_label = {
        "completed": "已完成",
        "partial": "部分完成",
        "failed": "未完成",
    }.get(status, "部分完成")
    lines = [f"核验状态：{status_label}"]
    summary = _text(result.get("summary"), 2_400)
    if summary:
        lines.append(summary)
    findings = [
        _text(item, 1_200)
        for item in result.get("findings") or []
        if _text(item, 1_200)
    ][:8]
    if findings:
        lines.append("已交接观察：\n" + "\n".join(f"- {item}" for item in findings))
    limitations = [
        _text(item, 700)
        for item in result.get("limitations") or []
        if _text(item, 700)
    ][:6]
    open_questions = [
        _text(item, 700)
        for item in result.get("open_questions") or []
        if _text(item, 700)
    ][:6]
    if limitations:
        lines.append("已知限制：\n" + "\n".join(f"- {item}" for item in limitations))
    if open_questions:
        lines.append("仍待确认：\n" + "\n".join(f"- {item}" for item in open_questions))
    if status != "completed":
        lines.append("该方向存在未完成交接，以下内容只代表本轮已经取得的结果。")
    content = "\n\n".join(lines).strip()[:12_000]
    return content, status


def build_team_review_report(
    state: Mapping[str, Any],
    *,
    evidence: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an internal receipt; callers must not publish it as an answer."""
    evidence_records = [item for item in (evidence if evidence is not None else state.get("evidence") or []) if isinstance(item, Mapping)]
    known_ids = _known_evidence_ids(evidence_records)
    results = [item for item in state.get("team_results") or [] if isinstance(item, Mapping)]
    # ``AgentResult.agent_id`` is the namespaced runtime identity
    # (``team:<run>:<expert>:<task>``), while the plan is keyed by the
    # registered expert. Index reports by their canonical task id first;
    # otherwise a valid report can be mistaken for a missing domain and the
    # fallback publisher will emit three empty sections.
    results_by_task_id = {
        str(item.get("task_id") or item.get("id") or "").strip(): item
        for item in results
        if str(item.get("task_id") or item.get("id") or "").strip()
    }
    by_expert: dict[str, Mapping[str, Any]] = {}
    for item in results:
        for raw_key in (
            item.get("expert_id"),
            item.get("agent_id"),
        ):
            key = str(raw_key or "").strip().lower()
            if key and key not in by_expert:
                by_expert[key] = item
    expert_ids = _required_experts(state) or list(by_expert)
    blocks: list[dict[str, Any]] = []
    for expert_id in expert_ids:
        task = next(
            (
                item
                for item in state.get("team_plan", {}).get("tasks", [])
                if isinstance(item, Mapping)
                and str(item.get("agent_id") or "").strip().lower() == expert_id
            ),
            {},
        ) if isinstance(state.get("team_plan"), Mapping) else {}
        task_id = str(task.get("task_id") or "").strip()
        result = results_by_task_id.get(task_id) if task_id else None
        if result is None:
            result = by_expert.get(expert_id)
        label = _text(
            (result or {}).get("agent_display_name")
            or task.get("agent_display_name")
            or _ROLE_LABELS.get(expert_id, expert_id),
            160,
        ) or "领域"
        section_label = _ROLE_SECTION_LABELS.get(expert_id, f"{label}核验")
        if result is None:
            blocks.append(
                {
                    "section": section_label,
                    "kind": "disclaimer",
                    "content": f"本轮没有收到{label}方向的 worker 交接。",
                    "evidence_ids": [],
                }
            )
            continue
        content, status = _worker_content(result)
        ids = _worker_evidence_ids(result, known_ids)
        blocks.append(
            {
                    "section": section_label,
                    "kind": "fact" if ids and status != "failed" else "disclaimer",
                    "content": content or f"{label}方向没有返回可发布观察。",
                "evidence_ids": ids,
            }
        )

    consensus = state.get("team_consensus")
    if isinstance(consensus, Mapping) and _text(consensus.get("conclusion"), 1_800):
        consensus_ids = [
            value
            for value in (str(item).strip() for item in consensus.get("evidence_ids") or [])
            if value in known_ids
        ][:24]
        blocks.append(
            {
                "section": "综合复核",
                "kind": "inference" if consensus_ids else "context",
                "content": (
                    f"共识状态：{_text(consensus.get('verdict'), 32) or 'unknown'}\n"
                    f"{_text(consensus.get('conclusion'), 1_800)}"
                    + (f"\n复核说明：{_text(consensus.get('rationale'), 1_200)}" if _text(consensus.get('rationale'), 1_200) else "")
                ),
                "evidence_ids": list(dict.fromkeys(consensus_ids)),
            }
        )

    limitations: list[str] = []
    for result in results:
        if str(result.get("status") or "") != "completed":
            expert_id = str(result.get("expert_id") or result.get("agent_id") or "").strip().lower()
            label = _text(result.get("agent_display_name"), 160) or _ROLE_LABELS.get(expert_id, "领域")
            limitations.append(f"{label}方向交接状态为 {str(result.get('status') or 'partial')}。")
    merge = state.get("team_evidence_merge")
    if isinstance(merge, Mapping):
        limitations.extend(_text(item, 600) for item in merge.get("limitations") or [] if _text(item, 600))
        missing = [str(item).strip() for item in merge.get("missing_task_ids") or [] if str(item).strip()]
        if missing:
            limitations.append("仍有任务没有完成证据交接：" + "、".join(missing[:8]) + "。")
    if limitations:
        blocks.append(
            {
                "section": "协作限制",
                "kind": "disclaimer",
                "content": "\n".join(f"- {item}" for item in list(dict.fromkeys(limitations))[:12]),
                "evidence_ids": [],
            }
        )

    return {
        "title": "研究交接与核验记录",
        "blocks": blocks or [
            {
                "section": "协作结果",
                "kind": "disclaimer",
                "content": "本轮没有收到可发布的领域观察。",
                "evidence_ids": [],
            }
        ],
    }


__all__ = ["build_team_review_report", "team_synthesis_contract_issues"]
