from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api.v1.endpoints.agent.chat import (
    _build_professional_buy_decision_answer,
)
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    compile_task,
    workflow_for,
)
from src.agent.task_planner import resolve_task_plan
from src.services.buy_criteria.evaluators.entry_risk_reward import EntryRiskRewardEvaluator
from src.services.buy_criteria.evaluators.industrial_competitiveness import (
    IndustrialCompetitivenessEvaluator,
)
from src.services.buy_criteria.base import CriterionEvidence
from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import (
    INDUSTRIAL_COMPETITIVENESS,
    MAINLINE_POSITION,
)
from src.services.buy_criteria.professional_analysis import DIMENSION_DEFINITIONS
from src.tools.evaluate_multi_stock_buy_criteria import (
    evaluate_multi_stock_buy_criteria,
)


def _task(symbols: list[str] | None = None) -> StandardTask:
    return StandardTask(
        task_id="buy_now",
        kind=StandardTaskKind.INVESTMENT_DECISION,
        objective="这些股票中哪些现在能买入",
        entity_scope=EntityScope.PREVIOUS_ANSWER,
        entities=symbols or [],
        parameters={"thesis": "人形机器人上游核心零部件"},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.99,
    )


def _planner_response(task: StandardTask) -> SimpleNamespace:
    payload = {
        "tasks": [task.model_dump(mode="json")],
        "needs_clarification": False,
        "clarification_question": None,
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        tool_calls=[SimpleNamespace(function=SimpleNamespace(
            name="submit_standard_task_plan",
            arguments=json.dumps(payload, ensure_ascii=False),
        ))],
        content=None,
    ))])


def _dimension(index: int, status: str = "pass") -> dict:
    dimension_id, title = DIMENSION_DEFINITIONS[index]
    return {
        "dimension_id": dimension_id,
        "status": status,
        "headline": f"{title}判断",
        "analysis": f"{title}的支持证据和反证已经完成交叉核验。",
        "key_evidence": [f"{title}关键事实"],
        "counter_evidence": [f"{title}主要保留"],
        "monitoring_points": [f"跟踪{title}"],
    }


def _professional_result() -> dict:
    first_dimensions = [
        _dimension(index, "partial" if index == 6 else "pass")
        for index in range(8)
    ]
    second_dimensions = [
        _dimension(
            index,
            "fail" if index == 7 else "partial" if index in {0, 3, 6} else "pass",
        )
        for index in range(8)
    ]
    return {
        "success": True,
        "partial": False,
        "contract_version": "professional_buy_analysis_v2",
        "playbook": "professional_eight_dimension_buy_analysis",
        "items": [
            {
                "contract_version": "professional_buy_analysis_v2",
                "analysis_mode": "professional_eight_dimension_buy_analysis",
                "symbol": "600519",
                "name": "贵州茅台",
                "investment_profile": "高质量消费龙头",
                "overall_summary": "竞争力和现金流较强，估值赔率仍需等待更好价格。",
                "core_thesis": "品牌壁垒与现金流支撑长期价值",
                "biggest_issue": "估值赔率只有半通过",
                "recommendation_code": "conditional_buy",
                "recommendation": "满足条件时可进入买入计划",
                "recommendation_reason": "八项没有硬性失败，但需要遵守估值与价格条件。",
                "score": 7.5,
                "score_total": 8,
                "counts": {"pass": 7, "partial": 1, "fail": 0, "insufficient": 0},
                "dimensions": first_dimensions,
                "bull_case_chain": "品牌壁垒 → 稳定需求 → 现金流兑现 → 估值修复",
                "risk_chain": "需求走弱 → 批价承压 → 盈利预期下修",
                "monitoring_points": ["批价", "经营现金流", "估值分位"],
                "source_links": [{
                    "title": "2025年年度报告",
                    "source": "巨潮资讯",
                    "date": "2026-03-30",
                    "url": "https://example.com/report.pdf",
                }],
                "evidence_gaps": [],
                "coverage_complete": True,
                "data_time": "2026-07-23T12:00:00+08:00",
                "quote_basis": "盘中最新价",
            },
            {
                "contract_version": "professional_buy_analysis_v2",
                "analysis_mode": "professional_eight_dimension_buy_analysis",
                "symbol": "000858",
                "name": "五粮液",
                "investment_profile": "产业逻辑存在但质量仍需验证",
                "overall_summary": "四项通过、三项半通过、一项不通过，适合跟踪而非直接买入。",
                "core_thesis": "消费复苏与品牌修复",
                "biggest_issue": "重大风险项未通过",
                "recommendation_code": "watchlist",
                "recommendation": "进入中期跟踪池",
                "recommendation_reason": "产业逻辑存在，但风险和赔率没有形成高确定性。",
                "score": 5.5,
                "score_total": 8,
                "counts": {"pass": 4, "partial": 3, "fail": 1, "insufficient": 0},
                "dimensions": second_dimensions,
                "bull_case_chain": "需求恢复 → 渠道改善 → 盈利修复",
                "risk_chain": "库存压力 → 回款放缓 → 现金流承压",
                "monitoring_points": ["经营现金流", "库存", "渠道回款"],
                "source_links": [],
                "evidence_gaps": [],
                "coverage_complete": True,
                "data_time": "2026-07-23T12:00:00+08:00",
                "quote_basis": "盘中最新价",
            },
        ],
        "resolved_entities": [
            {"symbol": "600519", "name": "贵州茅台"},
            {"symbol": "000858", "name": "五粮液"},
        ],
        "unresolved_entities": [],
        "requested_count": 2,
        "covered_count": 2,
        "coverage_complete": True,
        "errors": [],
        "warnings": [],
        "data_time": "2026-07-21T12:00:00+08:00",
        "is_stale": None,
    }


