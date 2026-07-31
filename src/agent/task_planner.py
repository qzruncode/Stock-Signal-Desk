# -*- coding: utf-8 -*-
"""Program-owned resource binding and verified security resolution.

Natural-language planning lives only in ``orchestrator_v2.planner``.  This
module contains no task-planning schema, retry loop or plan cache.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Iterable, Mapping

from pydantic import ValidationError

from src.agent.result_contracts import (
    CollectionFinancialFilterSpec,
    DomainBoardQuerySpec,
    InvestmentThesisContext,
    ThemeEvidenceContext,
)
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResultSelectionMode,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    TaskResource,
    parameter_requirement_issues,
    workflow_for,
)
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.services.stock_screening.screen_spec import QuantitativeScreenSpec
from src.tools.symbols import resolve_securities_csv


logger = logging.getLogger(__name__)


class SemanticResourceBindingUnavailableError(RuntimeError):
    """The live catalog or its semantic binding could not be completed."""


class TaskPlanValidationError(ValueError):
    """A frozen typed graph cannot be bound to executable resources."""


_DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "minLength": 1, "maxLength": 64},
        "board_queries": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "mapping_type": {
            "type": "string",
            "enum": ["catalog_binding", "unresolved"],
        },
        "rationale": {"type": "string", "maxLength": 240},
        "unresolved_parts": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
    },
    "required": [
        "label",
        "board_queries",
        "mapping_type",
        "rationale",
        "unresolved_parts",
    ],
}

_RESOURCE_BINDING_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_semantic_resource_bindings",
        "description": "Bind semantic task parameters to values from a supplied live resource catalog.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "bindings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "task_id": {"type": "string"},
                            "domains": {
                                "type": "array",
                                "items": _DOMAIN_SCHEMA,
                                "minItems": 1,
                                "maxItems": 12,
                            },
                        },
                        "required": ["task_id", "domains"],
                    },
                },
            },
            "required": ["bindings"],
        },
    },
}


_RESOURCE_BINDING_SYSTEM_PROMPT = """\
你是 Semantic Resource Binder。标准任务已经确定，你只能把其中的语义产业领域绑定到本次提供的实时
板块目录，不能增加、删除或改写任务，也不能选择数据 Tool。

用户写的领域名可能口语化、不完整或并非正式板块名。你需要理解其产业语义，只能从 catalog 逐字选择
项目中真实存在、最能承接该查询的板块名称，并标为 catalog_binding。允许选择严格同义板块、对应的
标准产品板块，或能够形成合理候选池的最近上位板块；按匹配度排序，最多四个。不得生成 catalog 之外
的别名，不得选择仅因文字相似但产业含义无关的板块，也不得把板块成员关系说成主营、订单或收入证明。
只有在目录中没有任何合理可执行的相关板块时才用 unresolved，且 board_queries 为空。保留原始 label，
在 rationale 中明确是精确对应还是近似候选映射及其边界；复合领域未覆盖部分放入 unresolved_parts。
每个输入 task_id 和每个输入领域都必须且只能返回一次。
"""



__all__ = [
    "SemanticResourceBindingUnavailableError",
    "TaskPlanValidationError",
    "current_user_request",
    "bind_task_plan_resources",
    "resolve_plan_entities",
    "validate_candidate_plan",
]


from . import _task_planner_functions1 as _task_planner_functions1
from . import _task_planner_functions2 as _task_planner_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_task_planner_functions1, _task_planner_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
