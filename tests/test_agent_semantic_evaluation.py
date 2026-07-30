# -*- coding: utf-8 -*-

import json
from pathlib import Path

from src.agent.orchestrator_v2.contracts import Capability
from src.agent.semantic_evaluation import (
    SemanticGoldenCase,
    score_semantic_case,
    summarize_semantic_scores,
)

_GOLDEN_PATH = (
    Path(__file__).parent / "fixtures" / "agent_planner_golden.json"
)


def test_semantic_golden_gate_covers_every_capability():
    values = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = [SemanticGoldenCase.from_value(value) for value in values]
    assert len({case.case_id for case in cases}) == len(cases)
    covered = {
        capability
        for case in cases
        for capability in case.required_capabilities
    }
    assert covered == {capability.value for capability in Capability}


def test_semantic_score_fails_missing_forbidden_duplicate_and_oversize():
    case = SemanticGoldenCase(
        case_id="case",
        prompt="prompt",
        required_capabilities=frozenset({"news_analysis"}),
        forbidden_capabilities=frozenset({"notification"}),
        maximum_nodes=2,
        required_dependencies=frozenset({
            ("security_lookup", "news_analysis"),
        }),
        expected_effects=(("news_analysis", "read"),),
        required_scope_terms=frozenset({"600519"}),
    )

    score = score_semantic_case(
        case,
        ["notification", "notification", "general_response"],
        dependencies=[],
        effects={"news_analysis": "mutation"},
        scope_text="",
    )

    assert score["passed"] is False
    assert score["missing_required"] == ["news_analysis"]
    assert score["present_forbidden"] == ["notification"]
    assert score["duplicates"] == ["notification"]
    assert score["too_many_nodes"] is True
    assert score["missing_dependencies"] == [
        ["security_lookup", "news_analysis"],
    ]
    assert score["effect_mismatches"][0]["actual"] == "mutation"
    assert score["missing_scope_terms"] == ["600519"]


def test_semantic_release_gate_requires_every_case_to_pass():
    summary = summarize_semantic_scores([
        {"passed": True},
        {"passed": False},
    ])

    assert summary == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "pass_rate": 0.5,
        "release_gate_passed": False,
    }