class _Controller:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.tool_calls: list[str] = []
        self._stream_tasks: list = []
        self.assistant_text_snapshot = ""

    def append_text(self, text: str) -> None:
        self.texts.append(text)

    def append_reasoning(self, _text: str) -> None:
        return None

    async def add_tool_call(self, name: str, tool_call_id: str | None = None):
        del tool_call_id
        self.tool_calls.append(name)
        return SimpleNamespace(
            append_args_text=MagicMock(),
            set_response=MagicMock(),
        )


def test_professional_dimension_order_is_exact_and_includes_all_eight_axes() -> None:
    assert tuple(item[0] for item in DIMENSION_DEFINITIONS) == (
        "market_mainline",
        "industrial_competitiveness",
        "industry_cycle",
        "competition_quality",
        "growth_drivers",
        "forward_catalysts",
        "valuation_odds",
        "major_risks",
    )


def test_investment_decision_compiles_every_company_into_one_professional_review() -> None:
    symbols = tuple(f"{index:06d}" for index in range(72))
    candidate = _task()
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=symbols))
    assert len(calls) == 1
    assert len(calls[0].arguments["symbols"].split(",")) == 72
    assert [symbol for call in calls for symbol in call.arguments["symbols"].split(",")] == list(symbols)
    assert {call.tool_name for call in calls} == {"evaluate_multi_stock_buy_criteria"}
    assert workflow_for(StandardTaskKind.INVESTMENT_DECISION).max_parallel_steps == 1
    assert all(call.arguments["thesis"] == "人形机器人上游核心零部件" for call in calls)


def test_previous_collection_buy_follow_up_uses_semantic_planning() -> None:
    messages = [
        {"role": "user", "content": "按受益领域找股票"},
        {
            "role": "assistant",
            "content": (
                "| 公司/代码 | 匹配领域 |\n|---|---|\n"
                "| 贝斯特 (300580) | 行星滚柱丝杠 |\n"
                "| 绿的谐波 (688017) | 减速器 |"
            ),
        },
        {"role": "user", "content": "这些股票中哪些现在能买入"},
    ]
    previous_entities = [
        {"name": "贝斯特", "symbol": "300580"},
        {"name": "绿的谐波", "symbol": "688017"},
    ]

    candidate = _task().model_copy(update={
        "parameters": {"thesis": "行星滚柱丝杠；减速器"},
    })
    completion = AsyncMock(return_value=_planner_response(candidate))

    plan = asyncio.run(resolve_task_plan(
        messages,
        {"model": "test", "api_base": "", "api_key": None},
        completion=completion,
        current_entities=[],
        previous_answer_entities=previous_entities,
    ))
    assert completion.await_count == 1
    assert plan.tasks[0].kind == StandardTaskKind.INVESTMENT_DECISION
    assert plan.tasks[0].entity_scope == EntityScope.PREVIOUS_ANSWER
    assert plan.tasks[0].parameters["thesis"] == "行星滚柱丝杠；减速器"


