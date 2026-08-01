from __future__ import annotations

import inspect
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import akshare as ak
import pandas as pd
import pytest

from src.agent.orchestrator_v2.contracts import Capability, EvidenceDimension
from src.agent.orchestrator_v2.intents import MarketOverviewIntent
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.industry_index_renderers import build_industry_index_context_answer
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    compile_task,
    workflow_for,
)
from src.services.akshare_evidence import get_company_evidence
from src.services.akshare_evidence.company_events import _investor_relations_qa
from src.tools.get_industry_index_context import get_industry_index_context
from src.tools.get_index_data import INDEX_MAP
from src.tools.get_market_regime import get_market_regime
from src.tools._akshare import cached_call
from src.tools.registry import ToolRegistry


def _uncached(key, fn, *, ttl_seconds=0, attempts=2):
    del key, ttl_seconds, attempts
    return fn(), False


def _task(kind: StandardTaskKind, parameters: dict) -> ResolvedTask:
    return ResolvedTask(
        candidate=StandardTask(
            task_id=kind.value,
            kind=kind,
            objective="test",
            entity_scope=EntityScope.NONE,
            entities=[],
            parameters=parameters,
            depends_on=[],
            output_requirements=[],
            confirmation=ConfirmationState.NOT_REQUIRED,
            confidence=1.0,
        ),
        symbols=(),
    )


def test_company_evidence_projects_market_wide_rows_to_one_symbol() -> None:
    matching = pd.DataFrame([{"股票代码": "600519", "公告日期": "2026-07-01", "值": 1}, {"股票代码": "000001", "值": 2}])
    direct = pd.DataFrame([{"公告日期": "2026-07-02", "状态": "正常"}])
    release_queue = pd.DataFrame([{"解禁时间": "2026-09-01", "解禁数量": 100}])
    release_holder = pd.DataFrame([{"股东名称": "示例股东", "解禁数量": 100}])
    with (
        patch("src.services.akshare_evidence.common.cached_call", side_effect=_uncached),
        patch.object(ak, "stock_gpzy_individual_pledge_ratio_detail_em", return_value=direct),
        patch.object(ak, "stock_restricted_release_queue_em", return_value=release_queue),
        patch.object(ak, "stock_restricted_release_stockholder_em", return_value=release_holder) as stockholder,
        patch.object(ak, "stock_hsgt_individual_em", return_value=pd.DataFrame([{"持股日期": "2026-07-01"}])),
        patch.object(ak, "stock_hsgt_individual_detail_em", return_value=pd.DataFrame([{"持股日期": "2026-07-01", "机构名称": "示例机构"}])) as northbound_detail,
        patch.object(ak, "stock_ggcg_em", return_value=matching),
        patch.object(ak, "stock_hold_control_cninfo", return_value=matching),
    ):
        result = get_company_evidence("600519", sections=("ownership",))

    section = result["sections"]["ownership"]
    assert result["coverage_complete"] is True
    assert section["datasets"]["shareholder_changes"]["item_count"] == 1
    assert section["datasets"]["control_structure"]["items"][0]["股票代码"] == "600519"
    assert section["datasets"]["restricted_release_shareholders"]["items"][0]["股东名称"] == "示例股东"
    stockholder.assert_called_once_with(symbol="600519", date="20260901")
    northbound_detail.assert_called_once_with(symbol="600519", start_date="20260701", end_date="20260701")
    assert "实时北向净买入" in section["warnings"][0]


def test_akshare_cache_single_flights_parallel_market_feed_calls() -> None:
    counter = 0
    lock = threading.Lock()

    def factory():
        nonlocal counter
        with lock:
            counter += 1
        time.sleep(0.05)
        return {"rows": 1}

    key = f"singleflight-test:{time.time_ns()}"
    with (
        patch("src.tools._akshare._persistent_cache_get", return_value=None),
        patch("src.tools._akshare._persistent_cache_put"),
        patch("src.tools._akshare.akshare_rate_limiter.wait"),
        ThreadPoolExecutor(max_workers=6) as pool,
    ):
        results = list(pool.map(lambda _: cached_call(key, factory, ttl_seconds=60), range(6)))

    assert counter == 1
    assert all(result[0] == {"rows": 1} for result in results)


