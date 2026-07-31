from __future__ import annotations

import asyncio
from contextlib import contextmanager
import inspect
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock, patch

import pytest

from src.agent.task_executor import (
    ConfirmationRequired,
    WorkflowExecutor,
    WorkflowPolicyValidator,
    action_fingerprint,
)
from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    Capability,
    ClaimRequirementV2,
    GoalContractV2,
    OrchestratorV2Error,
    QuestionType,
    UncertaintyMode,
)
from src.agent.orchestrator_v2.registry import capability_catalog, capability_for
from src.agent.orchestrator_v2.runtime import (
    CompiledIntentGraphV2,
    CompiledTaskV2,
)
from src.agent.task_planner import (
    resolve_plan_entities,
    validate_candidate_plan,
)
import src.agent.task_planner as task_planner_module
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResultSelectionMode,
    ResultSelectionSpec,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    WORKFLOW_REGISTRY,
    compile_task,
    registered_workflow_tools,
)
from src.tools.registry import ToolRegistry



"""Focused test slice 4; shared fixtures remain local to this slice."""

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
        self.tool_calls.append(name)
        tool = MagicMock()
        tool.append_args_text = MagicMock()
        tool.set_response = MagicMock()
        return tool

@contextmanager
def _unified_pipeline(
    chat_mod,
    plan: TaskPlan,
    resolved: list[ResolvedTask],
):
    dimensions = tuple(
        sorted(
            {
                dimension
                for task in resolved
                for dimension in capability_for(Capability(task.kind.value)).evidence_dimensions
            },
            key=lambda item: item.value,
        )
    )
    goal = GoalContractV2(
        objective="完成测试计划",
        question_type=QuestionType.RESEARCH,
        uncertainty_mode=UncertaintyMode.BOUNDED,
        deliverables=("返回测试计划结果",),
        claims=(
            ClaimRequirementV2(
                claim_id="result",
                question="测试计划是否形成结果",
                required_dimensions=dimensions,
            ),
        ),
    )
    compiled = CompiledIntentGraphV2(
        run_id="test-run",
        plan=plan,
        tasks=tuple(
            CompiledTaskV2(
                task=task,
                capability=Capability(task.kind.value),
                capability_version=capability_for(Capability(task.kind.value)).version,
                intent_schema_version=capability_for(Capability(task.kind.value)).schema_version,
                execution_policy=capability_for(Capability(task.kind.value)).execution_policy,
                resource_fingerprint=f"test-{task.task_id}",
            )
            for task in resolved
        ),
        assumptions=(),
    )
    graph = MagicMock()
    graph.run_id = "test-run"
    graph.outline.goal = goal
    graph.trace.schema_version = "orchestrator-4.0"
    graph.trace.stage_durations_ms = {}
    with (
        patch.object(
            chat_mod,
            "plan_intent_graph_v2",
            new=AsyncMock(return_value=graph),
        ),
        patch.object(
            chat_mod,
            "compile_intent_graph_v2",
            new=AsyncMock(return_value=compiled),
        ),
    ):
        yield

def _task(
    kind: StandardTaskKind,
    *,
    task_id: str = "task_a",
    entities: list[str] | None = None,
    parameters: dict | None = None,
    depends_on: list[str] | None = None,
    confirmation: ConfirmationState = ConfirmationState.NOT_REQUIRED,
    result_selection: ResultSelectionSpec | None = None,
) -> StandardTask:
    normalized_parameters = dict(parameters or {})
    if kind == StandardTaskKind.THEME_BUSINESS_EVIDENCE:
        normalized_parameters.setdefault(
            "evidence_context",
            {
                "target_topics": ["人形机器人"],
                "domain_theses": [
                    {
                        "label": str(domain.get("label") or ""),
                        "rationale": "该板块是人形机器人产业链的直接受益环节",
                    }
                    for domain in normalized_parameters.get("domains") or []
                    if isinstance(domain, dict) and domain.get("label")
                ],
            },
        )
    return StandardTask(
        task_id=task_id,
        kind=kind,
        objective=kind.value,
        entity_scope=EntityScope.NONE,
        entities=entities or [],
        parameters=normalized_parameters,
        depends_on=depends_on or [],
        result_selection=(
            result_selection
            or ResultSelectionSpec(
                mode=ResultSelectionMode.ALL_RELEVANT,
                max_items=None,
            )
            if kind == StandardTaskKind.INDUSTRY_RESEARCH
            else None
        ),
        output_requirements=[],
        confirmation=confirmation,
        confidence=0.95,
    )

