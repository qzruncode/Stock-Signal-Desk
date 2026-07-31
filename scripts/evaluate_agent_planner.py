#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the live semantic Planner against the versioned golden corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

import litellm


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.orchestrator_v2.planner import plan_intent_graph_v2  # noqa: E402
from src.agent.orchestrator_v2.registry import capability_for  # noqa: E402
from src.agent.semantic_evaluation import (  # noqa: E402
    SemanticGoldenCase,
    score_semantic_case,
    summarize_semantic_scores,
)
from src.config import setup_env  # noqa: E402
from src.llm.anthropic_gateway import (  # noqa: E402
    resolve_anthropic_gateway_config,
)


async def _main_async(corpus: Path, maximum_cases: int | None) -> int:
    values = json.loads(corpus.read_text(encoding="utf-8"))
    cases = [SemanticGoldenCase.from_value(value) for value in values[:maximum_cases]]
    llm_config = resolve_anthropic_gateway_config()

    scores = []
    for case in cases:
        try:
            graph = await plan_intent_graph_v2(
                [{"role": "user", "content": case.prompt}],
                llm_config,
                completion=litellm.acompletion,
            )
            capabilities = [node.outline.capability.value for node in graph.nodes]
            capability_by_node = {node.outline.node_id: node.outline.capability.value for node in graph.nodes}
            dependencies = [
                (
                    capability_by_node[reference.node_id],
                    node.outline.capability.value,
                )
                for node in graph.nodes
                for reference in node.outline.input_refs
                if reference.source == "node" and reference.node_id in capability_by_node
            ]
            effects = {
                capability: capability_for(capability).execution_policy.effect.value for capability in capabilities
            }
            scope_text = json.dumps(
                [node.intent.model_dump(mode="json") for node in graph.nodes],
                ensure_ascii=False,
                default=str,
            )
            score = score_semantic_case(
                case,
                capabilities,
                dependencies=dependencies,
                effects=effects,
                scope_text=scope_text,
            )
        except Exception as exc:
            score = {
                "case_id": case.case_id,
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        scores.append(score)
        print(json.dumps(score, ensure_ascii=False))

    summary = summarize_semantic_scores(scores)
    print(json.dumps({"summary": summary}, ensure_ascii=False))
    return 0 if summary["release_gate_passed"] else 1


def main() -> int:
    setup_env()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=PROJECT_ROOT / "tests/fixtures/agent_planner_golden.json",
    )
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    return asyncio.run(_main_async(args.corpus, args.max_cases))


if __name__ == "__main__":
    raise SystemExit(main())
