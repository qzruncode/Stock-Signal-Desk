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
from src.services.buy_criteria.evaluators.competition_landscape import CompetitionLandscapeEvaluator
from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator
from src.services.buy_criteria.evaluators.prosperity_cycle import ProsperityCycleEvaluator
from src.services.buy_criteria.evaluators.catalyst_events import CatalystEventsEvaluator
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.orchestrator import CriterionOrchestrator, _format_sse
from src.services.buy_criteria.professional_analysis import DIMENSION_DEFINITIONS


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
    def test_nine_evaluators(self):
        assert len(EVALUATOR_CLASSES) == 9

    def test_indices_sequential(self):
        instances = [cls() for cls in EVALUATOR_CLASSES]
        indices = [e.index for e in instances]
        assert indices == list(range(9))

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
        ), patch.object(
            DataService,
            "get_sector_flow",
            return_value={"records": [], "sector_count": 0, "data_time": "2026-06-18", "is_stale": False},
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        assert "主营业务：光模块的研发、生产和销售。" not in evidence.data_summary
        assert "本关只判断该方向当前是否具有主线" in evidence.data_summary
        assert "AI科技链（算力底座与半导体设备）" in evidence.data_summary
        assert "候选主线（仅作观察，不等同于当前主线）" in evidence.data_summary
        assert "严禁用公司旧主营简介来否定或证明市场主线" in evidence.data_summary
        assert "不是封闭白名单" in evidence.data_summary
        assert evidence.raw_data["market_mainline_report"]["current_mainlines"][0]["rank"] == 1


class TestGrowthDriversEvidence:
    def test_collect_data_uses_raw_news_and_research(self):
        """Verify growth_drivers passes raw news/research to LLM for judgment."""
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

        # Verify raw data structure — raw lists, no keyword filtering
        assert "policy_news" in evidence.raw_data
        assert "tech_research" in evidence.raw_data
        assert "demand_news" in evidence.raw_data
        assert len(evidence.raw_data["policy_news"]) == 2
        assert len(evidence.raw_data["tech_research"]) == 1
        assert len(evidence.raw_data["demand_news"]) == 2

        # Verify summary includes raw content for LLM to judge
        assert "## 政策驱动证据" in evidence.data_summary
        assert "## 技术驱动证据" in evidence.data_summary
        assert "## 需求驱动证据" in evidence.data_summary
        assert "## 判断约束" in evidence.data_summary
        assert "工信部发布光通信产业发展指导意见" in evidence.data_summary
        assert "1.6T光模块技术迭代加速" in evidence.data_summary
        assert "光模块订单旺盛" in evidence.data_summary
        # Raw approach tells LLM to judge, not keyword-filtered "未发现"
        assert "未发现政策相关报道" not in evidence.data_summary

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


class TestCompetitionLandscapeEvidence:
    def test_uses_security_scoped_research_and_rejects_market_price_noise(self):
        evaluator = CompetitionLandscapeEvaluator()
        stock_info = {
            "symbol": "688017",
            "name": "绿的谐波",
            "industry": "通用设备制造业",
            "main_business": "精密谐波减速器研发、生产和销售",
            "business_scope": "精密谐波减速器研发、生产和销售",
            "_investment_thesis": "谐波减速器",
        }
        with patch.object(DataService, "get_valuation_ratios", return_value={}), patch.object(
            DataService,
            "get_financials",
            return_value={"items": [
                {"report_date": "2025-06-30", "gross_margin": 34.7},
                {"report_date": "2025-09-30", "gross_margin": 36.5},
                {"report_date": "2025-12-31", "gross_margin": 36.9},
                {"report_date": "2026-03-31", "gross_margin": 33.6},
            ]},
        ), patch.object(
            DataService,
            "get_research_report",
            return_value={"items": [{
                "publish_date": "2026-05-01",
                "org": "产业研究机构",
                "title": "谐波减速器龙头，技术与份额壁垒稳固",
                "summary": "头部企业具备技术、客户和规模壁垒。",
            }]},
        ), patch.object(
            DataService,
            "search_industry_news",
            return_value={"items": []},
        ):
            evidence = evaluator.collect_data("688017", stock_info)

        assert "谐波减速器龙头" in evidence.data_summary
        assert "2026-03-31 33.6%" in evidence.data_summary
        assert "单个季度毛利率回落不等于连续下滑" in evidence.data_summary
        assert "股价、板块涨跌、资金流" in evidence.data_summary
        assert "硅料价格持续下探" not in evidence.data_summary


class TestProsperityCycleEvidence:
    def test_industry_terms_use_the_internal_topic_search_not_the_symbol_tool(self):
        evaluator = ProsperityCycleEvaluator()
        stock_info = {
            "symbol": "688017",
            "name": "绿的谐波",
            "industry": "通用设备制造业",
            "main_business": "精密谐波减速器生产和销售",
        }
        with patch.object(
            DataService,
            "get_financials",
            return_value={"items": [
                {"report_date": "2025Q3", "revenue_yoy": 30, "gross_margin": 35},
                {"report_date": "2025Q4", "revenue_yoy": 35, "gross_margin": 36},
                {"report_date": "2026Q1", "revenue_yoy": 40, "gross_margin": 36},
            ]},
        ), patch.object(
            DataService,
            "search_industry_news",
            return_value={"items": [{
                "title": "人形机器人需求增长",
                "summary": "谐波减速器订单与出货增长",
                "publish_time": "2026-07-01",
            }]},
        ) as industry_search, patch.object(
            DataService,
            "search_news",
            side_effect=AssertionError("industry terms must not be sent to the symbol-news API"),
        ), patch.object(DataService, "get_macro_indicator", return_value={}):
            evidence = evaluator.collect_data("688017", stock_info)

        industry_search.assert_called()
        assert "人形机器人需求增长" in evidence.data_summary


class TestCatalystEventsEvidence:
    def test_collect_data_uses_raw_announcements_news_and_research(self):
        """Verify catalyst_events passes raw data to LLM for judgment."""
        evaluator = CatalystEventsEvaluator()
        stock_info = {"symbol": "300502.SZ", "name": "新易盛", "industry": "通信设备"}
        announcements = {"items": [
            {"publish_time": "2026-06-15", "title": "公司将于2026年7月召开年度股东大会",
             "event_label": "股东大会", "severity": "low"},
            {"publish_time": "2026-06-10", "title": "1.6T光模块产品量产发布",
             "event_label": "产品发布", "severity": "medium"},
            {"publish_time": "2026-05-20", "title": "日常经营公告",
             "event_label": "一般公告", "severity": "low"},
        ]}
        news = {"items": [
            {"publish_time": "2026-06-01", "source": "证券时报",
             "title": "光通信峰会将于8月在上海召开",
             "summary": "行业年度峰会聚焦1.6T光模块技术。"},
            {"publish_time": "2026-05-15", "source": "第一财经",
             "title": "新易盛与头部云厂商签约合作",
             "summary": "签订长期供货协议。"},
            {"publish_time": "2026-04-01", "source": "新华网",
             "title": "光模块行业日常资讯",
             "summary": "行业运行正常。"},
        ]}
        research = {"items": [
            {"publish_date": "2026-05-20", "org": "中信证券", "rating": "买入",
             "title": "1.6T量产在即，业绩拐点将至",
             "summary": "新一代产品进入量产阶段，预计下季度业绩超预期。"},
            {"publish_date": "2026-03-10", "org": "华泰证券", "rating": "增持",
             "title": "光模块行业深度报告",
             "summary": "行业景气度持续。"},
        ]}

        with patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_announcements",
            return_value=announcements,
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_catalyst_document_passages",
            return_value={"items": [], "documents": [], "errors": []},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_report_schedule",
            return_value={"items": [], "errors": []},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.search_news",
            return_value=news,
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_research_report",
            return_value=research,
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        # Verify raw data — raw lists, no keyword filtering
        assert "announcement_events" in evidence.raw_data
        assert "news_events" in evidence.raw_data
        assert "research_events" in evidence.raw_data
        assert len(evidence.raw_data["announcement_events"]) == 3
        assert len(evidence.raw_data["news_events"]) == 3
        assert len(evidence.raw_data["research_events"]) == 2

        # Verify summary — raw data shown for LLM to judge
        assert "公告事件" in evidence.data_summary
        assert "新闻线索" in evidence.data_summary
        assert "研报表述" in evidence.data_summary
        assert "## 判断约束" in evidence.data_summary
        assert "公司将于2026年7月召开年度股东大会" in evidence.data_summary
        assert "光通信峰会将于8月在上海召开" in evidence.data_summary
        assert "1.6T量产在即" in evidence.data_summary
        # Raw approach — no keyword-filtered "未找到" messages
        assert "未找到催化事件" not in evidence.data_summary

    def test_collect_data_no_catalysts_found(self):
        """Verify empty data shows appropriate fallback."""
        evaluator = CatalystEventsEvaluator()
        stock_info = {"symbol": "000001.SZ", "name": "平安银行", "industry": "银行业"}

        with patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_announcements",
            return_value={"items": [
                {"publish_time": "2026-06-01", "title": "日常公告",
                 "event_label": "一般", "severity": "low"},
            ]},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_catalyst_document_passages",
            return_value={"items": [], "documents": [], "errors": []},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_report_schedule",
            return_value={"items": [], "errors": []},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.search_news",
            return_value={"items": [
                {"publish_time": "2026-06-01", "source": "新华网",
                 "title": "银行日常资讯", "summary": "运行正常"},
            ]},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_research_report",
            return_value={"items": [
                {"publish_date": "2026-05-01", "org": "某券商", "rating": "中性",
                 "title": "银行业报告", "summary": "平稳"},
            ]},
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # Raw data present
        assert "announcement_events" in evidence.raw_data
        assert "news_events" in evidence.raw_data
        assert "research_events" in evidence.raw_data
        # No old references
        assert "announcement_catalysts" not in evidence.raw_data
        assert "news_catalysts" not in evidence.raw_data
        assert "research_catalysts" not in evidence.raw_data
        # Raw approach shows actual content, no "未找到" messages
        assert "未找到催化事件" not in evidence.data_summary

class TestBaseEvaluatorWithMockedLLM:
    def test_evaluate_pass(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={"market_mainline_report": {
            "as_of_date": "2026-07-21", "report_pending": False,
            "current_mainlines": [{"name": "AI"}],
        }}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value=({"passed": True, "verdict": "核心主线，资金持续流入"}, "")):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is True
        assert "核心主线" in result.verdict
        assert result.criterion_id == evaluator.criterion_id

    def test_evaluate_fail(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={"market_mainline_report": {
            "as_of_date": "2026-07-21", "report_pending": False,
            "current_mainlines": [{"name": "AI"}],
        }}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value=({"passed": False, "verdict": "非主线"}, "")):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is False

    def test_evaluate_llm_failure_returns_not_passed(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={"market_mainline_report": {
            "as_of_date": "2026-07-21", "report_pending": False,
            "current_mainlines": [{"name": "AI"}],
        }}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value=(None, "All LLM models failed")):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is False
        assert "评估失败" in result.verdict


# ── Unit Tests: Orchestrator ─────────────────────────────────────────────


class TestOrchestrator:
    @staticmethod
    def _analysis(statuses: list[str]) -> dict:
        dimensions = []
        for (dimension_id, title), status in zip(DIMENSION_DEFINITIONS, statuses):
            dimensions.append({
                "dimension_id": dimension_id,
                "status": status,
                "headline": f"{title}结论",
                "analysis": f"{title}已经完成支持证据与反证核验。",
                "key_evidence": ["支持证据"],
                "counter_evidence": ["反证"],
                "monitoring_points": ["跟踪指标"],
            })
        return {
            "contract_version": "professional_buy_analysis_v2",
            "analysis_mode": "professional_eight_dimension_buy_analysis",
            "symbol": "000001",
            "name": "测试",
            "investment_profile": "测试画像",
            "overall_summary": "测试综合结论",
            "core_thesis": "测试核心逻辑",
            "biggest_issue": "测试主要问题",
            "recommendation_code": "watchlist",
            "recommendation": "进入中期跟踪池",
            "recommendation_reason": "仍有关键项目需要继续验证。",
            "score": 0,
            "score_total": 8,
            "counts": {},
            "dimensions": dimensions,
            "bull_case_chain": "需求 → 收入 → 利润",
            "risk_chain": "竞争 → 降价 → 利润承压",
            "monitoring_points": ["指标一", "指标二", "指标三"],
            "evidence_gaps": [],
            "source_links": [],
            "data_time": "2026-07-23T12:00:00+08:00",
            "coverage_complete": True,
        }

    def test_failed_dimension_does_not_stop_remaining_analysis(self):
        """A failed market view must not hide the other seven dimensions."""
        orchestrator = CriterionOrchestrator()
        analysis = self._analysis([
            "fail", "pass", "partial", "pass",
            "pass", "partial", "pass", "fail",
        ])
        with patch(
            "src.services.buy_criteria.orchestrator.analyze_professional_buy",
            return_value=analysis,
        ):
            results = orchestrator.run("000001", save_to_db=False)

        assert len(results) == 8
        assert results[0].passed is False
        assert results[1].passed is True
        assert results[-1].criterion_id == "major_risks"

    def test_all_pass(self):
        """If all eight dimensions pass, all eight results are returned."""
        orchestrator = CriterionOrchestrator()
        with patch(
            "src.services.buy_criteria.orchestrator.analyze_professional_buy",
            return_value=self._analysis(["pass"] * 8),
        ):
            results = orchestrator.run("000001", save_to_db=False)

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
