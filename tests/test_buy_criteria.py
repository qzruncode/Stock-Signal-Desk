# tests/test_buy_criteria.py
"""Tests for buy criteria analysis."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.services.buy_criteria.base import (
    BaseCriterionEvaluator,
    CriterionEvidence,
    CriterionResult,
    _parse_verdict_json,
)
from src.services.buy_criteria.evaluators import EVALUATOR_CLASSES
from src.services.buy_criteria.orchestrator import CriterionOrchestrator, _format_sse


# ── Unit Tests: _parse_verdict_json ─────────────────────────────────────


class TestParseVerdictJson:
    def test_plain_json(self):
        raw = '{"passed": true, "verdict": "行业景气上行"}'
        result = _parse_verdict_json(raw)
        assert result is not None
        assert result["passed"] is True
        assert "verdict" in result

    def test_markdown_fenced(self):
        raw = '```json\n{"passed": false, "verdict": "不通过"}\n```'
        result = _parse_verdict_json(raw)
        assert result is not None
        assert result["passed"] is False

    def test_embedded_in_text(self):
        raw = '根据分析，结果如下：\n{"passed": true, "verdict": "通过"}\n以上是结果。'
        result = _parse_verdict_json(raw)
        assert result is not None
        assert result["passed"] is True

    def test_invalid_returns_none(self):
        assert _parse_verdict_json("not json at all") is None
        assert _parse_verdict_json("") is None


# ── Unit Tests: CriterionResult ─────────────────────────────────────────


class TestCriterionResult:
    def test_to_dict(self):
        r = CriterionResult(
            criterion_id="test",
            criterion_name="测试",
            index=0,
            passed=True,
            verdict="通过",
            evidence=CriterionEvidence(raw_data={"key": "val"}, data_summary="摘要"),
        )
        d = r.to_dict()
        assert d["criterion_id"] == "test"
        assert d["passed"] is True
        assert d["evidence"]["raw_data"] == {"key": "val"}
        assert d["analyzed_at"]  # auto-populated


# ── Unit Tests: Evaluator Registry ──────────────────────────────────────


class TestEvaluatorRegistry:
    def test_eight_evaluators(self):
        assert len(EVALUATOR_CLASSES) == 8

    def test_indices_sequential(self):
        instances = [cls() for cls in EVALUATOR_CLASSES]
        indices = [e.index for e in instances]
        assert indices == list(range(8))

    def test_all_have_rubrics(self):
        for cls in EVALUATOR_CLASSES:
            e = cls()
            rubric = e.get_rubric()
            assert isinstance(rubric, str)
            assert len(rubric) > 50  # rubrics should be substantial


# ── Unit Tests: BaseCriterionEvaluator with mocked LLM ──────────────────


class TestBaseEvaluatorWithMockedLLM:
    def test_evaluate_pass(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value=({"passed": True, "verdict": "核心主线，资金持续流入"}, "")):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is True
        assert "核心主线" in result.verdict
        assert result.criterion_id == evaluator.criterion_id

    def test_evaluate_fail(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value=({"passed": False, "verdict": "非主线"}, "")):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is False

    def test_evaluate_llm_failure_returns_not_passed(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value=(None, "All LLM models failed")):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is False
        assert "评估失败" in result.verdict


# ── Unit Tests: Orchestrator ─────────────────────────────────────────────


class TestOrchestrator:
    def test_early_termination(self):
        """If evaluator 0 fails, only 1 result should be returned."""
        orchestrator = CriterionOrchestrator()
        mock_result_fail = CriterionResult(
            criterion_id="mainline_position", criterion_name="市场主线属性",
            index=0, passed=False, verdict="非主线",
        )

        mock_stock_info = {"symbol": "000001", "name": "测试", "industry": "测试"}

        with patch("src.services.buy_criteria.orchestrator._get_stock_info_safe", return_value=mock_stock_info):
            with patch.object(EVALUATOR_CLASSES[0], "evaluate", return_value=mock_result_fail):
                results = orchestrator.run("000001")

        assert len(results) == 1
        assert results[0].passed is False

    def test_all_pass(self):
        """If all 8 evaluators pass, 8 results should be returned."""
        orchestrator = CriterionOrchestrator()
        mock_stock_info = {"symbol": "000001", "name": "测试", "industry": "测试"}

        def make_pass_result(idx):
            cls = EVALUATOR_CLASSES[idx]
            e = cls()
            return CriterionResult(
                criterion_id=e.criterion_id, criterion_name=e.criterion_name,
                index=idx, passed=True, verdict="通过",
            )

        with patch("src.services.buy_criteria.orchestrator._get_stock_info_safe", return_value=mock_stock_info):
            for i, cls in enumerate(EVALUATOR_CLASSES):
                patch.object(cls, "evaluate", return_value=make_pass_result(i)).start()

            results = orchestrator.run("000001")

        assert len(results) == 8
        assert all(r.passed for r in results)


# ── Unit Tests: SSE Format ───────────────────────────────────────────────


class TestSSEFormat:
    def test_format_sse(self):
        result = _format_sse("test_event", {"key": "值"})
        assert "event: test_event" in result
        assert '"key"' in result
        assert "值" in result
        assert result.endswith("\n\n")
