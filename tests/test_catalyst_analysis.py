from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api.v1.endpoints.agent import chat as chat_mod
from api.v1.endpoints.agent.chat import (
    _build_catalyst_analysis_answer,
    _run_standard_task_pipeline,
)
from src.agent.task_planner import resolve_task_plan
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    compile_task,
)
from src.services.buy_criteria.base import CriterionEvidence, CriterionResult
from src.services.buy_criteria.evaluators.catalyst_events import (
    CatalystEventsEvaluator,
    normalize_catalyst_details,
)
from src.services.catalyst_evidence import (
    extract_business_passages,
    extract_forward_window_passages,
    fetch_formal_document,
    select_formal_documents,
)
from src.tools.analyze_stock_catalysts import analyze_stock_catalysts


def _task() -> StandardTask:
    return StandardTask(
        task_id="future_catalysts",
        kind=StandardTaskKind.CATALYST_ANALYSIS,
        objective="看下鸣志电器未来 6—12 个月的催化事件",
        entity_scope=EntityScope.CURRENT_MESSAGE,
        entities=[],
        parameters={},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=1.0,
    )


def _payload() -> dict:
    return {
        "success": True,
        "partial": False,
        "items": [{
            "symbol": "603728",
            "name": "鸣志电器",
            "horizon": "未来6—12个月",
            "passed": True,
            "verdict": "2027年一季度存在有明确来源的产品量产窗口。",
            "catalysts": [{
                "event": "新一代产品进入量产验证",
                "time_window": "2027-Q1",
                "why_it_matters": "若按期量产，将验证新增订单与收入兑现。",
                "confidence": "中",
                "verification_status": "多源印证",
                "evidence_ids": ["A1", "R1"],
                "sources": [{
                    "evidence_id": "A1",
                    "title": "关于新产品项目进展的公告",
                    "date": "2026-07-10",
                    "source": "公司公告",
                    "url": "https://example.com/a1",
                }],
            }],
            "missing_evidence": ["量产后的实际出货仍需后续公告确认"],
            "source_coverage": {
                "available_count": 3,
                "required_count": 3,
                "complete": True,
            },
            "retrieved_evidence": {
                "report_schedule": [{
                    "evidence_id": "S1",
                    "title": "2026年半年报预约披露",
                    "time_window": "2026-08-26",
                    "url": "https://example.com/schedule",
                }],
            },
            "analyzed_at": "2026-07-21T10:00:00+08:00",
        }],
        "resolved_entities": [{"symbol": "603728", "name": "鸣志电器"}],
        "unresolved_entities": [],
        "requested_count": 1,
        "covered_count": 1,
        "coverage_complete": True,
        "horizon": "未来6—12个月",
        "decision_boundary": "催化研究不等于买入判断",
        "source": "内部同步公司公告、公司新闻、券商研报",
        "errors": [],
        "warnings": [],
        "data_time": "2026-07-21T10:00:00+08:00",
        "is_stale": None,
    }


def test_explicit_company_catalyst_request_uses_semantic_planner() -> None:
    payload = {
        "tasks": [_task().model_dump(mode="json")],
        "needs_clarification": False,
        "clarification_question": None,
    }
    plan_response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        tool_calls=[SimpleNamespace(function=SimpleNamespace(
            name="submit_standard_task_plan",
            arguments=json.dumps(payload, ensure_ascii=False),
        ))],
        content=None,
    ))])
    completion = AsyncMock(return_value=plan_response)

    plan = asyncio.run(resolve_task_plan(
        [{"role": "user", "content": "看下鸣志电器未来 6—12 个月的催化事件"}],
        {"model": "test", "api_base": ""},
        completion=completion,
        current_entities=[{"name": "鸣志电器", "symbol": "603728"}],
        previous_answer_entities=[],
    ))

    assert completion.await_count == 1
    assert plan.tasks[0].kind == StandardTaskKind.CATALYST_ANALYSIS
    assert plan.tasks[0].entity_scope == EntityScope.CURRENT_MESSAGE


def test_catalyst_workflow_uses_only_the_internal_catalyst_tool() -> None:
    calls = compile_task(ResolvedTask(candidate=_task(), symbols=("603728",)))
    assert len(calls) == 1
    assert calls[0].tool_name == "analyze_stock_catalysts"
    assert calls[0].arguments == {"symbols": "603728"}


def test_catalyst_details_must_bind_to_real_ids_and_calendar_windows() -> None:
    raw = {
        "announcement_events": [{
            "evidence_id": "A1",
            "title": "量产项目公告",
            "date": "2026-07-01",
            "label": "项目进展",
            "url": "https://example.com/a1",
        }],
        "news_events": [],
        "research_events": [],
    }
    normalized = normalize_catalyst_details({
        "catalysts": [
            {
                "event": "项目量产",
                "time_window": "2027-Q1",
                "evidence_ids": ["A1"],
                "why_it_matters": "贡献收入",
            },
            {
                "event": "模型虚构事件",
                "time_window": "2027-Q2",
                "evidence_ids": ["N99"],
            },
            {
                "event": "模糊时间事件",
                "time_window": "未来半年",
                "evidence_ids": ["A1"],
            },
        ],
    }, raw)

    assert len(normalized) == 1
    assert normalized[0]["event"] == "项目量产"
    assert normalized[0]["sources"][0]["title"] == "量产项目公告"
    assert normalized[0]["sources"][0]["url"] == "https://example.com/a1"