def _domain(
    label: str,
    *boards: str,
    mapping_type: str | None = None,
    rationale: str = "测试中的板块目录解析结果",
    unresolved_parts: list[str] | None = None,
) -> dict:
    unresolved = mapping_type == "unresolved"
    selected = list(boards) or ([] if unresolved else [label])
    return {
        "label": label,
        "board_queries": selected,
        "mapping_type": mapping_type or "catalog_binding",
        "rationale": rationale,
        "unresolved_parts": unresolved_parts or ([label] if unresolved else []),
    }

def _financial_conditions(*conditions: dict) -> dict:
    return {"conditions": list(conditions)}

def _model_response(function_name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=function_name,
                                arguments=json.dumps(payload, ensure_ascii=False),
                            )
                        )
                    ],
                    content=None,
                )
            )
        ]
    )
def test_executor_discovers_board_candidates_before_business_evidence() -> None:
    industry = _task(
        StandardTaskKind.INDUSTRY_RESEARCH,
        task_id="industry",
        parameters={
            "query": "人形机器人哪些领域最受益",
            "domains": [_domain("人形机器人")],
        },
    )
    domain_candidates = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        task_id="domain_candidates",
        parameters={},
        depends_on=["industry"],
    )
    companies = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        task_id="companies",
        parameters={
            "candidate_scope": "candidate_collection",
            "query": "哪些公司正在大力发展",
        },
        depends_on=["domain_candidates"],
    )
    plan = TaskPlan(tasks=[industry, domain_candidates, companies])
    validate_candidate_plan(plan)
    tool_names: list[str] = []
    processor_tasks: list[tuple[str, list[dict], tuple[str, ...]]] = []

    async def runner(call, _arguments):
        tool_names.append(call.tool_name)
        if call.tool_name == "get_domain_stock_candidates":
            return {
                "success": True,
                "errors": [],
                "partial": False,
                "items": [
                    {"symbol": "002979", "name": "雷赛智能"},
                    {"symbol": "300007", "name": "汉威科技"},
                ],
            }
        return {"success": True, "errors": [], "partial": False, "items": []}

    async def processor(name, task, _evidence):
        processor_tasks.append(
            (
                name,
                list(task.parameters.get("domains") or []),
                task.symbols,
            )
        )
        if name == "ranked_domain_selection":
            return {
                "success": True,
                "errors": [],
                "semantic_artifacts": [
                    {
                        "type": "ranked_domains",
                        "groups": [
                            {
                                "tier": 1,
                                "domains": [
                                    {"label": "灵巧手", "tier": 1},
                                    {"label": "六维力传感器", "tier": 1},
                                ],
                            }
                        ],
                    }
                ],
                "resource_outputs": {
                    "domain_collection": [
                        _domain("灵巧手", "机器人执行器"),
                        _domain("六维力传感器", "传感器"),
                    ],
                },
            }
        return {
            "success": True,
            "errors": [],
            "semantic_artifacts": [],
            "resource_outputs": {
                "security_collection": [
                    {"symbol": "002979", "name": "雷赛智能"},
                    {"symbol": "300007", "name": "汉威科技"},
                ],
            },
        }

    result = asyncio.run(
        WorkflowExecutor(
            ToolRegistry(),
            runner,
            processor_runner=processor,
        ).execute(
            [
                ResolvedTask(candidate=industry),
                ResolvedTask(candidate=domain_candidates),
                ResolvedTask(
                    candidate=companies,
                    # Simulates the incidental name resolution that turns the phrase
                    # “人形机器人” into the listed company 300024. The explicit
                    # dependency collection must remain authoritative.
                    symbols=("300024",),
                    entity_names=(("300024", "机器人"),),
                ),
            ]
        )
    )

    assert result.success is True
    assert tool_names.index("get_domain_stock_candidates") < tool_names.index("get_company_theme_evidence")
    assert tool_names.count("get_domain_board_catalog") == 1
    assert tool_names.count("get_domain_stock_candidates") == 1
    assert tool_names.count("get_company_theme_evidence") == 2
    assert tool_names.count("search_financial_news") == 0
    assert tool_names.count("search_research_library") == 0
    assert processor_tasks[1] == (
        "company_evidence_binding",
        [
            _domain("灵巧手", "机器人执行器"),
            _domain("六维力传感器", "传感器"),
        ],
        ("002979", "300007"),
    )
    assert result.tasks[1].resource_outputs["domain_collection"] == [
        _domain("灵巧手", "机器人执行器"),
        _domain("六维力传感器", "传感器"),
    ]
    assert [entity.symbol for entity in result.tasks[2].output_entities] == [
        "002979",
        "300007",
    ]

