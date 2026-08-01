# -*- coding: utf-8 -*-
"""Two-stage, schema-driven semantic planner for Agent Orchestrator V2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
import inspect
import json
import logging
import os
import re
import time
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Literal, Mapping
import uuid

from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    AgentStage,
    AgentStageEventV2,
    AssumptionRecord,
    GoalContractV2,
    InputReferenceV2,
    IntentOutlineNodeV2,
    IntentOutlineV2,
    Capability,
    OrchestratorV2Error,
    PlanningTraceV2,
    PlannerVerificationV2,
    QuestionType,
    RepairIssueV2,
    RepairRecordV2,
    ResourceType,
    SourceConstraintMode,
    SourceKind,
    StageObserver,
    StageStatus,
)
from src.agent.orchestrator_v2.registry import (
    capability_catalog,
    capability_for,
    normalize_capability_intent,
)
from src.agent.task_planner import current_user_request
from src.llm.anthropic_gateway import build_litellm_kwargs


V2_SCHEMA_VERSION = "orchestrator-4.0"
MODEL_PROGRESS_HEARTBEAT_SECONDS = 5.0
logger = logging.getLogger(__name__)



class RawProviderPayloadError(ValueError):
    def __init__(self, payload: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.payload = payload

class MissingProviderPayloadError(ValueError):
    """The provider returned neither the requested function nor usable JSON."""

class ExactContractValidationError(ValueError):
    """Program-owned cross-field rules generated from the same capability spec."""

    def __init__(self, issues: tuple[RepairIssueV2, ...]) -> None:
        super().__init__("; ".join(item.message for item in issues))
        self.issues = issues

@dataclass(frozen=True)
class _AvailableArtifact:
    artifact_id: str
    resource_type: ResourceType
    producer_node_id: str

@dataclass(frozen=True)
class PlannedIntentNodeV2:
    outline: IntentOutlineNodeV2
    intent: BaseModel
    execution_parameters: Mapping[str, Any]
    assumptions: tuple[AssumptionRecord, ...]

@dataclass(frozen=True)
class PlannedIntentGraphV2:
    run_id: str
    outline: IntentOutlineV2
    nodes: tuple[PlannedIntentNodeV2, ...]
    trace: PlanningTraceV2

_OUTLINE_SYSTEM_PROMPT = """\
你是 Agent Orchestrator V2 的目标与语义拆解器。先形成 Goal Contract，再选择能力图。
Goal Contract 是本轮唯一完成定义，必须直接来自 current_request：
1. objective 明确用户真正要解决的问题；
2. question_type 区分事实、解释、诊断、比较、研究、预测、决策和操作；
3. deliverables 是用户最终要看到的内容，不是内部执行步骤；
4. claims 拆成必须得到支持的用户可见结论，并为每条结论声明 required_dimensions；
5. 用户明确指定的数据源、RSSHub 路由或“只使用/不得使用”边界必须逐项写入
   source_requirements；用户要求看到或继续消费的结构化集合、Feed 条目、原始文本文件和证据
   必须写入 required_output_resources；这些约束不能被相邻数据源替代；
6. 预测问题必须使用 question_type=forecast 和 uncertainty_mode=scenario，输出候选情景、
   相对排序、成立条件与失效信号，不能把“无法确定未来”作为主要答案；
