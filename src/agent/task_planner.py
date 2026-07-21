# -*- coding: utf-8 -*-
"""Semantic task decomposition without exposing data tools to the model."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable

from pydantic import ValidationError

from src.agent.result_contracts import CollectionFinancialFilterSpec
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    parameter_requirement_issues,
    planner_task_catalog,
    workflow_for,
)
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.services.stock_screening.screen_spec import quantitative_screen_spec_schema
from src.tools.symbols import find_securities_in_text


logger = logging.getLogger(__name__)

PLANNER_PRIMARY_TIMEOUT_SECONDS = 40.0
PLANNER_RECOVERY_TIMEOUT_SECONDS = 32.0
PLANNER_CACHE_TTL = timedelta(days=7)
PLANNER_CACHE_VERSION = "v9"


class TaskPlannerUnavailableError(RuntimeError):
    """The semantic planner timed out on both bounded attempts."""


class TaskPlanValidationError(ValueError):
    """The semantic planner returned a structurally unsafe candidate plan."""


_TASK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,31}$"},
        "kind": {"type": "string", "enum": [kind.value for kind in StandardTaskKind]},
        "objective": {"type": "string"},
        "entity_scope": {"type": "string", "enum": [scope.value for scope in EntityScope]},
        "entities": {"type": "array", "items": {"type": "string"}},
        "parameters": {"type": "object", "additionalProperties": True},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "output_requirements": {"type": "array", "items": {"type": "string"}},
        "confirmation": {
            "type": "string",
            "enum": [state.value for state in ConfirmationState],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "task_id",
        "kind",
        "objective",
        "entity_scope",
        "entities",
        "parameters",
        "depends_on",
        "output_requirements",
        "confirmation",
        "confidence",
    ],
}

_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_standard_task_plan",
        "description": "Submit a semantic decomposition into standard tasks. Do not select data tools.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tasks": {"type": "array", "items": _TASK_SCHEMA, "maxItems": 12},
                "needs_clarification": {"type": "boolean"},
                "clarification_question": {"type": ["string", "null"]},
            },
            "required": ["tasks", "needs_clarification", "clarification_question"],
        },
    },
}


_PLANNER_SYSTEM_PROMPT = """\
你是 Task Decomposer，只负责理解 current_request 并拆成标准子任务。你看不到也不能选择任何数据 Tool，
不能设计执行步骤，不能回答用户问题。程序会根据 kind 绑定不可修改的 Workflow。

执行规则：
1. current_request 是本轮唯一可执行目标；previous_assistant_outline 只用于解析其中的“上面、这些、继续、
   第一梯队”等指代，绝不能把上一轮已经完成的用户要求重新创建为本轮任务。只有纯指代时才继承紧邻
   上一回答的对象和范围。
2. 组合问题必须拆成最小但完整的标准任务；互不依赖的任务不声明依赖，只有后续任务确实需要前一
   任务输出时才写 depends_on。不要创建重复任务。
3. 股票实体放 entities，并用 entity_scope 声明范围来源。previous_answer 只表示紧邻上一回答明确列出
   的完整公司集合，不能扩展到更早内容。
4. 产业研究与按领域找股是两个任务：研究环节用 industry_research；从内部股票池按领域列候选用
   theme_stock_discovery，并把上一回答中被引用的全部具体产品领域解析到 parameters.domains。
   只有用户明确要求输出上市公司、股票、证券名单或候选时才能创建 theme_stock_discovery；要求分析
   产业链、受益环节、梯队或明确零部件领域本身不等于找股票，不能额外创建找股任务。
   parameters.context_theme 必须是最短的标准 A 股上位概念板块名，例如“人形机器人”，不能写成
   “人形机器人上游核心零部件”或整句研究主题。
   只有用户明确把订单、收入、量产、客户验证等公司级事实设为纳入或剔除条件时，才使用
   theme_business_evidence。普通的“相关、布局、受益、核心、大力发展”仍是 theme_stock_discovery。
5. 上一回答公司集合内按财务阈值筛选使用 collection_financial_filter，entity_scope 必须是
   previous_answer。parameters 必须包含：
   - metric：debt_ratio（资产负债率）、revenue（营业收入）或 deducted_net_profit（扣非净利润）；
   - period_basis：latest_report、ttm、previous_fiscal_year（去年完整年报）或 fiscal_year；
   - operator、threshold、threshold_unit、action；
   - threshold_unit：比率用 percent；金额按用户原单位使用 cny、wan_cny 或 yi_cny，不能自行换算 threshold；
   - 用户说“去年/上年”的年度财务指标必须用 previous_fiscal_year，不能替换成 ttm 或最新季度累计值；
   - 只有用户明确指定某个年度才用 fiscal_year，并同时提供 fiscal_year 整数。