def test_pledge_detail_falls_back_to_the_latest_market_ratio_projection() -> None:
    market = pd.DataFrame(
        [
            {"股票代码": "600519", "公告日期": "2026-07-01", "质押股份数量": 10},
            {"股票代码": "000001", "公告日期": "2026-07-01", "质押股份数量": 20},
        ]
    )
    empty = pd.DataFrame()
    with (
        patch("src.services.akshare_evidence.common.cached_call", side_effect=_uncached),
        patch.object(ak, "stock_gpzy_individual_pledge_ratio_detail_em", side_effect=TypeError("empty result")),
        patch.object(ak, "stock_gpzy_profile_em", return_value=pd.DataFrame([{"交易日期": "2026-07-31"}])),
        patch.object(ak, "stock_gpzy_pledge_ratio_em", return_value=market) as market_call,
        patch.object(ak, "stock_restricted_release_queue_em", return_value=empty),
        patch.object(ak, "stock_hsgt_individual_em", return_value=empty),
        patch.object(ak, "stock_hsgt_individual_detail_em", return_value=empty),
        patch.object(ak, "stock_ggcg_em", return_value=empty),
        patch.object(ak, "stock_hold_control_cninfo", return_value=empty),
    ):
        result = get_company_evidence("600519", sections=("ownership",))

    pledges = result["sections"]["ownership"]["datasets"]["equity_pledges"]
    assert pledges["success"] is True
    assert pledges["source_api"] == "AKShare.stock_gpzy_pledge_ratio_em"
    assert [item["股票代码"] for item in pledges["items"]] == ["600519"]
    assert pledges["fallback_from"] == "AKShare.stock_gpzy_individual_pledge_ratio_detail_em"
    assert pledges["pledge_snapshot_date"] == "20260731"
    market_call.assert_called_once_with(date="20260731")


def test_investor_qa_dispatches_to_the_market_specific_akshare_source() -> None:
    frame = pd.DataFrame([{"股票代码": "600519", "问题": "示例问题", "回答": "示例回答"}])
    with (
        patch("src.services.akshare_evidence.common.cached_call", side_effect=_uncached),
        patch.object(ak, "stock_sns_sseinfo", return_value=frame) as sse,
        patch.object(ak, "stock_irm_cninfo", return_value=frame) as cninfo,
    ):
        shanghai = _investor_relations_qa(ak, "600519")
        shenzhen = _investor_relations_qa(ak, "000001")

    assert shanghai["platform"] == "上证e互动"
    assert shenzhen["platform"] == "互动易"
    sse.assert_called_once_with(symbol="600519")
    cninfo.assert_called_once_with(symbol="000001")


def test_financial_events_query_report_period_feeds_and_keep_source_stage() -> None:
    matching = pd.DataFrame([{"股票代码": "600519", "公告日期": "2026-04-10", "预告类型": "预增"}])
    with (
        patch("src.services.akshare_evidence.common.cached_call", side_effect=_uncached),
        patch.object(ak, "stock_yjyg_em", return_value=matching),
        patch.object(ak, "stock_yjbb_em", return_value=matching),
        patch.object(ak, "stock_yjkb_em", return_value=matching),
        patch.object(ak, "stock_sy_yq_em", return_value=matching),
        patch.object(ak, "stock_sy_jz_em", return_value=matching),
        patch.object(ak, "stock_report_disclosure", return_value=matching),
    ):
        result = get_company_evidence(
            "600519",
            sections=("financial_events",),
            report_period_count=2,
        )

    datasets = result["sections"]["financial_events"]["datasets"]
    assert set(datasets) == {
        "performance_forecasts",
        "performance_reports",
        "performance_flashes",
        "goodwill_impairment_expectations",
        "goodwill_impairments",
        "report_disclosure_schedule",
    }
    assert all(dataset["source_api"].startswith("AKShare.") for dataset in datasets.values())
    assert datasets["performance_forecasts"]["items"][0]["report_period"]


def test_market_regime_keeps_every_source_as_an_independent_dataset() -> None:
    frame = pd.DataFrame([{"日期": "2026-07-30", "value": 1}, {"日期": "2026-07-31", "value": 2}])
    with (
        patch("src.services.akshare_evidence.common.cached_call", side_effect=_uncached),
        patch.object(ak, "stock_a_high_low_statistics", return_value=frame),
        patch.object(ak, "stock_ebs_lg", return_value=frame),
        patch.object(ak, "stock_market_pe_lg", return_value=frame),
        patch.object(ak, "stock_market_pb_lg", return_value=frame),
        patch.object(ak, "stock_index_pe_lg", return_value=frame),
        patch.object(ak, "stock_a_congestion_lg", return_value=frame),
    ):
        result = get_market_regime(history_points=20)

    assert result["coverage_complete"] is True
    assert set(result["datasets"]) == {
        "new_high_low",
        "equity_bond_spread",
        "shanghai_pe",
        "shenzhen_pe",
        "shanghai_pb",
        "shenzhen_pb",
        "index_pe",
        "congestion",
    }
    assert result["datasets"]["new_high_low"]["latest"]["value"] == 2


def test_sw_industry_context_uses_matched_code_for_history_and_components() -> None:
    catalog = pd.DataFrame([{"指数代码": "801120", "指数名称": "食品饮料", "最新价": 1000}])
    history = pd.DataFrame([{"日期": "2026-07-30", "收盘": 990}, {"日期": "2026-07-31", "收盘": 1000}])
    components = pd.DataFrame([{"证券代码": "600519", "证券名称": "贵州茅台"}])
    with (
        patch("src.services.akshare_evidence.common.cached_call", side_effect=_uncached),
        patch.object(ak, "index_realtime_sw", return_value=catalog),
        patch.object(ak, "index_hist_sw", return_value=history) as history_call,
        patch.object(ak, "index_component_sw", return_value=components) as component_call,
    ):
        result = get_industry_index_context("食品饮料", history_points=20)

    assert result["matches"][0]["指数代码"] == "801120"
    assert result["histories"]["801120"]["item_count"] == 2
    assert result["components"]["801120"]["items"][0]["证券代码"] == "600519"
    history_call.assert_called_once_with(symbol="801120", period="day")
    component_call.assert_called_once_with(symbol="801120")


