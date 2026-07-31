# -*- coding: utf-8 -*-
"""Finite-catalog industry selection with exact V2 model contracts.

The model performs two bounded semantic operations:
1. decompose the user's topic into value-chain benefit roles;
2. bind those roles to IDs from the complete live board catalog.

Catalog identity, cardinality, membership, resource publication and rendering
are program-owned.  No retrieval index, keyword route or partial catalog is
used by this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel

from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    OrchestratorV2Error,
    RepairIssueV2,
)
from src.agent.orchestrator_v2.planner import (
    ExactContractValidationError,
    call_model_exact_v2,
)
from src.agent.result_contracts import (
    DomainBoardBindingV2,
    DomainCatalogSelectionV2,
    DomainCollectionCoverageV2,
    DomainCollectionV2,
    DomainResultSelectionV2,
    DomainSelectionAssumptionV2,
    IndustryBenefitOutlineV2,
)
from src.agent.task_workflows import (
    ResolvedTask,
    ResultSelectionMode,
    ResultSelectionSpec,
)
from src.services.domain_board_catalog import domain_board_catalog_snapshot_id


Completion = Callable[..., Awaitable[Any]]
ProcessorProgress = Callable[[int, int, dict[str, Any]], Awaitable[None]]

logger = logging.getLogger(__name__)

INDUSTRY_CATALOG_SELECTION_VERSION = "v10"


_BENEFIT_OUTLINE_PROMPT = """\
你是产业受益链拆解器。只根据用户明确提出的产业主题，形成可用于实时板块目录映射的紧凑产业链角色。

规则：
1. topic 保留用户主题；selection_objective 只复述本轮受益领域选择目标。
2. roles 描述具体产品、部件、软件、服务或基础设施环节，不得输出股票、公司或板块代码。
3. role_id 使用稳定、简短的小写英文标识；label 使用中文业务名称。
4. benefit_mechanism 只说明该环节如何受益，不得编造订单、收入、客户、市场份额或当前行情。
5. tier=1 表示受益最直接，tier=2 至 tier=4 依次降低。
6. 不得输出工具名、调用顺序、批次、超时、重试、缓存或数据源字段。
7. 必须按 submit_industry_benefit_outline_v2 的精确 Schema 返回唯一结构化结果。\
"""


_CATALOG_SELECTION_PROMPT = """\
你是有限实时板块目录选择器。输入 project_boards 是本轮完整目录，你只能返回其中真实存在的 board_id。

规则：
1. 必须在全部 project_boards 中完成语义比较，不得创建、改写或猜测 board_id。
2. 每个 item 只包含 board_id、role_id、tier；禁止返回板块名称、理由、置信度或额外字段。
3. role_id 必须来自 benefit_outline.roles；tier 必须与所引用 role 的 tier 完全一致。
4. 优先选择能直接表达具体受益环节的板块；名称相似但产业含义不相关的板块不得选择。
5. best_one 必须返回唯一一个；top_k 最多返回 max_items 个但不要求凑满；all_relevant 返回全部判断为相关的板块。
6. 若目录中没有直接表达某个 role 的板块，必须跳过该 role；不得用宽泛主题、政策概念或上下位领域替代具体受益环节。
7. 同一个 board_id 只能出现一次。若多个 role 只能映射到同一个 board_id，只保留 tier
   数字最小的 role；tier 相同时保留 benefit_outline.roles 中顺序最靠前的 role。
   不得因无法确定而返回目录之外的替代项。
8. 必须按 submit_domain_catalog_selection_v2 的精确 Schema 返回唯一结构化结果。\
"""



__all__ = [
    "INDUSTRY_CATALOG_SELECTION_VERSION",
    "rank_project_board_domains_v2",
]


from . import _industry_catalog_selection_functions1 as _industry_catalog_selection_functions1
from . import _industry_catalog_selection_functions2 as _industry_catalog_selection_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_industry_catalog_selection_functions1, _industry_catalog_selection_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