6. 全市场公式筛选使用 stock_screening，parameters.screen_spec 必须是完整可执行规格；缺少必要条件或
   含不支持条件时请求澄清，不能静默补默认值或删条件。
7. general_response 只用于不需要实时或外部数据的普通回答。涉及当前行情、财务、新闻、市场或外部
   事实时选择对应的数据任务。
8. 用户追问“这些/上面股票中哪些现在能买入、可介入、值得买”时，必须创建 investment_decision，
   entity_scope=previous_answer，并覆盖紧邻上一回答列出的完整公司集合；不要改成 stock_comparison、
   stock_deep_research、public_web_research 或多个零散数据任务。parameters.thesis 应尽量用上一回答中被
   指代的产业方向概括真实受益逻辑，不得虚构。该任务的程序工作流会固定执行九项门槛并自动分批。
9. confirmation 只描述用户是否已经在当前对话中明确确认具体高影响动作。不能把“建议、可以、看看”
   推断成确认。无需二次确认填 not_required；需要但尚未确认填 missing；明确确认才填 explicit。
10. trade_execution 只能表示交易请求，不能改成投资分析。它会进入独立安全状态机；当前不可执行时由
   程序说明，Planner 不得绕过。
11. 只通过 submit_standard_task_plan 返回完整结构化结果。\
"""


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return ""


def current_user_request(
    messages: list[dict[str, Any]],
    *,
    recovery: bool = False,
) -> str:
    """Return only the latest user goal; historical goals never reach Planner."""
    text = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _message_text(message)
            if text:
                break
    if not text:
        return ""
    limit = 6000 if recovery else 12000
    if len(text) <= limit:
        return text
    head = max(1000, limit - 1200)
    return text[:head] + "\n...[当前问题中段省略]...\n" + text[-1000:]


def _previous_investment_thesis(
    messages: list[dict[str, Any]],
    target_entities: list[dict[str, str]] | None = None,
) -> str:
    """Recover the latest relevant domain table, even across an intervening turn."""
    target_tokens: set[str] = set()
    for entity in target_entities or []:
        for key in ("name", "symbol"):
            value = re.sub(r"\s+", "", str(entity.get(key) or "")).lower()
            if value:
                target_tokens.add(value)

    def _is_domain_header(value: str) -> bool:
        compact = re.sub(r"[\s*_`/／]", "", value)
        exact = {
            "领域", "方向", "产业方向", "细分方向", "匹配领域", "受益领域",
            "产业环节", "核心环节", "所属主线", "主线分支", "受益方向",
        }
        return compact in exact or any(
            phrase in compact
            for phrase in ("匹配领域", "受益领域", "产业方向", "产业环节", "核心环节", "主线分支")
        )

    for message in reversed(messages[:-1]):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        previous = _message_text(message)
        if not previous:
            continue
        lines = previous.splitlines()
        for index, raw_line in enumerate(lines):
            if not raw_line.strip().startswith("|"):
                continue
            cells = [cell.strip() for cell in raw_line.strip().strip("|").split("|")]
            selected_columns = [
                column_index for column_index, cell in enumerate(cells) if _is_domain_header(cell)
            ]
            if not selected_columns:
                continue
            values: list[str] = []
            matched_target = not target_tokens
            for row in lines[index + 2:]:
                if not row.strip().startswith("|"):
                    break
                row_cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                normalized_row = re.sub(r"\s+", "", " ".join(row_cells)).lower()
                row_matches_target = not target_tokens or any(
                    token in normalized_row for token in target_tokens
                )
                if not row_matches_target:
                    continue
                matched_target = True
                for column_index in selected_columns:
                    if column_index >= len(row_cells):
                        continue
                    value = re.sub(r"[*_`]", "", row_cells[column_index]).strip()
                    if value and value not in values and not re.fullmatch(r"[:\- ]+", value):
                        values.append(value)
                if len(values) >= 12:
                    break
            if matched_target and values:
                return "；".join(values)[:400]
    return ""


def _labeled_current_investment_thesis(request: str) -> str:
    """Read only an explicitly labelled thesis; never guess from prose."""
    match = re.search(
        r"(?:投资逻辑|产业方向|受益方向|细分领域)\s*[：:]\s*([^。！？!?\n]{2,400})",
        str(request or ""),
    )
    if not match:
        return ""
    value = re.sub(r"[*_`]", "", match.group(1)).strip(" ，,；;。")
    return value[:400]


def _explicit_collection_buy_plan(
    messages: list[dict[str, Any]],
    current_entities: list[dict[str, str]],
    previous_entities: list[dict[str, str]],
) -> TaskPlan | None:
    """Guarantee the exact follow-up contract before semantic model planning.

    This selects a standard task, not a data tool. The immutable workflow still
    owns batching, tool access and all nine decision gates.
    """
    request = current_user_request(messages)
    if not request or not (current_entities or previous_entities):
        return None
    if re.search(r"下单|委托|替我买|帮我买|立即买入|买(?:入)?\s*\d+\s*(?:股|手)", request):
        return None
    has_reference = bool(re.search(r"这些|上面|上述|其中|这几只|这几家|它们|上文", request))
    has_buy_decision = bool(re.search(
        r"(?:哪些|哪只|哪几只|是否|能否|能不能|可以|可|值得|适合|现在|当前)"
        r"[^。？！\n]{0,16}(?:买入|买|介入|布局)|"
        r"(?:买入|介入)[^。？！\n]{0,12}(?:哪些|哪只|吗|么)",
        request,
    ))
    if not has_buy_decision:
        return None
    if has_reference and previous_entities:
        entity_scope = EntityScope.PREVIOUS_ANSWER
    elif current_entities:
        entity_scope = EntityScope.CURRENT_MESSAGE
    else:
        return None
    thesis = _previous_investment_thesis(messages, previous_entities)
    if not thesis and current_entities:
        thesis = _labeled_current_investment_thesis(request)
    task = StandardTask(
        task_id="strict_buy_follow_up",
        kind=StandardTaskKind.INVESTMENT_DECISION,
        objective=request[:400],
        entity_scope=entity_scope,
        entities=[],
        parameters={"thesis": thesis},
        depends_on=[],
        output_requirements=["逐股首项否决", "九项全通过才可买入", "仓位与失效条件"],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=1.0,
    )
    return TaskPlan(tasks=[task], source="deterministic_follow_up_contract")


def _explicit_catalyst_research_plan(
    messages: list[dict[str, Any]],
    current_entities: list[dict[str, str]],
    previous_entities: list[dict[str, str]],
) -> TaskPlan | None:
    """Route an explicit company catalyst request without a planning-model call.

    This is an exact task contract, not a keyword-based investment judgement:
    the request must name the research dimension (catalyst), a forward window,
    and a locally verified company scope.  Event interpretation remains inside
    the evidence-bound catalyst evaluator.
    """
    request = current_user_request(messages)
    if not request or "催化" not in request:
        return None
    has_forward_window = bool(
        re.search(r"(?:未来|今后|后续|接下来)", request)
        or re.search(r"6\s*[—–-]\s*12\s*个?月", request)
        or re.search(r"半年\s*(?:到|至|—|–|-)\s*一年", request)
    )
    if not has_forward_window:
        return None
    has_reference = bool(re.search(r"这些|上面|上述|其中|这几只|这几家|它们|上文", request))
    if current_entities:
        entity_scope = EntityScope.CURRENT_MESSAGE
    elif has_reference and previous_entities:
        entity_scope = EntityScope.PREVIOUS_ANSWER
    else:
        return None
    task = StandardTask(
        task_id="future_catalysts",
        kind=StandardTaskKind.CATALYST_ANALYSIS,
        objective=request[:400],
        entity_scope=entity_scope,
        entities=[],
        parameters={},
        depends_on=[],
        output_requirements=["逐项列出明确时间窗", "绑定可回查来源", "区分确认事件与待验证线索"],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=1.0,
    )
    return TaskPlan(tasks=[task], source="deterministic_explicit_research_contract")


def previous_assistant_outline(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = 7000,
) -> str:
    """Preserve referential scope from the immediately previous answer.

    Long research answers are often structured as headings, bullets and tables.
    A naive head/tail truncation can remove exactly the middle table referenced
    by a follow-up such as “上面第一梯队的领域”.  This generic Markdown outline
    keeps that structure without introducing domain keywords or task routing.
    """
    previous = ""
    for message in reversed(messages[:-1]):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        previous = _message_text(message)
        if previous:
            break
    if not previous:
        return ""
    if len(previous) <= max_chars:
        return previous

    structural: list[str] = []
    for raw_line in previous.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if (
            line.startswith(("#", "|", "- ", "* ", "> "))
            or re.match(r"^\d+[.)、]\s*", line)
            or "**" in line
        ):
            structural.append(line)
    outline = "\n".join(structural)
    head = previous[:1400]
    tail = previous[-700:]
    combined = f"[开头]\n{head}\n[结构化提纲]\n{outline}\n[结尾]\n{tail}"
    return combined[:max_chars]


def _cache_key(
    messages: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    current_entities: list[dict[str, str]],
    previous_answer_entities: list[dict[str, str]],
) -> str | None:
    if not llm_cfg.get("api_base"):
        return None
    payload = {
        "version": PLANNER_CACHE_VERSION,
        "model": str(llm_cfg.get("model") or ""),
        "current_request": current_user_request(messages, recovery=True),
        "previous_answer_reference_outline": previous_assistant_outline(messages),
        "current_entities": current_entities,
        "previous_answer_entities": previous_answer_entities,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"standard_task_plan:{PLANNER_CACHE_VERSION}:{digest}"


def _load_cached_plan(cache_key: str | None) -> TaskPlan | None:
    if not cache_key:
        return None
    try:
        from src.storage.manager import DatabaseManager

        cached = DatabaseManager.get_instance().get_tool_cache(cache_key)
        if not cached:
            return None
        updated_at = cached.get("updated_at")
        if not isinstance(updated_at, datetime) or datetime.now() - updated_at > PLANNER_CACHE_TTL:
            return None
        payload = json.loads(bytes(cached["payload"]).decode("utf-8"))
        plan = TaskPlan.model_validate(payload)
        validate_candidate_plan(plan)
        return plan.model_copy(update={"source": "semantic_cache"})
    except Exception:
        logger.warning("[TaskPlanner] ignored invalid persistent plan cache", exc_info=True)
        return None


def _save_cached_plan(cache_key: str | None, plan: TaskPlan) -> None:
    if not cache_key:
        return
    try:
        from src.storage.manager import DatabaseManager

        payload = plan.model_copy(update={"source": "semantic"}).model_dump_json().encode("utf-8")
        DatabaseManager.get_instance().save_tool_cache(cache_key, payload)
    except Exception:
        logger.warning("[TaskPlanner] failed to persist plan cache", exc_info=True)


def _json_object(value: str) -> dict[str, Any]:
    stripped = re.sub(r"^```(?:json)?\s*", "", value.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("planner response is not an object")
    return parsed


def _payload_from_response(response: Any) -> dict[str, Any]:
    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    for choice in field(response, "choices") or []:
        message = field(choice, "message")
        if message is None:
            continue
        for tool_call in field(message, "tool_calls") or []:
            function = field(tool_call, "function")
            if field(function, "name") != "submit_standard_task_plan":
                continue
            arguments = field(function, "arguments")
            if isinstance(arguments, dict):
                return arguments
            if arguments:
                return _json_object(str(arguments))
        content = field(message, "content")
        if isinstance(content, str) and content.strip():
            return _json_object(content)
    raise ValueError("planner returned no structured payload")


def validate_candidate_plan(plan: TaskPlan) -> None:
    """Validate semantic parameters before any entity resolution or tool call."""
    issues: list[str] = []
    for task in plan.tasks:
        spec = workflow_for(task.kind)
        unknown = set(task.parameters) - spec.allowed_parameters
        missing = spec.required_parameters - set(task.parameters)
        if unknown:
            issues.append(f"{task.task_id}: unsupported parameters {sorted(unknown)}")
        if missing:
            issues.append(f"{task.task_id}: missing parameters {sorted(missing)}")
        for parameter, allowed_values in spec.parameter_enums.items():
            if parameter not in task.parameters:
                continue
            if task.parameters[parameter] not in allowed_values:
                issues.append(
                    f"{task.task_id}: {parameter} must be one of {sorted(allowed_values)}"
                )
        action = str(task.parameters.get("action") or "")
        if action in spec.confirmation_actions and task.confirmation == ConfirmationState.NOT_REQUIRED:
            issues.append(f"{task.task_id}: high-impact action must declare confirmation state")
        for issue in parameter_requirement_issues(task, spec):
            issues.append(f"{task.task_id}: {issue}")
        if task.kind == StandardTaskKind.COLLECTION_FINANCIAL_FILTER:
            if task.entity_scope != EntityScope.PREVIOUS_ANSWER:
                issues.append(f"{task.task_id}: collection filter scope must be previous_answer")
            try:
                CollectionFinancialFilterSpec.model_validate(task.parameters)
            except ValidationError as exc:
                issues.append(
                    f"{task.task_id}: invalid collection financial filter: "
                    + "; ".join(error["msg"] for error in exc.errors())
                )
        if task.kind == StandardTaskKind.STOCK_SCREENING:
            screen_spec = task.parameters.get("screen_spec")
            if not isinstance(screen_spec, dict):
                issues.append(f"{task.task_id}: screen_spec must be a complete object")
    if issues:
        raise TaskPlanValidationError("; ".join(issues))


async def resolve_task_plan(
    messages: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
    current_entities: list[dict[str, str]] | None = None,
    previous_answer_entities: list[dict[str, str]] | None = None,
) -> TaskPlan:
    request_text = current_user_request(messages)
    if not request_text:
        raise ValueError("conversation has no user text")
    current = current_entities or []
    previous = previous_answer_entities or []
    for deterministic in (
        _explicit_collection_buy_plan(messages, current, previous),
        _explicit_catalyst_research_plan(messages, current, previous),
    ):
        if deterministic is not None:
            validate_candidate_plan(deterministic)
            return deterministic
    cache_key = _cache_key(messages, llm_cfg, current, previous)
    cached = _load_cached_plan(cache_key)
    if cached is not None:
        logger.info("[TaskPlanner] reused validated persistent plan")
        return cached

    validation_error = ""
    for attempt in range(2):
        context = {
            "current_request": current_user_request(messages, recovery=attempt == 1),
            "previous_answer_reference_outline": previous_assistant_outline(messages),
            "verified_entities_in_current_message": current,
            "verified_entities_in_previous_answer": previous,
            "standard_task_catalog": planner_task_catalog(),
            "quantitative_screen_spec_schema": quantitative_screen_spec_schema(nullable=False),
        }
        if validation_error:
            context["previous_validation_error"] = validation_error
            context["instruction"] = (
                "修正上一候选计划的结构问题，只返回完整标准任务计划；"
                "不要改写用户目标，也不要选择或提及数据工具。"
            )
        kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=False,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            tools=[_PLAN_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_standard_task_plan"}},
            temperature=0,
            max_tokens=2400,
        )
        try:
            timeout = PLANNER_PRIMARY_TIMEOUT_SECONDS if attempt == 0 else PLANNER_RECOVERY_TIMEOUT_SECONDS
            async with asyncio.timeout(timeout):
                response = await completion(**kwargs)
            plan = TaskPlan.model_validate(_payload_from_response(response))
            validate_candidate_plan(plan)
            _save_cached_plan(cache_key, plan)
            return plan
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            validation_error = str(exc)
            logger.warning(
                "[TaskPlanner] attempt=%d failed=%s recovery=%s",
                attempt + 1,
                type(exc).__name__,
                attempt == 0,
            )
            if attempt == 1:
                if isinstance(exc, (ValidationError, ValueError, json.JSONDecodeError)):
                    raise TaskPlanValidationError(
                        f"invalid standard task plan after retry: {exc}"
                    ) from exc
                raise TaskPlannerUnavailableError(
                    "semantic task planner unavailable after recovery"
                ) from exc
    raise TaskPlanValidationError("standard task plan was not resolved")


def _dedupe_symbols(entities: Iterable[dict[str, str]]) -> list[str]:
    symbols: list[str] = []
    for entity in entities:
        symbol = str(entity.get("symbol") or "").strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def resolve_plan_entities(
    plan: TaskPlan,
    *,
    current_entities: list[dict[str, str]],
    previous_answer_entities: list[dict[str, str]],
) -> list[ResolvedTask]:
    """Resolve model-selected scopes only through the local security dictionary."""
    resolved: list[ResolvedTask] = []
    for task in plan.tasks:
        explicit = find_securities_in_text("、".join(task.entities), limit=100)
        if task.entity_scope == EntityScope.CURRENT_MESSAGE:
            candidates = [*current_entities, *explicit]
        elif task.entity_scope == EntityScope.PREVIOUS_ANSWER:
            candidates = [*previous_answer_entities]
        elif task.entity_scope == EntityScope.CONVERSATION:
            candidates = [*explicit, *current_entities, *previous_answer_entities]
        else:
            candidates = explicit
        symbols = _dedupe_symbols(candidates)
        spec = workflow_for(task.kind)
        if spec.requires_entities and not symbols:
            raise TaskPlanValidationError(
                f"{task.task_id} requires a locally verified A-share entity"
            )
        resolved.append(ResolvedTask(candidate=task, symbols=tuple(symbols)))
    return resolved


__all__ = [
    "TaskPlanValidationError",
    "TaskPlannerUnavailableError",
    "current_user_request",
    "previous_assistant_outline",
    "resolve_plan_entities",
    "resolve_task_plan",
    "validate_candidate_plan",
]