7. 操作问题使用 uncertainty_mode=not_applicable；其余问题按事实确定性选择 exact 或 bounded。
只允许使用 capability_catalog 已声明的 evidence_dimensions。能力图中全部能力的
evidence_dimensions 并集必须覆盖每条强制 claim 的 required_dimensions；
能力的 limitations 明确说明它不能证明什么，禁止用相邻数据冒充目标证据。
例如 market_overview 只能证明市场状态，不能证明未来市场主线；
未来市场主线必须选择 market_mainline_research。
你只表达用户目标、能力和资源关系。
禁止输出任何工具名、批次、并发、超时、重试、缓存、抓取深度、搜索条数、内部字段或默认值。
source=node 的 input_refs 只能引用本张图中的上游 node_id 和它真实产生的资源类型。
引用 conversation_context 中的跨轮终态资源时必须使用 source=artifact 和对应 artifact_id；
禁止把历史 producer_node_id 伪装成本张图节点。若用户目标不明确，返回最小澄清。
若 current_request 是对唯一可用终态文本文件或 RSS 条目的开放式继续分析，且没有提出新的来源、
对象或外部证据需求，不要扩展分析领域，也不要因缺少细分角度而重新搜索：使用 explanation、
一个基于 feed_content 的最小强制 claim，以及消费该历史 artifact 的单个
financial_article_read 节点；具体回答只受已绑定文件证据约束。
不要把数据源缺失改写成公开新闻或网络搜索任务。不要把一组联合财务条件拆成多个筛选节点。
显式 RSSHub 路由不能改写成 announcement_analysis、news_analysis 或其他数据能力。若用户要求
读取该路由的条目以及最新一条的全文或原文件，能力图必须是来源检查 → financial_feed_read →
financial_article_read；后两者通过 rss_source_collection 和 rss_item_collection 相连。
只保留完成当前用户明确请求所必需的最小能力图；不得添加为潜在追问准备的下游任务。
每个 objective 只能复述用户已经表达的目标，不得擅自增加细分领域、条件、动作或输出类型。
不得把某个能力的输出继续消费成用户没有明确要求的另一类结果。
最小能力图仍必须足以产生用户本轮要求的终态结论，不能只完成前置候选发现。
theme_stock_discovery 只建立结构化板块成分股候选全集；候选成员关系不等于公司业务匹配、
受益程度、投入强度或发展状态。若用户本轮要求任何公司级事实判断，必须继续选择能产生
该判断的终态能力。对领域候选逐家公司核验主题业务与发展强度时，theme_business_evidence
必须同时消费上游 theme_stock_discovery 产生的 domain_collection 和 security_collection；
不得把当前消息中偶然解析出的证券实体当成上游候选集合。
capability_catalog 中 subsumes_capabilities 非空的能力是自包含复合能力；选择它时，不得再添加
其中列出的并列能力节点，复合能力自己的固定 Workflow 会按顺序完成全部证据与判断。
result_selection 只能用于 capability_catalog 中 supports_result_selection=true 的能力，否则必须为 null。
只有用户明确要求唯一一个、明确数量或“全部相关”时才填写 result_selection；用户没有表达数量时必须
返回 null，由程序采用能力自己的默认值并记录假设。
"""

_INTENT_SYSTEM_PROMPT = """\
你是 Agent Orchestrator V2 的单能力参数化器。任务图与能力已经冻结。
只能按本次提供的唯一精确 Schema 填写用户明确表达的业务语义，不得增加任务、工具名或执行参数。
没有由用户表达的可选字段保持缺省；程序会负责默认值并记录假设。
主题、对象和研究主体字段只能填写用户明确说出的具体主体；分析目标留在 frozen_node.objective。
禁止把“最受益、比较、分析”等目标扩展成用户没有点名的细分行业、零部件、应用场景或结论。
财务指标必须区分资产负债率、营收、归母净利润和扣非净利润，金额保留用户表达的单位。
显式 RSSHub route_path 必须原样填写；“最新一条”在 financial_article_read 中填写
selection=latest，不能填标题、不能猜公司名称，也不能重新搜索条目。
financial_article_read 必须用 response_mode 明确区分“只交付资源”“回答文档问题”
“完整总结”和“继续分析”。引用历史文本文件时，artifact_id 只写在 input_refs，绝不能复制到
resource_id；需要从文本文件集合中选择 PDF 等格式时填写 document_mime_type。回答或分析文件内容时
必须填写 reading_mode；targeted 必须同时填写 query，完整总结必须使用 complete。
"""

_VERIFIER_SYSTEM_PROMPT = """\
你是独立的 Agent 计划验收器。只比较 current_request、Goal Contract 与 frozen_outline：
1. Goal 的问题类型、交付物、强制结论和不确定性模式必须完整且忠于当前请求；
2. 图中能力的证据维度并集必须覆盖 Goal 每条强制结论，不能只做前置发现或相邻分析；
3. 不得包含用户没有要求、也不为 Goal 证据覆盖所必需的能力；
4. 每条资源边必须让下游消费上游真实的结构化结果；
5. 复合能力已经包含的子能力不能重复出现；
6. 预测问题必须接受不确定性并要求情景、排序、触发条件和失效信号，不能要求确定性预言；
7. 没有资源边的单节点终态能力是合法图，不要求节点消费自己的输出；
8. Goal 中 source_requirements 与 required_output_resources 必须忠于用户明确限制；显式 RSSHub
   路由不得被公告、新闻、行情、财务或公开网页能力替代；
8. fallback_capabilities 只供执行失败后的程序化补证，不是初始图的必选节点；
9. 不评价工具、参数、实现方式或答案文风。
严格输出验收 Schema。没有问题时 accepted=true 且所有问题数组为空；
存在任何遗漏、越界或资源错误时 accepted=false，且至少把一项问题写入对应的
问题数组。若无法完成验收，则 accepted=false、confidence=0 且问题数组为空，
表示主动弃权，不得用占位文字否决计划。不得替计划辩护。\
"""

__all__ = [
    "ExactContractValidationError",
    "PlannedIntentGraphV2",
    "PlannedIntentNodeV2",
    "call_model_exact_v2",
    "plan_intent_graph_v2",
]


from . import _planner_functions1 as _planner_functions1
from . import _planner_functions2 as _planner_functions2
from . import _planner_functions3 as _planner_functions3
from . import _planner_functions4 as _planner_functions4


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_planner_functions1, _planner_functions2, _planner_functions3, _planner_functions4):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