def test_buy_follow_up_receives_structured_context_across_turns() -> None:
    messages = [{"role": "user", "content": "这些股票中哪些现在能买入"}]
    previous_entities = [
        {"name": "绿的谐波", "symbol": "688017"},
        {"name": "鸣志电器", "symbol": "603728"},
    ]

    domains = [{
        "label": "谐波减速器",
        "board_queries": ["减速器"],
        "mapping_type": "proxy_board",
        "rationale": "测试绑定",
        "unresolved_parts": [],
    }]
    context = {
        "version": "1",
        "turns": [{
            "request": "按方向找股票",
            "tasks": [{
                "task_id": "discover",
                "kind": "theme_stock_discovery",
                "objective": "领域找股",
                "parameters": {"domains": domains},
                "depends_on": [],
                "entities": previous_entities,
                "status": "completed",
                "result_context": [],
            }],
            "entities": previous_entities,
        }],
    }
    candidate = _task().model_copy(update={
        "parameters": {
            "thesis": "谐波减速器",
            "thesis_context": {"summary": "谐波减速器", "domains": domains},
        },
    })
    completion = AsyncMock(return_value=_planner_response(candidate))

    plan = asyncio.run(resolve_task_plan(
        messages,
        {"model": "test", "api_base": "", "api_key": None},
        completion=completion,
        current_entities=[],
        previous_answer_entities=previous_entities,
        conversation_context=context,
    ))
    assert plan.tasks[0].parameters["thesis_context"]["domains"] == domains
    planner_context = json.loads(completion.await_args.kwargs["messages"][1]["content"])
    assert planner_context["conversation_context"]["turns"][0]["tasks"][0]["parameters"]["domains"] == domains


def test_buy_data_service_consumes_structured_domain_contract() -> None:
    thesis_context = {
        "summary": "人形机器人执行机构",
        "domains": [
            {
                "label": "灵巧手及力控部件",
                "board_queries": ["机器人执行器"],
                "mapping_type": "proxy_board",
                "rationale": "已绑定",
                "unresolved_parts": [],
            },
            {
                "label": "电机（伺服电机/步进电机）",
                "board_queries": ["机器人执行器"],
                "mapping_type": "proxy_board",
                "rationale": "已绑定",
                "unresolved_parts": [],
            },
        ],
    }
    expected = {"success": True, "items": [], "errors": [], "warnings": []}
    with patch(
        "src.tools.get_domain_stock_candidates.get_domain_stock_candidates",
        return_value=expected,
    ) as candidate_tool:
        result = DataService().get_investment_thesis_candidates(thesis_context)

    assert result == expected
    domain_specs = candidate_tool.call_args.args[0]
    assert [item["label"] for item in domain_specs] == [
        "灵巧手及力控部件", "电机（伺服电机/步进电机）",
    ]
    assert all(item["board_queries"] == ["机器人执行器"] for item in domain_specs)


def test_explicit_current_buy_request_uses_semantic_planner() -> None:
    messages = [{
        "role": "user",
        "content": (
            "投资逻辑：谐波减速器；空心杯电机；无框力矩电机。"
            "绿的谐波（688017）和鸣志电器（603728）现在能买入吗？"
        ),
    }]
    current_entities = [
        {"name": "绿的谐波", "symbol": "688017"},
        {"name": "鸣志电器", "symbol": "603728"},
    ]

    candidate = _task().model_copy(update={
        "entity_scope": EntityScope.CURRENT_MESSAGE,
        "entities": ["绿的谐波", "鸣志电器"],
        "parameters": {"thesis": "谐波减速器；空心杯电机；无框力矩电机"},
    })
    completion = AsyncMock(return_value=_planner_response(candidate))

    plan = asyncio.run(resolve_task_plan(
        messages,
        {"model": "test", "api_base": "", "api_key": None},
        completion=completion,
        current_entities=current_entities,
        previous_answer_entities=[],
    ))
    assert completion.await_count == 1
    assert plan.tasks[0].entity_scope == EntityScope.CURRENT_MESSAGE
    assert plan.tasks[0].parameters["thesis"] == "谐波减速器；空心杯电机；无框力矩电机"


