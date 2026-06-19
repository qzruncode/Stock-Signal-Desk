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

        # Raw data keys present (empty lists, no keyword filtering)
        assert "policy_news" in evidence.raw_data
        assert "tech_research" in evidence.raw_data
        assert "demand_news" in evidence.raw_data
        # No old references
        assert "industry_cycle" not in evidence.raw_data
        assert "policy_drivers" not in evidence.raw_data
        assert "tech_drivers" not in evidence.raw_data
        assert "未发现政策相关报道" not in evidence.data_summary

    def test_collect_data_handles_exceptions_gracefully(self):
        """Verify graceful degradation when all data sources fail."""
        evaluator = GrowthDriversEvaluator()
        stock_info = {"symbol": "000001.SZ", "name": "测试", "industry": "测试", "main_business": "测试"}

        with patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.search_news",
            side_effect=Exception("network error"),
        ), patch(
            "src.services.buy_criteria.evaluators.growth_drivers.DataService.get_research_report",
            side_effect=Exception("timeout"),
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # Still produces valid summary even with all sources failing
        assert "## 政策驱动证据" in evidence.data_summary
        assert "## 技术驱动证据" in evidence.data_summary
        assert "## 需求驱动证据" in evidence.data_summary
        assert "## 判断约束" in evidence.data_summary
        assert "无新闻数据" in evidence.data_summary
        assert "无研报数据" in evidence.data_summary


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


class TestCompetitionLandscapeEvidence:
    def test_collect_data_uses_valuation_financials_sectors_news(self):
        """Verify competition_landscape extracts margin/sector/price-war evidence from raw data."""
        evaluator = CompetitionLandscapeEvaluator()
        stock_info = {
            "symbol": "300502.SZ",
            "name": "新易盛",
            "industry": "通信设备",
            "main_business": "光模块",
        }
        valuation = {
            "gross_margin": 49.16,
            "net_margin": 38.2,
            "industry_average": {
                "gross_margin": 35.0,
                "net_margin": 22.0,
            },
        }
        financials = {
            "items": [
                {"report_date": "2026-03-31", "gross_margin": 49.16},
                {"report_date": "2025-12-31", "gross_margin": 47.5},
                {"report_date": "2025-09-30", "gross_margin": 45.2},
                {"report_date": "2025-06-30", "gross_margin": 43.8},
            ]
        }
        sectors = {
            "items": [
                {"name": "半导体", "change_pct": 3.2},
                {"name": "通信设备", "change_pct": 2.1},
                {"name": "计算机设备", "change_pct": 1.8},
            ],
        }
        news = {
            "items": [
                {
                    "publish_time": "2026-05-10",
                    "source": "第一财经",
                    "title": "光模块行业景气，订单持续增长",
                    "summary": "行业需求旺盛，毛利率稳步提升。",
                },
            ],
        }

        with patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_valuation_ratios",
            return_value=valuation,
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_financials",
            return_value=financials,
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_sector_list",
            return_value=sectors,
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.search_news",
            return_value=news,
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        # Verify raw data structure
        assert "margin_data" in evidence.raw_data
        assert "margin_trend" in evidence.raw_data
        assert "sector_ranking" in evidence.raw_data
        assert "price_war_signals" in evidence.raw_data
        assert evidence.raw_data["margin_data"]["gross_margin"] == 49.16
        assert evidence.raw_data["margin_data"]["industry_avg_gross_margin"] == 35.0
        assert len(evidence.raw_data["margin_trend"]["items"]) == 4
        assert len(evidence.raw_data["sector_ranking"]["items"]) == 3
        assert evidence.raw_data["price_war_signals"]["count"] == 0

        # Verify summary format
        assert "## 毛利率数据" in evidence.data_summary
        assert "当前毛利率：49.16%" in evidence.data_summary
        assert "行业平均毛利率：35.0%" in evidence.data_summary
        assert "最近4季度毛利率趋势" in evidence.data_summary
        assert "## 行业板块竞争格局" in evidence.data_summary
        assert "板块共3个行业参与排名" in evidence.data_summary
        assert "## 价格战信号" in evidence.data_summary
        assert "近6个月未发现明显价格战/内卷信号" in evidence.data_summary
        assert "## 判断约束" in evidence.data_summary
        assert "内卷风险高" in evidence.data_summary
        assert "数据获取不完整" not in evidence.data_summary

    def test_collect_data_detects_price_war_keywords(self):
        """Verify price war keyword detection from news."""
        evaluator = CompetitionLandscapeEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "测试公司",
            "industry": "测试行业",
        }
        news = {
            "items": [
                {
                    "publish_time": "2026-04-01",
                    "source": "证券时报",
                    "title": "行业价格战加剧，企业降价促销",
                    "summary": "多家企业宣布降价策略，毛利率持续下滑。",
                },
                {
                    "publish_time": "2026-05-15",
                    "source": "财联社",
                    "title": "行业内卷严重，头部企业也难以幸免",
                    "summary": "竞争白热化，价格下探成为常态。",
                },
                {
                    "publish_time": "2026-06-01",
                    "source": "新华网",
                    "title": "行业平稳发展，无异常",
                    "summary": "行业运行正常。",
                },
            ],
        }

        with patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_valuation_ratios",
            return_value={},
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_financials",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_sector_list",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.search_news",
            return_value=news,
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        assert evidence.raw_data["price_war_signals"]["count"] == 2
        assert "发现2条价格战/内卷相关报道" in evidence.data_summary
        assert "行业价格战加剧" in evidence.data_summary
        assert "行业内卷严重" in evidence.data_summary

    def test_collect_data_no_industry_cycle_dependency(self):
        """Ensure competition_landscape does not call IndustryCycleService."""
        evaluator = CompetitionLandscapeEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
        }

        with patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_valuation_ratios",
            return_value={},
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_financials",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_sector_list",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.search_news",
            return_value={"items": []},
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # No old references
        assert "industry_cycle" not in evidence.raw_data
        assert "industry_cycle_error" not in evidence.raw_data
        assert "concentration_cr5" not in evidence.raw_data
        assert "competition_intensity" not in evidence.raw_data
        assert "peer_comparison" not in evidence.raw_data

    def test_collect_data_handles_exceptions_gracefully(self):
        """Ensure evaluator handles data source failures gracefully."""
        evaluator = CompetitionLandscapeEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
        }

        with patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_valuation_ratios",
            side_effect=Exception("network error"),
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_financials",
            side_effect=Exception("timeout"),
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.get_sector_list",
            side_effect=Exception("service unavailable"),
        ), patch(
            "src.services.buy_criteria.evaluators.competition_landscape.DataService.search_news",
            side_effect=Exception("dns failure"),
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # Should not crash
        assert "## 毛利率数据" in evidence.data_summary
        assert "## 行业板块竞争格局" in evidence.data_summary
        assert "板块排名数据缺失" in evidence.data_summary
        assert "## 价格战信号" in evidence.data_summary
        assert "近6个月未发现明显价格战/内卷信号" in evidence.data_summary


class TestCatalystEventsEvidence:
    def test_collect_data_uses_announcements_news_and_research(self):
        """Verify catalyst_events extracts catalyst events from announcements, news, and research."""
        evaluator = CatalystEventsEvaluator()
        stock_info = {
            "symbol": "300502.SZ",
            "name": "新易盛",
            "industry": "通信设备",
        }
        announcements = {
            "items": [
                {
                    "title": "公司将于2026年7月召开年度股东大会",
                    "publish_time": "2026-06-15",
                    "event_label": "股东大会",
                    "severity": "low",
                },
                {
                    "title": "1.6T光模块产品量产发布",
                    "publish_time": "2026-06-10",
                    "event_label": "产品发布",
                    "severity": "medium",
                },
                {
                    "title": "日常经营公告",
                    "publish_time": "2026-05-20",
                    "event_label": "一般公告",
                    "severity": "low",
                },
            ],
        }
        news = {
            "items": [
                {
                    "publish_time": "2026-06-01",
                    "source": "证券时报",
                    "title": "光通信峰会将于8月在上海召开",
                    "summary": "行业年度峰会聚焦1.6T光模块技术。",
                },
                {
                    "publish_time": "2026-05-15",
                    "source": "第一财经",
                    "title": "新易盛与头部云厂商签约合作",
                    "summary": "签订长期供货协议。",
                },
                {
                    "publish_time": "2026-04-01",
                    "source": "新华网",
                    "title": "光模块行业日常资讯",
                    "summary": "行业运行正常。",
                },
            ],
        }
        research = {
            "items": [
                {
                    "publish_date": "2026-05-20",
                    "org": "中信证券",
                    "rating": "买入",
                    "title": "1.6T量产在即，业绩拐点将至",
                    "summary": "新一代产品进入量产阶段，预计下季度业绩超预期。",
                },
                {
                    "publish_date": "2026-03-10",
                    "org": "华泰证券",
                    "rating": "增持",
                    "title": "光模块行业深度报告",
                    "summary": "行业景气度持续。",
                },
            ],
        }

        with patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_risk_events",
            return_value=announcements,
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.search_news",
            return_value=news,
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_research_report",
            return_value=research,
        ):
            evidence = evaluator.collect_data("300502.SZ", stock_info)

        # Verify raw data structure
        assert "announcement_catalysts" in evidence.raw_data
        assert "news_catalysts" in evidence.raw_data
        assert "research_catalysts" in evidence.raw_data
        assert evidence.raw_data["announcement_catalysts"]["count"] == 2
        assert evidence.raw_data["news_catalysts"]["count"] == 2
        assert evidence.raw_data["research_catalysts"]["count"] == 1

        # Verify summary format
        assert "## 公告催化事件" in evidence.data_summary
        assert "## 新闻催化线索" in evidence.data_summary
        assert "## 研报催化线索" in evidence.data_summary
        assert "## 判断约束" in evidence.data_summary
        assert "1.6T光模块产品量产发布" in evidence.data_summary
        assert "光通信峰会将于8月在上海召开" in evidence.data_summary
        assert "1.6T量产在即" in evidence.data_summary
        assert "数据获取不完整" not in evidence.data_summary

        # Verify no old references
        assert "catalyst_error" not in evidence.raw_data
        assert "risk_events" not in evidence.raw_data

    def test_collect_data_no_catalysts_found(self):
        """When no catalyst keywords match, show appropriate messages."""
        evaluator = CatalystEventsEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
        }

        with patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_risk_events",
            return_value={"items": [
                {"title": "日常公告", "publish_time": "2026-06-01", "event_label": "一般", "severity": "low"},
            ]},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.search_news",
            return_value={"items": [
                {"publish_time": "2026-06-01", "source": "新华网", "title": "银行日常资讯", "summary": "运行正常"},
            ]},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_research_report",
            return_value={"items": [
                {"publish_date": "2026-05-01", "org": "某券商", "rating": "中性", "title": "银行行业报告", "summary": "平稳"},
            ]},
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        assert "近90天公告中未找到催化事件" in evidence.data_summary
        assert "近180天新闻中未找到催化线索" in evidence.data_summary
        assert "研报中未找到催化线索" in evidence.data_summary
        assert "至少一个具体催化才判为通过" in evidence.data_summary

    def test_collect_data_no_industry_cycle_dependency(self):
        """Ensure catalyst_events does not reference old catalyst/risk cross-references."""
        evaluator = CatalystEventsEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
        }

        with patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_risk_events",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.search_news",
            return_value={"items": []},
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_research_report",
            return_value={"items": []},
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # No old references
        assert "catalyst" not in evidence.raw_data or "catalyst_error" not in evidence.raw_data
        assert "risk_events" not in evidence.raw_data

    def test_collect_data_handles_exceptions_gracefully(self):
        """Ensure evaluator handles data source failures gracefully."""
        evaluator = CatalystEventsEvaluator()
        stock_info = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "银行业",
        }

        with patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_risk_events",
            side_effect=Exception("network error"),
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.search_news",
            side_effect=Exception("timeout"),
        ), patch(
            "src.services.buy_criteria.evaluators.catalyst_events.DataService.get_research_report",
            side_effect=Exception("service unavailable"),
        ):
            evidence = evaluator.collect_data("000001.SZ", stock_info)

        # Should not crash, should show fallback messages
        assert "## 公告催化事件" in evidence.data_summary
        assert "## 新闻催化线索" in evidence.data_summary
        assert "## 研报催化线索" in evidence.data_summary
        assert "近90天公告中未找到催化事件" in evidence.data_summary
        assert "近180天新闻中未找到催化线索" in evidence.data_summary
        assert "研报中未找到催化线索" in evidence.data_summary


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