def test_formal_report_selection_finds_annual_report_beyond_recent_titles() -> None:
    announcements = [
        {
            "title": f"日常公告{index}",
            "publish_date": f"2026-07-{20 - index:02d}",
            "url": f"https://example.com/AN202607010000000{index:02d}.html",
        }
        for index in range(10)
    ]
    announcements.append({
        "title": "鸣志电器:鸣志电器2025年年度报告",
        "publish_date": "2026-04-25",
        "url": "https://data.eastmoney.com/notices/detail/603728/AN202604241821560106.html",
    })

    selected = select_formal_documents(announcements)

    assert [item["title"] for item in selected] == ["鸣志电器:鸣志电器2025年年度报告"]


def test_forward_report_passages_are_recalled_by_calendar_structure_not_catalyst_keywords() -> None:
    content = (
        "甲项目在2025年第四季度完成。"
        "冷却系统用核心部件已经获得客户认可，预计于2026年下半年启动交付。"
        "另一制造基地预计2026年四季度可投入使用，为后续规模化交付提供支撑。"
    )

    passages = extract_forward_window_passages(
        content,
        as_of=date(2026, 7, 21),
    )

    assert [item["time_window"] for item in passages] == ["2026年下半年", "2026年四季度"]
    assert "启动交付" in passages[0]["excerpt"]


def test_business_report_passages_use_uniform_document_coverage() -> None:
    content = (
        "公司消费电子业务保持稳定。"
        "机器人头部模组已稳定量产，六维力传感器、关节模组与灵巧手模组进入客户供应链。"
        "公司已建立机器人整机研发和批量生产体系。"
    )
    passages = extract_business_passages(
        content,
        thesis="人形机器人上游核心零部件",
        thesis_context={"domains": [{
            "label": "灵巧手及力控部件",
            "board_queries": ["人形机器人", "机器人执行器"],
        }]},
    )

    assert passages
    assert "六维力传感器" in passages[0]["excerpt"]
    assert passages[0]["selection_method"] == "uniform_document_coverage"


def test_formal_document_uses_primary_pdf_when_metadata_body_is_empty() -> None:
    primary_pdf = "https://static.cninfo.com.cn/finalpage/2026-03-31/report.pdf"
    with patch(
        "src.services.catalyst_evidence._fetch_content_page",
        return_value={
            "notice_title": "测试公司2025年年度报告",
            "notice_date": "2026-03-31",
            "notice_content": "",
            "page_size": 1,
            "attach_url_web": "https://secondary.example/report.pdf",
        },
    ), patch(
        "src.services.catalyst_evidence._extract_pdf_text",
        return_value=("机器人核心部件已经规模化交付。", 88),
    ) as extract_pdf:
        document = fetch_formal_document({
            "url": "https://data.example/AN20260331000001.html",
            "preferred_document_url": primary_pdf,
        })

    extract_pdf.assert_called_once_with(primary_pdf, max_pages=120)
    assert document["content"] == "机器人核心部件已经规模化交付。"
    assert document["page_count"] == 88


def test_catalyst_gate_fails_closed_when_model_does_not_return_bound_events() -> None:
    evaluator = CatalystEventsEvaluator()
    evidence = CriterionEvidence(
        raw_data={
            "announcement_events": [{
                "evidence_id": "A1",
                "title": "项目进展",
                "date": "2026-07-01",
            }],
            "news_events": [],
            "research_events": [],
        },
        data_summary="evidence",
    )
    with patch.object(evaluator, "collect_data", return_value=evidence), patch.object(
        evaluator,
        "_call_llm",
        return_value=({"passed": True, "verdict": "存在催化"}, ""),
    ):
        result = evaluator.evaluate("603728", {"symbol": "603728", "name": "鸣志电器"})

    assert result.passed is False
    assert result.details["catalysts"] == []
    assert "明确日历时间窗" in result.verdict