def test_entry_context_is_deterministic_and_returns_position_and_invalidation() -> None:
    evaluator = EntryRiskRewardEvaluator()
    technical = {
        "success": True,
        "data_time": "2026-07-21",
        "is_stale": False,
        "indicators": {
            "close": 10,
            "ma20": 9.8,
            "ma60": 9.2,
            "boll_lower": 9.0,
            "low_20d": 8.9,
            "low_60d": 8.5,
            "high_20d": 12,
            "high_60d": 13,
            "boll_upper": 12.5,
            "atr14": 0.4,
            "atr14_pct": 4,
            "rsi14": 60,
        },
    }
    result = evaluator.evaluate(
        "600519",
        {},
        {"technical": technical, "quote": {"price": 10, "data_time": "2026-07-21", "is_stale": False}},
    )
    assert result.passed is True
    assert result.details["risk_reward_ratio"] >= 2
    assert result.details["recommended_initial_position_pct"] == 5
    assert result.details["recommended_max_position_pct"] == 10
    assert result.details["invalidation_conditions"]

    failed = evaluator.evaluate(
        "600519",
        {},
        {"technical": technical, "quote": {"price": 10.8, "data_time": "2026-07-21", "is_stale": False}},
    )
    assert failed.passed is False
    assert failed.details["recommended_initial_position_pct"] == 0

    stale = evaluator.evaluate(
        "600519",
        {},
        {"technical": technical, "quote": {"price": 10, "data_time": "2026-07-18", "is_stale": True}},
    )
    assert stale.passed is False
    assert "新鲜度" in stale.verdict


def test_critical_data_failure_stops_before_model_can_return_a_false_positive() -> None:
    evaluator = MainlinePositionEvaluator()
    evidence = CriterionEvidence(
        raw_data={"market_mainline_report_error": "upstream unavailable"},
        data_summary="报告状态：获取失败",
    )
    with patch.object(evaluator, "collect_data", return_value=evidence), patch.object(
        evaluator, "_call_llm"
    ) as llm:
        result = evaluator.evaluate("600519", {"symbol": "600519"})
    assert result.passed is False
    assert "获取失败" in result.verdict
    llm.assert_not_called()


def test_industrial_competitiveness_accepts_formal_substitute_evidence_collection() -> None:
    evaluator = IndustrialCompetitivenessEvaluator()
    stock_info = {
        "symbol": "300000",
        "name": "测试公司",
        "main_business": "机器人执行器",
        "_investment_thesis": "人形机器人核心零部件",
        "_investment_thesis_context": {"summary": "人形机器人核心零部件", "domains": [{
            "label": "人形机器人核心零部件", "board_queries": ["机器人执行器"],
            "mapping_type": "proxy_board", "rationale": "测试绑定", "unresolved_parts": [],
        }]},
    }
    with patch.object(DataService, "get_business_segments", return_value={"items": []}), patch(
        "src.services.buy_criteria.evaluators.industrial_competitiveness.DataService.get_financials",
        return_value={"items": [{"report_date": "2026Q1", "revenue_yoy": 40}]},
    ), patch(
        "src.services.buy_criteria.evaluators.industrial_competitiveness.DataService.get_announcements",
        return_value={"items": [{"publish_date": "2026-06-01", "title": "产能扩建公告", "url": "https://example.test/a"}]},
    ), patch(
        "src.services.buy_criteria.evaluators.industrial_competitiveness.DataService.search_news",
        return_value={"items": [{"publish_time": "2026-06-02", "title": "获客户订单", "summary": "订单、客户验证与量产"}]},
    ), patch(
        "src.services.buy_criteria.evaluators.industrial_competitiveness.DataService.get_research_report",
        return_value={"items": []},
    ):
        evidence = evaluator.collect_data("300000", stock_info)
    assert "未取得可用的分业务收入或利润披露" in evidence.data_summary
    assert "产能扩建公告" in evidence.data_summary
    assert "订单、客户验证与量产" in evidence.data_summary
    assert "不得把订单或量产公告设成额外必选项" in evidence.data_summary