def test_adapter_calls_are_guarded_by_the_current_akshare_source_signatures() -> None:
    expected = {
        "stock_restricted_release_stockholder_em": {"symbol", "date"},
        "stock_hsgt_individual_detail_em": {"symbol", "start_date", "end_date"},
        "stock_jgdy_tj_em": {"date"},
        "stock_jgdy_detail_em": {"date"},
        "stock_irm_cninfo": {"symbol"},
        "stock_sns_sseinfo": {"symbol"},
        "index_hist_sw": {"symbol", "period"},
    }
    for name, parameters in expected.items():
        assert parameters <= set(inspect.signature(getattr(ak, name)).parameters), name


def test_a_share_industry_index_context_is_reachable_through_typed_workflows() -> None:
    cases = {
        StandardTaskKind.INDUSTRY_INDEX_RESEARCH: (
            {"query": "食品饮料", "index_type": "一级行业"},
            "get_industry_index_context",
            Capability.INDUSTRY_INDEX_RESEARCH,
        ),
    }
    for kind, (parameters, tool_name, capability) in cases.items():
        calls = compile_task(_task(kind, parameters))
        assert [call.tool_name for call in calls] == [tool_name]
        specification = capability_for(capability)
        assert specification.capability == capability
        assert {
            EvidenceDimension.INDUSTRY_STRUCTURE,
            EvidenceDimension.PRICE_HISTORY,
        } <= specification.evidence_dimensions
    assert workflow_for(StandardTaskKind.INDUSTRY_INDEX_RESEARCH).result_contract == "industry_index_context"


def test_industry_index_result_contract_renders_all_source_constituents_without_synthesis() -> None:
    result = {
        "success": True,
        "index_type": "一级行业",
        "source": "AKShare/申万宏源研究指数",
        "data_time": "2026-08-01T09:32:22+08:00",
        "matches": [{"指数代码": "801120", "指数名称": "食品饮料"}],
        "histories": {
            "801120": {
                "success": True,
                "items": [
                    {"日期": "2026-02-02", "收盘": 900.0},
                    {"日期": "2026-07-31", "收盘": 990.0},
                ],
            }
        },
        "components": {
            "801120": {
                "success": True,
                "items": [
                    {"证券代码": "600519", "证券名称": "贵州茅台"},
                    {"证券代码": "000858", "证券名称": "五粮液"},
                    {"证券代码": "600809", "证券名称": "山西汾酒"},
                ],
            }
        },
        "membership_boundary": "指数成分关系只证明申万分类归属，不证明订单、收入或投资价值。",
        "errors": [],
    }
    answer = build_industry_index_context_answer(
        [{"tool": "get_industry_index_context", "result": result}]
    )

    assert answer is not None
    assert "区间变动 **+10.00%**" in answer
    assert "共 **3 只**；以下逐条列出本次源数据返回的全部成分股。" in answer
    assert all(code in answer for code in ("600519", "000858", "600809"))
    assert "指数成分关系只证明申万分类归属" in answer


def test_fundamental_workflow_requests_only_company_relevant_structured_evidence() -> None:
    task = ResolvedTask(
        candidate=StandardTask(
            task_id="fundamental_analysis",
            kind=StandardTaskKind.FUNDAMENTAL_ANALYSIS,
            objective="核验公司基本面事项",
            entity_scope=EntityScope.CURRENT_MESSAGE,
            entities=["300750"],
            parameters={},
            depends_on=[],
            output_requirements=[],
            confirmation=ConfirmationState.NOT_REQUIRED,
            confidence=1.0,
        ),
        symbols=("300750",),
    )

    calls = compile_task(task)
    structured = next(call for call in calls if call.tool_name == "get_company_structured_evidence")

    assert structured.arguments["scope"] == "risk_and_catalyst"


def test_market_overview_and_index_tool_share_the_supported_index_catalog() -> None:
    assert INDEX_MAP["000300"] == ("沪深300", "sh000300")
    assert MarketOverviewIntent(index_code="000300").index_code == "000300"

    with pytest.raises(ValueError, match="supported mainland index"):
        MarketOverviewIntent(index_code="000852")


def test_akshare_expansion_does_not_register_off_scope_market_tools() -> None:
    tool_names = set(ToolRegistry().get_tool_names())
    assert {
        "get_fund_portfolio",
        "get_derivatives_context",
        "get_cross_market_stock_research",
    }.isdisjoint(tool_names)
    assert {
        "fund_research",
        "derivatives_research",
        "cross_market_research",
    }.isdisjoint(capability.value for capability in Capability)