def test_catalyst_tool_preserves_normalized_sources_and_coverage() -> None:
    raw = {
        "announcement_events": [{"evidence_id": "A1", "title": "项目公告"}],
        "news_events": [{"evidence_id": "N1", "title": "产品新闻"}],
        "research_events": [{"evidence_id": "R1", "title": "公司研报"}],
    }
    criterion = CriterionResult(
        criterion_id="catalyst_events",
        criterion_name="催化事件",
        index=5,
        passed=True,
        verdict="存在明确催化",
        evidence=CriterionEvidence(raw_data=raw, data_summary="evidence"),
        details={"catalysts": _payload()["items"][0]["catalysts"], "missing_evidence": []},
        analyzed_at="2026-07-21T10:00:00+08:00",
    )
    with patch(
        "src.tools.analyze_stock_catalysts.resolve_securities_csv",
        return_value=([{"symbol": "603728", "name": "鸣志电器"}], []),
    ), patch(
        "src.tools.analyze_stock_catalysts.DataService.get_stock_info",
        return_value={"symbol": "603728", "name": "鸣志电器"},
    ), patch(
        "src.tools.analyze_stock_catalysts.CatalystEventsEvaluator.evaluate",
        return_value=criterion,
    ):
        result = analyze_stock_catalysts("鸣志电器")

    assert result["success"] is True
    assert result["items"][0]["source_coverage"]["complete"] is True
    assert result["items"][0]["catalysts"][0]["evidence_ids"] == ["A1", "R1"]


def test_deterministic_catalyst_answer_contains_window_source_and_boundary() -> None:
    answer = _build_catalyst_analysis_answer([{
        "tool": "analyze_stock_catalysts",
        "arguments": {"symbols": "603728"},
        "result": _payload(),
    }])
    assert answer is not None
    assert "2027-Q1" in answer
    assert "关于新产品项目进展的公告" in answer
    assert "https://example.com/a1" in answer
    assert "2026-08-26" in answer
    assert "不自动等于利好" in answer
    assert "不等于现在可以买入" in answer


def test_deterministic_catalyst_answer_shows_raw_clues_when_no_event_passes() -> None:
    payload = _payload()
    item = payload["items"][0]
    item["passed"] = False
    item["catalysts"] = []
    item["retrieved_evidence"] = {
        "research": [{
            "evidence_id": "R1",
            "title": "先发布局灵巧手电机",
            "org": "华源证券",
            "date": "2026-06-01",
            "url": "https://example.com/r1.pdf",
        }],
        "news": [],
        "announcements": [],
    }
    answer = _build_catalyst_analysis_answer([{
        "tool": "analyze_stock_catalysts",
        "arguments": {"symbols": "603728"},
        "result": payload,
    }])

    assert answer is not None
    assert "尚缺明确时间窗的原始线索" in answer
    assert "先发布局灵巧手电机" in answer
    assert "不升级为已核验催化" in answer


def test_deterministic_catalyst_answer_preserves_formal_windows_when_model_fails() -> None:
    payload = _payload()
    item = payload["items"][0]
    item["passed"] = False
    item["verdict"] = "催化事件评估失败：模型暂时不可用"
    item["catalysts"] = []
    item["retrieved_evidence"] = {
        "formal_documents": [{
            "evidence_id": "D1",
            "title": "2025年年度报告",
            "time_window": "2026年下半年",
            "excerpt": "冷却系统核心部件预计于2026年下半年启动交付。",
            "url": "https://example.com/annual-report.pdf",
        }],
        "report_schedule": [],
    }

    answer = _build_catalyst_analysis_answer([{
        "tool": "analyze_stock_catalysts",
        "arguments": {"symbols": "603728"},
        "result": payload,
    }])

    assert answer is not None
    assert "已从正式报告正文核验到 1 项未来经营节点" in answer
    assert "不会将其误报为“没有催化”" in answer
    assert "2026年下半年启动交付" in answer
    assert "模型暂时不可用时先保留原文事实" in answer


class _Controller:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.tool_calls: list[str] = []
        self._stream_tasks: list = []
        self.assistant_text_snapshot = ""

    def append_text(self, value: str) -> None:
        self.texts.append(value)

    def append_reasoning(self, _value: str) -> None:
        return None

    async def add_tool_call(self, name: str, tool_call_id: str | None = None):
        del tool_call_id
        self.tool_calls.append(name)
        stream = MagicMock()
        stream.append_args_text = MagicMock()
        stream.set_response = MagicMock()
        return stream


def test_production_catalyst_request_calls_fixed_tool_then_semantic_synthesis() -> None:
    controller = _Controller()
    candidate = _task()
    resolved = [ResolvedTask(candidate=candidate, symbols=("603728",))]
    with patch.object(
        chat_mod, "resolve_task_plan", new=AsyncMock(return_value=chat_mod.TaskPlan(tasks=[candidate]))
    ), patch.object(
        chat_mod, "resolve_plan_entities", return_value=resolved
    ), patch.object(
        chat_mod,
        "execute_tool_isolated",
        return_value=_payload(),
    ), patch.object(
        chat_mod,
        "_stream_final_answer_without_tools",
        new=AsyncMock(return_value="鸣志电器的已核验催化窗口为 2027-Q1。"),
    ), patch.object(
        chat_mod, "_flush_substreams", new=AsyncMock()
    ):
        answer = asyncio.run(_run_standard_task_pipeline(
            controller,
            [{"role": "user", "content": "看下鸣志电器未来 6—12 个月的催化事件"}],
            {"model": "test", "api_base": ""},
        ))

    assert controller.tool_calls == ["analyze_stock_catalysts"]
    assert "2027-Q1" in answer