def test_mainline_uses_structured_current_branch_instead_of_a_closed_broad_label_list() -> None:
    evaluator = MainlinePositionEvaluator()
    stock_info = {
        "symbol": "688017",
        "name": "绿的谐波",
        "industry": "通用设备制造业",
        "main_business": "精密传动装置研发、设计、生产和销售",
        "business_scope": "精密谐波减速器、机电一体化产品的研发和生产",
        "_investment_thesis": "谐波减速器",
        "_investment_thesis_context": {"summary": "谐波减速器", "domains": [{
            "label": "谐波减速器", "board_queries": ["减速器"],
            "mapping_type": "proxy_board", "rationale": "测试绑定", "unresolved_parts": [],
        }]},
    }
    broad_report = {
        "report_pending": False,
        "as_of_date": "2026-07-21",
        "overview": "科技成长活跃",
        "market_stage": {"label": "结构行情"},
        "current_mainlines": [{"name": "科技成长", "branches": ["半导体"]}],
        "future_mainlines": [],
    }
    sectors = {
        "concept": {
            "data_time": "2026-07-21",
            "items": [
                {"name": "人形机器人", "change_pct": 2.85, "net_flow": 225255.5},
                {"name": "减速器", "change_pct": 1.52, "net_flow": 11565.57},
            ],
        },
        "industry": {
            "data_time": "2026-07-21",
            "items": [{"name": "通用设备制造业", "change_pct": 3.52}],
        },
    }
    candidates = {
        "success": True,
        "partial": False,
        "requested_domains": ["谐波减速器"],
        "inferred_context_themes": ["人形机器人", "机器人概念"],
        "items": [{
            "symbol": "688017",
            "matched_domains": ["谐波减速器"],
            "lookup_themes": ["减速器"],
            "boards": ["减速器"],
            "sources": [{"name": "东方财富概念板块", "board": "减速器", "date": "2026-07-21"}],
        }],
    }
    with patch.object(DataService, "get_market_mainline_report", return_value=broad_report), patch.object(
        DataService, "get_sector_list", side_effect=lambda sector_type: sectors[sector_type],
    ), patch.object(
        DataService, "get_investment_thesis_candidates", return_value=candidates,
    ), patch.object(DataService, "get_sentiment", side_effect=AssertionError("mainline must not fetch sentiment")), patch.object(
        DataService, "get_social_sentiment", side_effect=AssertionError("mainline must not fetch social chatter"),
    ):
        evidence = evaluator.collect_data("688017", stock_info)

    assert evidence.raw_data["thesis_membership"]["company_matched"] is True
    assert evidence.raw_data["thesis_membership"]["context_themes"] == ["人形机器人", "机器人概念"]
    assert "人形机器人" in evidence.data_summary
    assert "减速器" in evidence.data_summary
    assert evaluator.evidence_failure_reason(evidence) is None
    assert "不是细分方向的封闭白名单" in MAINLINE_POSITION


def test_mature_formal_product_does_not_require_a_named_order_or_mass_production_notice() -> None:
    assert "必须至少取得订单" not in INDUSTRIAL_COMPETITIVENESS
    assert "不要求公司必须披露带有某一下游主题名称的专项订单" in INDUSTRIAL_COMPETITIVENESS

    evaluator = IndustrialCompetitivenessEvaluator()
    stock_info = {
        "symbol": "603728",
        "name": "鸣志电器",
        "main_business": "控制电机及其驱动系统",
        "business_scope": "电机、驱动器及控制系统研发、生产和销售",
        "company_profile": "长期从事控制电机及驱动系统业务",
        "_investment_thesis": "空心杯电机",
        "_investment_thesis_context": {"summary": "空心杯电机", "domains": [{
            "label": "空心杯电机", "board_queries": ["机器人执行器"],
            "mapping_type": "proxy_board", "rationale": "测试绑定", "unresolved_parts": [],
        }]},
    }
    with patch.object(DataService, "get_business_segments", return_value={"items": []}), patch.object(
        DataService, "get_financials", return_value={"items": [{"report_date": "2026Q1", "revenue_yoy": 12}]},
    ), patch.object(DataService, "get_announcements", return_value={"items": []}), patch.object(
        DataService, "search_news", return_value={"items": []},
    ), patch.object(DataService, "get_research_report", return_value={"items": [{
        "publish_date": "2026-06-01",
        "org": "产业研究机构",
        "title": "控制电机平台竞争力",
        "summary": "产品平台、技术积累与客户应用形成竞争壁垒",
    }]}), patch.object(
        DataService,
        "get_investment_thesis_candidates",
        return_value={
            "requested_domains": ["空心杯电机"],
            "items": [{"symbol": "603728", "matched_domains": ["空心杯电机"], "boards": ["机器人执行器"]}],
        },
    ):
        evidence = evaluator.collect_data("603728", stock_info)
    assert "经营范围：电机、驱动器及控制系统研发、生产和销售" in evidence.data_summary
    assert "不得把订单或量产公告设成额外必选项" in evidence.data_summary
    assert evaluator.evidence_failure_reason(evidence) is None