def test_failed_domain_collection_v2_blocks_every_downstream_data_tool() -> None:
    industry = _task(
        StandardTaskKind.INDUSTRY_RESEARCH,
        task_id="industry",
        parameters={
            "query": "人形机器人哪些领域最受益",
            "domains": [_domain("人形机器人")],
        },
    )
    domain_candidates = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        task_id="domain_candidates",
        parameters={},
        depends_on=["industry"],
    )
    companies = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        task_id="companies",
        parameters={"candidate_scope": "candidate_collection"},
        depends_on=["domain_candidates"],
    )
    tool_names: list[str] = []

    async def runner(call, _arguments):
        tool_names.append(call.tool_name)
        return {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "boards": [{"sector_code": "BK0566", "name": "减速器"}],
        }

    async def processor(name, _task, _evidence):
        assert name == "ranked_domain_selection"
        return {
            "success": False,
            "partial": False,
            "error_code": "planner_schema_invalid",
            "errors": ["板块 ID 绑定未通过精确 Schema"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {},
            "coverage": {
                "catalog_total": 1,
                "catalog_supplied": 1,
                "selected_count": 0,
                "binding_complete": False,
            },
        }

    result = asyncio.run(
        WorkflowExecutor(
            ToolRegistry(),
            runner,
            processor_runner=processor,
        ).execute(
            [
                ResolvedTask(candidate=industry),
                ResolvedTask(candidate=domain_candidates),
                ResolvedTask(candidate=companies),
            ]
        )
    )

    assert tool_names == ["get_domain_board_catalog"]
    assert [task.status for task in result.tasks] == [
        "failed",
        "skipped",
        "blocked",
    ]
    assert result.tasks[0].resource_outputs == {}

def test_business_evidence_preserves_one_terminal_result_per_company() -> None:
    companies = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        task_id="companies",
        parameters={
            "domains": [{"label": "减速器"}],
            "candidate_scope": "candidate_collection",
            "query": "核验业务进展",
        },
    )

    async def runner(_call, arguments):
        return {
            "success": True,
            "errors": ["个股研报源超时"],
            "partial": True,
            "symbol": arguments["symbol"],
            "evidence_documents": [],
        }

    async def processor(_name, _task, _evidence):
        return {
            "success": True,
            "partial": True,
            "errors": [],
            "resource_outputs": {
                "security_collection": [
                    {"symbol": "301368", "name": "丰立智能"},
                ],
            },
        }

    result = asyncio.run(
        WorkflowExecutor(
            ToolRegistry(),
            runner,
            processor_runner=processor,
        ).execute(
            [
                ResolvedTask(candidate=companies, symbols=("301368",)),
            ]
        )
    )

    assert result.success is True
    assert result.tasks[0].status == "completed"
    assert len(result.tasks[0].calls) == 1
    assert result.tasks[0].calls[0].arguments["symbol"] == "301368"
    assert result.tasks[0].calls[0].success is True
    assert [entity.symbol for entity in result.final_entities] == ["301368"]

def test_task_plan_rejects_dependency_cycles_before_execution() -> None:
    first = _task(
        StandardTaskKind.REALTIME_QUOTE,
        task_id="first",
        depends_on=["second"],
    )
    second = _task(
        StandardTaskKind.TECHNICAL_ANALYSIS,
        task_id="second",
        depends_on=["first"],
    )
    with pytest.raises(ValueError, match="cycle"):
        TaskPlan(tasks=[first, second])
