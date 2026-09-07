"""Project validated answer blocks into immutable, owner-scoped research.

No model calls, text-to-verdict heuristics, or control-flow decisions occur at
this boundary. Historical buy-gate records remain readable.
"""

from datetime import datetime
import hashlib
import json

from src.agent.claim_validation import claim_checks_pass
from src.agent.langgraph_runtime.answer_contract import resolve_answer_sources
from src.agent.langgraph_runtime.claim_evidence import build_structured_claim_evidence_ledger
from src.tools.base import evidence_record_is_eligible


def project_research_conclusions(state: dict, *, as_of: datetime) -> list[dict]:
    evidence = [item for item in state.get("evidence", []) if evidence_record_is_eligible(item)]
    # The native structured_response channel can retain the previous turn;
    # structured_answer is the validated, explicitly reset per-turn copy.
    answer = resolve_answer_sources(state.get("structured_answer"), evidence)
    blocks = answer.get("blocks", [])
    records = []
    for index, annotation in enumerate(answer.get("research", [])):
        symbol = str(annotation.get("symbol", "")).strip()
        indices = annotation.get("block_indices", [])
        if not symbol or not indices or any(type(i) is not int or i < 1 or i > len(blocks) for i in indices):
            continue
        selected = [dict(blocks[i - 1]) for i in dict.fromkeys(indices)]
        if not any(block.get("kind") in {"inference", "recommendation"} for block in selected):
            continue
        scoped = [{**block, "content": f"{symbol}: {block.get('content', '')}"} for block in selected]
        ledger = build_structured_claim_evidence_ledger(scoped, evidence, state.get("tool_results", []))
        if not ledger["claims"] or any(not claim_checks_pass(claim) for claim in ledger["claims"]):
            continue
        ids = set(ledger["cited_evidence_ids"])
        sources = [item for item in evidence if (item.get("evidence_id") or item.get("id")) in ids]
        verdict = annotation.get("verdict")
        if verdict not in {"buy", "not_buy", "watch", "avoid"}:
            continue
        payload = {"blocks": selected, "source_records": sources}
        records.append({
            "task_id": f"research-{index + 1}", "symbol": symbol, "verdict": verdict,
            "conclusion_type": "research", "contract_version": "research-1.0", "as_of_at": as_of,
            "thesis": {"blocks": selected}, "evidence": {"items": sources},
            "evidence_fingerprint": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
        })
    return records