def test_multi_stock_tool_never_silently_truncates_and_preserves_all_results() -> None:
    resolved = [
        {"symbol": "600519", "name": "贵州茅台"},
        {"symbol": "000858", "name": "五粮液"},
    ]
    with patch(
        "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch(
        "src.tools.evaluate_multi_stock_buy_criteria.analyze_professional_buy",
        side_effect=[_professional_result()["items"][0], _professional_result()["items"][1]],
    ):
        result = evaluate_multi_stock_buy_criteria("600519,000858", "消费复苏")
    assert result["requested_count"] == 2
    assert result["covered_count"] == 2
    assert [item["symbol"] for item in result["items"]] == ["600519", "000858"]

    too_many = [{"symbol": f"{index:06d}", "name": str(index)} for index in range(301)]
    with patch(
        "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
        return_value=(too_many, []),
    ):
        rejected = evaluate_multi_stock_buy_criteria("nine")
    assert rejected["success"] is False
    assert rejected["covered_count"] == 0
    assert "没有静默截断" in rejected["errors"][0]


def test_renderer_outputs_all_eight_axes_score_chains_and_monitoring() -> None:
    evidence = [{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "600519,000858"},
        "result": _professional_result(),
    }]
    answer = _build_professional_buy_decision_answer(evidence)
    assert answer is not None
    assert "贵州茅台 (600519) | ✅7 / ◐1 / ❌0 / ?0 | 7.5/8" in answer
    assert "五粮液 (000858) | ✅4 / ◐3 / ❌1 / ?0 | 5.5/8" in answer
    assert answer.count("### 8. ❌ 无重大风险隐患") == 1
    assert "看多链条" in answer
    assert "风险链条" in answer
    assert "后续最关键的验证指标" in answer
    missing_answer = _build_professional_buy_decision_answer([{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "600519,000858,300750"},
        "result": _professional_result(),
    }])
    assert missing_answer is not None
    assert "缺失 **1 只**" in missing_answer


def test_production_follow_up_uses_only_professional_internal_tool_and_validated_answer() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    candidate = _task(["贵州茅台", "五粮液"])
    plan = TaskPlan(tasks=[candidate])
    resolved = [ResolvedTask(candidate=candidate, symbols=("600519", "000858"))]
    controller = _Controller()

    def execute(name: str, arguments: dict, **_kwargs) -> dict:
        assert name == "evaluate_multi_stock_buy_criteria"
        assert arguments["symbols"] == "600519,000858"
        return _professional_result()

    async def run() -> str:
        with patch.object(chat_mod, "resolve_task_plan", new=AsyncMock(return_value=plan)), patch.object(
            chat_mod, "resolve_plan_entities", return_value=resolved
        ), patch.object(
            chat_mod, "execute_tool_isolated", side_effect=execute
        ), patch.object(
            chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value
        ), patch.object(
            chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value
        ), patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "这些股票中哪些现在能买入"}],
                {"model": "test", "api_base": "", "api_key": None, "extra_headers": None},
            )

    answer = asyncio.run(run())
    assert controller.tool_calls == ["evaluate_multi_stock_buy_criteria"]
    assert "贵州茅台 (600519) | ✅7 / ◐1 / ❌0 / ?0 | 7.5/8" in answer
    assert "五粮液 (000858) | ✅4 / ◐3 / ❌1 / ?0 | 5.5/8" in answer
    assert "### 8. ❌ 无重大风险隐患" in answer
    assert "网络搜索" not in " ".join(controller.tool_calls)
