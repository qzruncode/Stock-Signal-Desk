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
from src.services.buy_criteria.evaluators.growth_drivers import GrowthDriversEvaluator
from src.services.buy_criteria.evaluators.growth_space import GrowthSpaceEvaluator
from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator
from src.services.buy_criteria.evaluators.prosperity_cycle import ProsperityCycleEvaluator
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


# ── Unit Tests: Mainline Position Evidence ──────────────────────────────


class TestMainlinePositionEvidence:
    def test_collect_data_includes_market_report_and_company_profile(self):
        evaluator = MainlinePositionEvaluator()
        stock_info = {
            "symbol": "300502.SZ",
            "name": "成都新易盛通信技术股份有限公司",
            "industry": "计算机、通信和其他电子设备制造业",
            "main_business": "光模块的研发、生产和销售。",
            "product_type": "光互联产品",
            "product_name": "光互联产品",
            "profile": "公司主营高速光模块。",
        }
        market_report = {
            "report_pending": False,
            "as_of_date": "2026-06-18",
            "overview": "当前主线为AI科技链。",
            "market_stage": {"label": "结构性主升初期"},
            "current_mainlines": [
                {
                    "name": "AI科技链（算力底座与半导体设备）",
                    "rank": 1,
                    "stage": "主升初期",
                    "branches": ["算力网络与数据中心基础设施"],
                    "reason": "算力基础设施催化。",
                    "evidence": ["算力网建设推进"],
                }
            ],
            "future_mainlines": [
                {
                    "name": "消费电子与AI终端",
                    "stage_hint": "候选观察期",
                    "reason": "需要产品销量验证。",
                    "triggers": ["AI终端发布"],
                }
            ],
        }

        with patch(
            "src.services.buy_criteria.evaluators.mainline_position.DataService.get_market_mainline_report",
            return_value=market_report,
        ), patch(
            "src.services.buy_criteria.evaluators.mainline_position.DataService.get_sector_list",
            return_value={"items": [], "data_time": "2026-06-18"},
        ), patch(
            "src.services.buy_criteria.evaluators.mainline_position.DataService.get_sentiment",
            return_value={"sentiment_score": 18.8, "total_discussion": None},
        ), patch(
            "src.services.buy_criteria.evaluators.mainline_position.DataService.get_social_sentiment",
            return_value={},
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        assert "主营业务：光模块的研发、生产和销售。" in evidence.data_summary
        assert "AI科技链（算力底座与半导体设备）" in evidence.data_summary
        assert "候选主线（仅作观察，不等同于当前主线）" in evidence.data_summary
        assert "不要使用本地关键词命中" in evidence.data_summary
        assert evidence.raw_data["market_mainline_report"]["current_mainlines"][0]["rank"] == 1


class TestProsperityCycleEvidence:
    def test_collect_data_uses_sector_list_and_fund_flow(self):
        evaluator = ProsperityCycleEvaluator()
        stock_info = {
            "symbol": "300502.SZ",
            "name": "成都新易盛通信技术股份有限公司",
            "industry": "计算机设备",
            "main_business": "光模块的研发、生产和销售。",
            "product_type": "光互联产品",
            "product_name": "光互联产品",
        }
        sectors = {
            "items": [
                {"name": "半导体", "change_pct": 3.2},
                {"name": "计算机设备", "change_pct": 1.8},
                {"name": "通信设备", "change_pct": 1.5},
                {"name": "消费电子", "change_pct": 0.9},
                {"name": "电力设备", "change_pct": -0.5},
            ],
        }
        fund_flow = [
            {"name": "半导体", "main_net_inflow": 5e9, "pct_chg": 3.2},
            {"name": "计算机设备", "main_net_inflow": 2e9, "pct_chg": 1.8},
            {"name": "通信设备", "main_net_inflow": 1e9, "pct_chg": 1.5},
            {"name": "消费电子", "main_net_inflow": 0.5e9, "pct_chg": 0.9},
            {"name": "电力设备", "main_net_inflow": -1e9, "pct_chg": -0.5},
        ]
        financials = {
            "items": [
                {"report_date": "2026-03-31", "revenue_yoy": 42.1, "revenue_qoq": 18.2, "net_profit_yoy": 58.0, "gross_margin": 45.5},
                {"report_date": "2025-12-31", "revenue_yoy": 36.0, "revenue_qoq": 12.4, "net_profit_yoy": 40.2, "gross_margin": 43.0},
                {"report_date": "2025-09-30", "revenue_yoy": 28.5, "revenue_qoq": 8.0, "net_profit_yoy": 31.8, "gross_margin": 41.2},
            ]
        }
        pmi = {
            "latest": {"period": "2026-05", "value": 50.5},
            "trend": "扩张",
            "history": [{"period": "2026-05", "value": 50.5}],
            "data_time": "2026-05",
        }

        with patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_sector_list",
            return_value=sectors,
        ), patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_sector_flow_industry",
            return_value=fund_flow,
        ), patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_financials",
            return_value=financials,
        ), patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_macro_indicator",
            return_value=pmi,
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        assert "板块排名第2名" in evidence.data_summary
        assert "本行业[计算机设备]" in evidence.data_summary
        assert "板块前5名" in evidence.data_summary
        assert "资金净流入前3" in evidence.data_summary
        assert "2026-03-31：营收同比 42.10%" in evidence.data_summary
        assert "宏观PMI最新值：50.5" in evidence.data_summary
        assert "不要因为PMI或产能利用率缺失" in evidence.data_summary
        assert evidence.data_summary != "数据获取不完整"
        # Ensure no old references remain
        assert "行业景气度评分" not in evidence.data_summary
        assert "行业β结论" not in evidence.data_summary
        assert "周期阶段" not in evidence.data_summary

    def test_collect_data_industry_not_matched(self):
        """When industry doesn't match any sector, report gap."""
        evaluator = ProsperityCycleEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
            "main_business": "银行业务",
            "product_type": "金融服务",
            "product_name": "银行服务",
        }
        sectors = {
            "items": [
                {"name": "半导体", "change_pct": 3.2},
                {"name": "计算机设备", "change_pct": 1.8},
            ],
        }
        financials = {"items": []}
        pmi = {"latest": {}, "trend": "", "history": [], "data_time": None}

        with patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_sector_list",
            return_value=sectors,
        ), patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_sector_flow_industry",
            return_value=[],
        ), patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_financials",
            return_value=financials,
        ), patch(
            "src.services.buy_criteria.evaluators.prosperity_cycle.DataService.get_macro_indicator",
            return_value=pmi,
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        assert "未匹配到板块排名数据" in evidence.data_summary
        assert "行业板块排名未匹配" in evidence.data_summary


class TestGrowthDriversEvidence:
    def test_collect_data_uses_news_and_research(self):
        """Verify growth_drivers extracts policy/tech/demand evidence from raw data."""
        evaluator = GrowthDriversEvaluator()
        stock_info = {
            "symbol": "300502.SZ",
            "name": "新易盛",
            "industry": "通信设备",
            "main_business": "光模块",
        }
        news = {
            "items": [
                {
                    "publish_time": "2026-05-10",
                    "source": "新华社",
                    "title": "工信部发布光通信产业发展指导意见",
                    "summary": "支持光通信技术升级和产业化。",
                },
                {
                    "publish_time": "2026-06-01",
                    "source": "第一财经",
                    "title": "光模块订单旺盛，厂商扩产",
                    "summary": "多家光模块厂商订单增长，产能供不应求。",
                },
            ],
        }
        research = {
            "items": [
                {
                    "publish_date": "2026-04-15",
                    "org": "中信证券",
                    "title": "1.6T光模块技术迭代加速",
                    "summary": "新一代1.6T产品进入量产阶段，技术突破显著。",
                },
            ],
        }

        with patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.search_news",
            return_value=news,
        ), patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.get_research_report",
            return_value=research,
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        # Verify new data structure
        assert "policy_evidence" in evidence.raw_data
        assert "tech_evidence" in evidence.raw_data
        assert "demand_evidence" in evidence.raw_data
        assert evidence.raw_data["policy_evidence"]["count"] == 1
        assert evidence.raw_data["tech_evidence"]["count"] == 1
        assert evidence.raw_data["demand_evidence"]["count"] == 1

        # Verify summary format
        assert "## 政策驱动证据" in evidence.data_summary
        assert "## 技术驱动证据" in evidence.data_summary
        assert "## 需求驱动证据" in evidence.data_summary
        assert "## 判断约束" in evidence.data_summary
        assert "工信部发布光通信产业发展指导意见" in evidence.data_summary
        assert "1.6T光模块技术迭代加速" in evidence.data_summary
        assert "光模块订单旺盛" in evidence.data_summary
        assert "数据获取不完整" not in evidence.data_summary

    def test_collect_data_no_industry_cycle_dependency(self):
        """Ensure growth_drivers does not call IndustryCycleService."""
        evaluator = GrowthDriversEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
            "main_business": "银行业务",
        }

        with patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.search_news",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.get_research_report",
            return_value={"items": []},
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # No old references
        assert "industry_cycle" not in evidence.raw_data
        assert "sentiment" not in evidence.raw_data
        assert "policy_drivers" not in evidence.raw_data
        assert "tech_drivers" not in evidence.raw_data
        assert "demand_drivers" not in evidence.raw_data
        # Empty evidence shows appropriate messages
        assert "未发现政策相关报道" in evidence.data_summary
        assert "未发现技术突破相关描述" in evidence.data_summary
        assert "未发现需求/订单增长线索" in evidence.data_summary

    def test_collect_data_handles_exceptions_gracefully(self):
        """Ensure evaluator handles data source failures gracefully."""
        evaluator = GrowthDriversEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
        }

        with patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.search_news",
            side_effect=Exception("network error"),
        ), patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.get_research_report",
            side_effect=Exception("timeout"),
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # Should not crash, should show fallback messages
        assert "## 政策驱动证据" in evidence.data_summary
        assert "## 技术驱动证据" in evidence.data_summary
        assert "## 需求驱动证据" in evidence.data_summary
        assert "未发现政策相关报道" in evidence.data_summary


class TestGrowthSpaceEvidence:
    def test_collect_data_uses_financial_research_and_news_evidence(self):
        evaluator = GrowthSpaceEvaluator()
        stock_info = {
            "symbol": "300502.SZ",
            "name": "成都新易盛通信技术股份有限公司",
            "industry": "计算机、通信和其他电子设备制造业",
            "main_business": "光模块的研发、生产和销售。",
            "product_type": "光互联产品",
            "product_name": "光互联产品",
        }
        financials = {
            "items": [
                {"report_date": "2026-03-31", "revenue_yoy": 105.76, "revenue_qoq": 0.01, "net_profit_yoy": 76.8, "gross_margin": 49.16},
            ]
        }
        research = {
            "items": [
                {
                    "publish_date": "2026-05-27",
                    "org": "山西证券",
                    "rating": "买入",
                    "title": "1.6T环比上量将加快",
                    "profit_forecasts": [
                        {"year": 2026, "eps": 21.33, "pe": 32.8},
                        {"year": 2027, "eps": 40.7, "pe": 17.2},
                    ],
                }
            ],
            "data_time": "2026-05-27T00:00:00",
            "is_stale": False,
        }
        news = {
            "items": [
                {
                    "publish_time": "2026-06-18T09:40:42",
                    "source": "第一财经",
                    "event_label": "一般资讯",
                    "title": "资金从消费流向AI",
                    "summary": "光模块概念股新易盛上涨。",
                }
            ],
            "data_time": "2026-06-18T09:40:42",
            "is_stale": False,
        }

        with patch(
            "src.services.buy_criteria.evaluators.growth_space.DataService.get_financials",
            return_value=financials,
        ), patch(
            "src.services.buy_criteria.evaluators.growth_space.DataService.get_research_report",
            return_value=research,
        ), patch(
            "src.services.buy_criteria.evaluators.growth_space.DataService.search_news",
            return_value=news,
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        # Verify no old industry_cycle/beta_detector references in output
        assert "行业β结论" not in evidence.data_summary
        assert "行业周期与空间证据" not in evidence.data_summary
        assert "未来3年空间项" not in evidence.data_summary
        assert "驱动因素项" not in evidence.data_summary

        # Verify new data sources are present
        assert "2026-03-31：营收同比 105.76%" in evidence.data_summary
        assert "山西证券 买入：1.6T环比上量将加快" in evidence.data_summary
        assert "第一财经 [一般资讯] 资金从消费流向AI" in evidence.data_summary
        assert "盈利预测汇总" in evidence.data_summary
        assert "增速预测线索" in evidence.data_summary
        assert "数据获取不完整" not in evidence.data_summary

    def test_collect_data_no_industry_cycle_dependency(self):
        """Ensure growth_space does not call IndustryCycleService or beta_detector."""
        evaluator = GrowthSpaceEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
            "main_business": "银行业务",
            "product_type": "金融服务",
            "product_name": "银行服务",
        }

        with patch(
            "src.services.buy_criteria.evaluators.growth_space.DataService.get_financials",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.growth_space.DataService.get_research_report",
            return_value={"items": [], "data_time": None, "is_stale": True},
        ), patch(
            "src.services.buy_criteria.evaluators.growth_space.DataService.search_news",
            return_value={"items": [], "data_time": None, "is_stale": True},
        ) as mock_news:
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # Confirm no industry_cycle call was made
        assert "industry_cycle" not in evidence.raw_data
        assert "industry_beta_detector" not in evidence.raw_data
        assert "industry_cycle_error" not in evidence.raw_data
        assert "数据获取不完整" not in evidence.data_summary


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
