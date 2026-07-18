# -*- coding: utf-8 -*-
"""
Agent Chat endpoint — ReAct loop with assistant-stream DataStreamResponse.

POST /api/v1/agent/chat

Body: { "messages": [ { "role": "user", "content": "贵州茅台行情" } ] }
Response: DataStreamResponse (line-delimited type-code:json chunks)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import litellm
from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.tools import (
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from src.agent.run_registry import (
    ActiveRun,
    RunBroadcaster,
    RunCapacityExceeded,
    active_run_registry,
)
from src.agent.runtime_safety import (
    AgentRequestValidationError,
    agent_request_rate_limiter,
    get_agent_runtime_limits,
    validate_chat_request_body,
)
from src.agent.progress import strip_agent_progress
from src.agent.analysis_playbooks import (
    AnalysisPlaybook,
    INDUSTRY_CHAIN,
    INVESTMENT_DECISION,
    STOCK_DEEP_RESEARCH,
    THEME_COMPANY_MAPPING,
    QUANTITATIVE_SCREENING,
    mandatory_tool_calls,
    select_analysis_playbook,
    select_playbook_for_intent,
)
from src.agent.research_intent import ResearchIntent, resolve_research_intent
from src.agent.evidence_facts import BoundEvidenceFact, bind_company_evidence
from src.tools.registry import ToolRegistry
from src.tools.process_runner import execute_tool_isolated
from src.llm.anthropic_gateway import (
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager
from src.auth import get_client_ip
from src.tools.symbols import (
    find_securities_in_markdown_table_first_column,
    find_securities_in_text,
    normalize_tool_security_arguments,
)

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 6
MAX_TOOL_CALLS_PER_RUN = 10
MAX_TOOL_CALLS_PER_ROUND = 4
MAX_AGENT_RUN_SECONDS = 120.0
TOOL_EXECUTION_TIMEOUT_SECONDS = 45.0
PROFESSIONAL_EVIDENCE_TIMEOUT_SECONDS = 90.0
QUANTITATIVE_SCREEN_TIMEOUT_SECONDS = 210.0
FINAL_SYNTHESIS_TIMEOUT_SECONDS = 60.0

# controller 产出层:当前生产路径走自建后台运行时的 RunBroadcaster (方法名与
# assistant-stream 的 RunController 对齐:append_text/add_tool_call/add_data/
# append_reasoning,以及 _stream_tasks 属性),不再依赖 create_run 的单连接生命周期。
ControllerLike = RunBroadcaster

_registry = ToolRegistry()

SYSTEM_PROMPT = """\
你是专业的 A 股 Stock Agent。你的职责是基于可追溯数据完成研究、比较、风险识别和情景分析，帮助用户形成判断；不编造事实，不承诺收益，不把单一指标直接等同于买卖建议。

## 不可违反的原则
1. 价格、财务、估值、预测、资金流、新闻和宏观数字必须来自工具；没有数据就明确写“缺失”，禁止猜测。
2. 区分事实、计算结果、机构预测、媒体报道和你的推断。机构一致预期不是公司承诺，资金流口径不等同真实机构持仓，社交情绪不代表全市场。
3. 每个关键结论必须能对应到工具证据，并注明数据日期/报告期和来源。数据陈旧、降级或抓取不完整时必须显式提示。
4. 同时寻找支持证据和反证；风险、异常现金流、估值透支和预期落空可能性优先呈现。
5. 使用中文和 Markdown；数字用千分位，百分比保留两位小数。不要输出虚假的精确目标价。

## 工具优先级
1. 结构化股票数据优先：行情/K线 → 财务报表 → 主营构成 → 估值与一致预期 → 同行比较 → 个股资金流。
2. 个股消息优先 search_news、get_announcements、get_research_report、get_risk_events；市场/行业/宏观主题资讯直接用 search_financial_news，它会自动选择 Infos 页的 RSSHub 源，不要先查询源目录。
3. 只有结构化工具和 RSSHub 不足、为空或需要读取某个公开网页原文时，才使用 websearch；拿到具体 URL 后再按需用 webfetch。网页内容必须交叉验证，不能覆盖更权威的结构化或公告数据。
4. 股票代码和名称由工具内部解析，不要为了代码确认单独浪费一次调用；只有身份存在歧义时才用 get_stock_info 核实。
5. 对话里出现多家公司时，优先一次调用 get_multi_stock_snapshot；它已包含行情、估值、技术与最新报告期财务快照。拿到成功结果后直接回答，不要再为每家公司分别重复调用行情、技术或财务工具；只有用户明确要求深挖某一家公司时再补充单股证据。
6. 不得凭记忆猜证券代码。系统给出的“已核验证券实体”是唯一可信的名称/代码映射；缺失时把公司名称原样传给工具解析。

## 专业分析框架
- 公司质量：主营构成、收入与利润趋势、ROE/毛利率/现金流、资产负债与股东变化。
- 预期与估值：PE/PB/历史分位、同行估值、未来 EPS/净利润一致预期、预期兑现条件。
- 技术与交易状态：趋势、MACD/RSI/ATR、量价、支撑阻力、个股与板块资金持续性。
- 催化与风险：正式公告优先，其次研报与新闻；标明事件时间、信息级别和可能影响路径。
- 市场环境：指数、市场宽度、板块轮动、利率与宏观变量；只在确实影响标的时展开。

## 工作方式
1. 先识别问题是行情查询、单项研究、完整个股研究、同行比较、行业主题还是风险检查。
2. 只回答用户这一轮真正问的范围，并结合最近对话解析“它、这些公司、上面提到的”等指代。指代仍不唯一时先简短确认，不要擅自选择对象。
3. 每轮并行调用 1-4 个互补工具，整轮通常 2-6 个工具已经足够。只取能改变结论的证据；证据已足够时立即停止调用，禁止为了“更全面”重复检索同一维度。
4. 多公司或组合问题优先 get_multi_stock_snapshot 一次批量取数；主题研究优先用 search_financial_news 与 search_research_library 交叉验证。只有用户确实问当前盘面、价格或交易状态时才取实时数据。
5. 公司、主题或产业关联必须区分“已形成相关收入/订单”“已送样或客户验证”“仅有技术储备/概念关联”，不得把候选关系写成已确认事实。
6. 完整个股研究通常至少覆盖：get_stock_info/get_business_segments、get_financials、get_valuation_ratios/get_consensus_estimates、get_peer_comparison、get_technical_indicators/get_stock_capital_flow、公告与风险事件。按问题裁剪，不机械全调。
7. 信息不足时继续补取；工具明确失败后不要用相同参数重试，改用其声明的降级路径或说明缺口。
8. 单一季度的经营现金流可能有季节性，未取得同比或连续报告期数据时，只能描述当期差异，不能直接下结论为利润质量恶化、渠道压货或长期趋势。

## 最终回答最低要求
- 先给结论摘要，再列关键证据、反证/风险和仍缺失的信息。
- 对投资判断给出“成立条件、失效条件、需要跟踪的指标”，而不是只给看多/看空标签。
- 关键事实尽量用 Markdown 链接引用工具返回的原始来源；明确数据截至时间、主要来源、是否用了网页兜底，以及置信度（高/中/低）和原因。
- 直接回答，不复述内部规划，不输出“第一步/接下来我会”等过程旁白。
"""

def _strip_progress_markers(text: str) -> str:
    """Remove UI-only progress copy before conversation history returns to the LLM."""
    return strip_agent_progress(text)


def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return _join_text_parts(content)
    return ""


def _resolve_turn_policy(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return one general Agent policy instead of brittle keyword routing.

    Tool choice belongs to the model plus deterministic execution middleware.
    Hard-coded intent branches made follow-up questions lose capabilities as
    soon as the wording changed, which is unacceptable for a general chat.
    """
    text = _last_user_text(messages).lower()
    is_casual = text in {"你好", "您好", "hello", "hi", "谢谢", "在吗"}
    return {
        "name": "casual" if is_casual else "general_agent",
        "allowed_tools": set() if is_casual else None,
        "max_tool_calls": MAX_TOOL_CALLS_PER_RUN,
        "max_discovery_calls": None,
        "require_tools": False,
        "guidance": (
            "闲聊无需调用工具。"
            if is_casual
            else "按当前问题选择最少且互补的工具；涉及实时或外部事实必须取证。多证券任务必须批量查询。"
        ),
    }


def _verified_entity_context(messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, str]]]:
    """Build a deterministic name/code map from recent conversation history."""
    latest_user = _last_user_text(messages).lower()
    is_referential = any(
        marker in latest_user
        for marker in ("这些", "上述", "上面", "前面", "它们", "他们", "those", "them")
    )
    if is_referential:
        # Scope “these companies” to the subjects explicitly listed in the
        # immediately preceding assistant table.  Mining all prose also picks
        # up examples, caveats and even the generic word “机器人” (which is an
        # A-share company name), silently broadening a six-company question.
        for message in reversed(messages[:-1]):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            text = content if isinstance(content, str) else _join_text_parts(content or [])
            listed = find_securities_in_markdown_table_first_column(text, limit=20)
            if not listed:
                # Some comparison tables put the company in the second or
                # third column (for example "细分环节 / 核心公司 / 逻辑").
                # Read only table rows from the immediately preceding answer;
                # prose may mention benchmark companies or old examples that
                # are not part of “这些公司”.
                table_entities: List[Dict[str, str]] = []
                seen_symbols: set[str] = set()
                for line in text.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("|"):
                        continue
                    if re.fullmatch(r"\|?[\s|:\-]+\|?", stripped):
                        continue
                    # Company cells in deliverable tables carry their verified
                    # six-digit code. Do not scan the whole row: phrases such
                    # as “人形机器人” would otherwise resolve the generic word
                    # “机器人” to the listed company 300024.
                    coded_cells = [
                        cell
                        for cell in stripped.strip("|").split("|")
                        if re.search(r"(?<!\d)[036]\d{5}(?!\d)", cell)
                    ]
                    for cell in coded_cells:
                        for entity in find_securities_in_text(cell, limit=20):
                            if entity["symbol"] in seen_symbols:
                                continue
                            seen_symbols.add(entity["symbol"])
                            table_entities.append(entity)
                            if len(table_entities) >= 20:
                                break
                        if len(table_entities) >= 20:
                            break
                    if len(table_entities) >= 20:
                        break
                listed = table_entities
            if listed:
                mapping = "、".join(f"{item['name']}={item['symbol']}" for item in listed)
                return (
                    "已核验证券实体（上一条回答表格中明确列出的公司，禁止扩展范围）：" + mapping,
                    listed,
                )
            return "上一条回答没有可核验的证券实体；禁止从更早历史扩展‘这些公司’的范围。", []

    collected: List[Dict[str, str]] = []
    seen: set[str] = set()
    for message in reversed(messages[-8:]):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else _join_text_parts(content or [])
        for entity in find_securities_in_text(text, limit=20):
            if entity["symbol"] in seen:
                continue
            seen.add(entity["symbol"])
            collected.append(entity)
            if len(collected) >= 20:
                break
        if len(collected) >= 20:
            break
    if not collected:
        return "本轮上下文没有自动识别到已核验证券实体；遇到指代不清时先向用户确认。", []
    mapping = "、".join(f"{item['name']}={item['symbol']}" for item in collected)
    return (
        "已核验证券实体（来自本地 stock_meta，禁止改写或猜测代码）：" + mapping,
        collected,
    )


def _previous_answer_table_entities(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Return only coded securities from the immediately previous answer table."""
    for message in reversed(messages[:-1]):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else _join_text_parts(content or [])
        listed = find_securities_in_markdown_table_first_column(text, limit=20)
        if listed:
            return listed
        table_entities: List[Dict[str, str]] = []
        seen_symbols: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|") or re.fullmatch(r"\|?[\s|:\-]+\|?", stripped):
                continue
            for cell in stripped.strip("|").split("|"):
                if not re.search(r"(?<!\d)[036]\d{5}(?!\d)", cell):
                    continue
                for entity in find_securities_in_text(cell, limit=20):
                    if entity["symbol"] in seen_symbols:
                        continue
                    seen_symbols.add(entity["symbol"])
                    table_entities.append(entity)
                    if len(table_entities) >= 20:
                        return table_entities
        return table_entities
    return []


def _entities_for_semantic_intent(
    intent: ResearchIntent,
    *,
    current_entities: List[Dict[str, str]],
    previous_answer_entities: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Resolve the model-selected scope against the local security dictionary."""
    if intent.entity_scope == "previous_answer":
        return previous_answer_entities
    semantic_entities = find_securities_in_text("、".join(intent.entities), limit=20)
    if intent.entity_scope == "current_message":
        candidates = [*current_entities, *semantic_entities]
    elif intent.entity_scope == "conversation":
        candidates = [*semantic_entities, *previous_answer_entities]
    else:
        candidates = semantic_entities
    resolved: List[Dict[str, str]] = []
    seen_symbols: set[str] = set()
    for entity in candidates:
        symbol = str(entity.get("symbol") or "")
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        resolved.append(entity)
    return resolved[:20]


def _entity_context_text(
    entities: List[Dict[str, str]],
    *,
    semantic_scope: Optional[str] = None,
) -> str:
    if not entities:
        return "本轮语义范围没有已核验证券实体；禁止猜测证券代码。"
    mapping = "、".join(f"{item['name']}={item['symbol']}" for item in entities)
    scope = f"，语义范围={semantic_scope}" if semantic_scope else ""
    return f"已核验证券实体（来自本地 stock_meta{scope}，禁止扩展范围）：{mapping}"


def _coalesce_multi_security_calls(
    calls: List[Dict[str, Any]],
    *,
    verified_entities: Optional[List[Dict[str, str]]] = None,
    referential_followup: bool = False,
) -> List[Dict[str, Any]]:
    """Collapse repeated quote/technical calls into one batch snapshot call."""
    batchable_names = {"get_realtime_quotes", "get_technical_indicators"}
    batch_indexes: List[int] = []
    raw_symbols: List[str] = []
    for index, call in enumerate(calls):
        if str(call.get("name") or "").strip() not in batchable_names:
            continue
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except (TypeError, json.JSONDecodeError):
            arguments = {}
        value = arguments.get("symbols") or arguments.get("symbol")
        if isinstance(value, str) and value.strip():
            batch_indexes.append(index)
            raw_symbols.extend(
                part.strip()
                for part in value.replace("，", ",").split(",")
                if part.strip()
            )
    unique_symbols = list(dict.fromkeys(raw_symbols))
    if len(batch_indexes) < 2 or len(unique_symbols) < 2:
        return calls
    if referential_followup and verified_entities:
        # For “these companies / the ones above”, the model may emit remembered
        # numeric codes.  Preserve its requested count but replace guessed codes
        # with the recent deterministic entity map in conversation order.
        unique_symbols = [
            item["symbol"]
            for item in verified_entities[: min(len(unique_symbols), 12)]
        ]

    first_index = batch_indexes[0]
    synthetic = {
        "id": calls[first_index].get("id") or f"call_{uuid.uuid4().hex}",
        "name": "get_multi_stock_snapshot",
        "arguments": json.dumps({"symbols": ",".join(unique_symbols)}, ensure_ascii=False),
    }
    batch_index_set = set(batch_indexes)
    result: List[Dict[str, Any]] = []
    for index, call in enumerate(calls):
        if index == first_index:
            result.append(synthetic)
        elif index not in batch_index_set:
            result.append(call)
    return result


def _select_balanced_multi_security_calls(
    calls: List[Dict[str, Any]],
    *,
    latest_user_text: str,
    limit: int = MAX_TOOL_CALLS_PER_ROUND,
) -> List[Dict[str, Any]]:
    """Choose complete evidence dimensions instead of truncating stock pairs.

    Models often request the same tool once per compared company.  Taking the
    first N calls can leave one company with valuation/business/risk evidence
    and the other without it.  A successful batch snapshot already covers
    quote valuation, technicals and latest-period fundamentals, so redundant
    calls are removed and the remaining per-company calls are admitted as a
    whole group by tool name.
    """
    batch = next(
        (call for call in calls if call.get("name") == "get_multi_stock_snapshot"),
        None,
    )
    if batch is None:
        return calls[:limit]

    text = latest_user_text.lower()
    wants_history = any(marker in text for marker in ("历史", "分位", "历年", "趋势", "连续"))
    covered = {"get_realtime_quotes", "get_technical_indicators"}
    if not wants_history:
        covered.update({"get_financials", "get_valuation_ratios"})

    groups: Dict[str, List[Dict[str, Any]]] = {}
    first_index: Dict[str, int] = {}
    for index, call in enumerate(calls):
        name = str(call.get("name") or "")
        if call is batch or name in covered:
            continue
        groups.setdefault(name, []).append(call)
        first_index.setdefault(name, index)

    risk_names = {"get_risk_events", "get_announcements", "search_news"}
    business_names = {"get_business_segments", "get_stock_info"}
    forecast_names = {"get_consensus_estimates", "get_peer_comparison", "get_research_report"}
    wants_risk = any(marker in text for marker in ("风险", "雷", "隐患", "诉讼", "监管"))
    wants_business = any(marker in text for marker in ("基本面", "主营", "业务", "产品", "收入构成"))
    wants_forecast = any(marker in text for marker in ("预期", "预测", "未来", "增长"))

    try:
        batch_args = json.loads(batch.get("arguments") or "{}")
    except (TypeError, json.JSONDecodeError):
        batch_args = {}
    raw_batch_symbols = batch_args.get("symbols")
    batch_symbols = [
        item.strip()
        for item in re.split(r"[,，、;；]+", str(raw_batch_symbols or ""))
        if item.strip()
    ][:12]

    # Fill an explicitly requested comparison dimension even when the model's
    # first plan only asked for the batch snapshot.  These calls are symmetric
    # by construction, so both companies are judged from the same evidence.
    synthetic_name: Optional[str] = None
    if wants_risk and not any(name in risk_names for name in groups):
        synthetic_name = "get_risk_events"
    elif wants_business and not any(name in business_names for name in groups):
        synthetic_name = "get_business_segments"
    elif wants_history and "get_valuation_ratios" not in groups:
        synthetic_name = "get_valuation_ratios"
    elif wants_forecast and not any(name in forecast_names for name in groups):
        synthetic_name = "get_consensus_estimates"
    if synthetic_name and batch_symbols:
        groups[synthetic_name] = [
            {
                "id": f"call_{uuid.uuid4().hex}",
                "name": synthetic_name,
                "arguments": json.dumps({"symbol": symbol}, ensure_ascii=False),
            }
            for symbol in batch_symbols
        ]
        first_index[synthetic_name] = -1

    def priority(name: str) -> tuple[int, int]:
        score = 30
        if wants_risk and name in risk_names:
            score = 0
        elif wants_business and name in business_names:
            score = 5
        elif wants_forecast and name in forecast_names:
            score = 10
        return score, first_index[name]

    selected = [batch]
    remaining = max(0, limit - 1)
    for name in sorted(groups, key=priority):
        group = groups[name]
        if len(group) > remaining:
            continue
        selected.extend(group)
        remaining -= len(group)
        if remaining == 0:
            break
    return selected


# 兼容别名：历史代码与测试以 ``AgentModelConfigError`` 捕获网关配置缺失错误。
# Phase 6 测试迁移后将移除此别名，统一改用 ``AnthropicGatewayConfigError``。
AgentModelConfigError = AnthropicGatewayConfigError


# 历史轮 tool 结果中的「明细数组」字段：这些是单次压缩保留给当前轮 LLM 看的细节，
# 进入下一轮后已无价值（模型只引用最新结论），裁掉可显著降低 token 累积。
_TOOL_DETAIL_ARRAY_KEYS = (
    "recent", "recent_periods", "history", "items", "top_movers", "bottom_movers",
    "inflow_top", "outflow_top", "top_holders", "holder_changes", "daily_trend",
    "score_trend", "search_fallback", "criteria",
)


def _slim_tool_content(result_str: str) -> str:
    """对历史轮的 tool 结果做二次瘦身：丢弃明细数组，保留摘要字段。

    解析失败（非 JSON / 非 dict）则原样返回，绝不破坏结果。已裁剪过的（含
    `_slimmed` 标记）不重复处理。只影响回灌给 LLM 的历史消息，不影响 UI 气泡。
    """
    text = (result_str or "").strip()
    if not text or text[0] != "{":
        return result_str
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return result_str
    if not isinstance(payload, dict) or payload.get("_slimmed"):
        return result_str

    slimmed: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in _TOOL_DETAIL_ARRAY_KEYS:
            continue
        slimmed[key] = value
    slimmed["_slimmed"] = True
    try:
        return json.dumps(slimmed, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return result_str


# ---------------------------------------------------------------------------
# 入站消息格式适配：前端（@assistant-ui/react-data-stream）发的是 AI SDK v5
# 格式（content 为 part 数组），后端 ReAct 循环内部与 litellm/anthropic 用的是
# OpenAI 格式（tool_calls / tool_call_id + 字符串 content）。两类格式混发会让
# litellm 的 anthropic 转换层静默丢弃历史 tool 数据（convert_to_anthropic_tool_result
# 只认 text/image_url part 且要求顶层 tool_call_id）。这里在入口统一归一化。
# ---------------------------------------------------------------------------

_AI_SDK_PART_TYPES = {"text", "tool-call", "tool-result", "reasoning", "file", "image"}


def _is_aisdk_content(content: Any) -> bool:
    """判断 content 是否为 AI SDK v5 的 part 数组（而非 OpenAI 的字符串/对象）。"""
    if not isinstance(content, list) or not content:
        return False
    return any(
        isinstance(p, dict) and p.get("type") in _AI_SDK_PART_TYPES for p in content
    )


def _join_text_parts(parts: List[Dict[str, Any]]) -> str:
    """把 AI SDK 的 text part 文本拼成一个字符串（reasoning 不拼入 content）。"""
    chunks: List[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            text = str(p.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def _convert_aisdk_assistant(msg: Dict[str, Any]) -> Dict[str, Any]:
    """AI SDK assistant 消息 → OpenAI 格式（content 字符串 + tool_calls）。"""
    parts = msg.get("content") or []
    content_text = _strip_progress_markers(_join_text_parts(parts))
    tool_calls: List[Dict[str, Any]] = []
    for p in parts:
        if not isinstance(p, dict) or p.get("type") != "tool-call":
            continue
        tool_call_id = p.get("toolCallId") or f"call_{uuid.uuid4().hex}"
        tool_name = p.get("toolName") or ""
        try:
            arguments = json.dumps(p.get("input") or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            arguments = "{}"
        tool_calls.append({
            "id": tool_call_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": arguments},
        })
    out: Dict[str, Any] = {"role": "assistant", "content": content_text or None}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _convert_aisdk_tool(msg: Dict[str, Any]) -> Dict[str, Any]:
    """AI SDK tool 消息 → OpenAI 格式（tool_call_id + 字符串 content）。

    转换后立即对 content 跑一次 _slim_tool_content：这是从前端回传的历史 tool 结果，
    属于更早轮次，明细数组对当前/后续轮无价值，裁掉省 token。本轮新加的 tool 消息
    由 _run_react_loop 末尾的 current_tool_ids 逻辑负责（保完整）。
    """
    parts = msg.get("content") or []
    tool_result = next(
        (p for p in parts if isinstance(p, dict) and p.get("type") == "tool-result"),
        None,
    )
    if tool_result is None:
        return {"role": "tool", "tool_call_id": f"call_{uuid.uuid4().hex}", "content": ""}
    tool_call_id = tool_result.get("toolCallId") or f"call_{uuid.uuid4().hex}"
    output = tool_result.get("output") or {}
    value = output.get("value") if isinstance(output, dict) else output
    is_error = bool(
        (isinstance(output, dict) and output.get("type") == "error-json")
        or tool_result.get("isError")
    )
    try:
        content_str = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        content_str = str(value)
    if is_error:
        content_str = "[工具执行错误] " + content_str
    # 历史 tool 结果二次瘦身（明细数组已无价值）
    content_str = _slim_tool_content(content_str)
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content_str}


def _normalize_incoming_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把前端入站消息归一化为 OpenAI 格式（litellm/anthropic 期望）。

    兼容三种 content 形态：
    - 字符串：OpenAI/简化格式，按原样透传（user/system/assistant）。
    - AI SDK part 数组：转换（见 _convert_aisdk_*）。
    - 已是 OpenAI 格式（tool 有顶层 tool_call_id、assistant 有顶层 tool_calls）：透传。

    单条消息转换失败则原样透传，不阻断整批。
    """
    normalized: List[Dict[str, Any]] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        try:
            role = str(raw.get("role") or "").strip()
            content = raw.get("content")

            # 已是 OpenAI 格式：tool 有顶层 tool_call_id，或 assistant 有顶层 tool_calls → 透传
            if role == "tool" and raw.get("tool_call_id") is not None:
                normalized.append(raw)
                continue
            if role == "assistant" and raw.get("tool_calls") is not None:
                normalized.append(raw)
                continue

            # assistant / tool 的数组 content 一律按 AI SDK 转（OpenAI 这两个角色的
            # content 不会是数组，无需用 _is_aisdk_content 探测 part type）。
            if role == "assistant" and isinstance(content, list):
                normalized.append(_convert_aisdk_assistant(raw))
                continue
            if role == "tool" and isinstance(content, list):
                normalized.append(_convert_aisdk_tool(raw))
                continue

            if not _is_aisdk_content(content):
                # 字符串/空 content：原样透传（补 role 默认值）
                normalized_content = content
                if role == "assistant" and isinstance(content, str):
                    normalized_content = _strip_progress_markers(content)
                normalized.append({
                    "role": role or "user",
                    **{
                        k: (normalized_content if k == "content" else v)
                        for k, v in raw.items()
                        if k != "role"
                    },
                })
                continue

            # user / system / 未知 role 的 AI SDK 数组 content：拼文本
            normalized.append({
                "role": role or "user",
                "content": _join_text_parts(content) or None,
            })
        except Exception:
            logger.debug("[Agent] normalize message failed, passthrough: %s", raw)
            normalized.append(raw)
    return normalized


# ---------------------------------------------------------------------------
# 模型配置 / litellm kwargs 组装已收敛到共享 helper ``src.llm.anthropic_gateway``。
# 此处保留薄封装别名，便于本文件内调用点维持原读法；``stream`` 由调用方显式传入。
# ---------------------------------------------------------------------------
_get_llm_config = resolve_anthropic_gateway_config


def _build_llm_kwargs(llm_cfg: Dict[str, Any], *, stream: bool, **extra: Any) -> Dict[str, Any]:
    """Assemble litellm kwargs from the gateway config (delegates to shared helper)."""
    return build_litellm_kwargs(llm_cfg, stream=stream, **extra)


# ---------------------------------------------------------------------------
# 上下文窗口管理：token 估算 + 超阈值时自动摘要压缩早期对话
# ---------------------------------------------------------------------------

# 压缩触发阈值 = 窗口 × 0.8（留 20% 给回复，弥补中文 token 估算偏差）
_CONTEXT_COMPACT_RATIO = 0.8
# 压缩时保留最近多少条消息完整（user/assistant/tool 成组，不切断 tool_call↔result 配对）
_KEEP_RECENT_MESSAGES = 6

_COMPACT_SUMMARY_PROMPT = """\
你是对话压缩助手。下面是用户与 A 股分析助手的早期对话（含工具调用与结果）。
请把它压缩成一段紧凑的中文摘要，供后续对话引用。要求：
1. 保留所有出现过的股票代码、公司名称、关键数值（价格、涨跌幅、财务指标、日期）。
2. 保留已得出的分析结论与判断（如"近60天震荡上行""估值偏高""不可买入"等）。
3. 丢弃查询过程、工具调用细节、重复的中间数据明细。
4. 只输出摘要正文，不要加标题或额外说明。\
"""


def _estimate_messages_tokens(messages: List[Dict[str, Any]], model: str) -> int:
    """估算 messages 的 token 数。litellm.token_counter 失败时回退字符粗估。"""
    try:
        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:
        # 回退：网关模型未映射 → 按字符粗估（中文约 1.5 字/token，英文约 4 字符/token，取 2.5 偏保守）
        total_chars = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                total_chars += sum(len(str(p)) for p in content)
            # tool_calls 等结构也计入
            tc = m.get("tool_calls")
            if tc:
                total_chars += sum(len(json.dumps(t, ensure_ascii=False, default=str)) for t in tc)
        return int(total_chars / 2.5) + len(messages) * 4


async def _summarize_for_compaction(
    llm_cfg: Dict[str, Any], to_summarize: List[Dict[str, Any]]
) -> Optional[str]:
    """调一次 LLM 把早期消息压缩成摘要文本。失败返回 None（调用方回退）。"""
    compact_messages = [
        {"role": "system", "content": _COMPACT_SUMMARY_PROMPT},
        {"role": "user", "content": json.dumps(
            [{"role": m.get("role"), "content": m.get("content")} for m in to_summarize],
            ensure_ascii=False, default=str,
        )},
    ]
    kwargs = _build_llm_kwargs(llm_cfg, stream=False, messages=compact_messages)  # 摘要非流式，直接拿完整文本
    try:
        response = await litellm.acompletion(**kwargs)
        text = ""
        for choice in getattr(response, "choices", []) or []:
            msg = getattr(choice, "message", None)
            if msg and getattr(msg, "content", None):
                text += str(msg.content)
        return text.strip() or None
    except Exception:
        logger.warning("[Agent] compaction summary LLM call failed", exc_info=True)
        return None


async def _compact_history_if_needed(
    full_messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """超阈值时把早期对话摘要化，保留最近 _KEEP_RECENT_MESSAGES 条完整。

    返回可能被压缩后的 full_messages。未超阈值或无法压缩时原样返回。
    """
    context_window = llm_cfg.get("context_window") or 200_000
    threshold = int(context_window * _CONTEXT_COMPACT_RATIO)
    tokens_before = _estimate_messages_tokens(full_messages, llm_cfg["model"])
    if tokens_before < threshold:
        return full_messages

    # 至少保留 system + 近期消息；待摘要部分为空（消息太少但单条超长）则无法压缩
    if len(full_messages) <= _KEEP_RECENT_MESSAGES + 1:
        logger.info(
            "[Agent] context over threshold (%d/%d) but too few messages to compact",
            tokens_before, threshold,
        )
        return full_messages

    # system（首条）保留，其后到「倒数 KEEP_RECENT_MESSAGES 条」之间为待摘要部分
    keep_count = _KEEP_RECENT_MESSAGES
    to_summarize = full_messages[1:-keep_count]
    recent = full_messages[-keep_count:]
    # 待摘要太少（<=1）则不再压缩：可能是上一轮刚压缩过只剩摘要，再压缩无意义且会重复。
    if len(to_summarize) <= 1:
        return full_messages

    summary = await _summarize_for_compaction(llm_cfg, to_summarize)
    if not summary:
        # 摘要失败：宁可交给主调用可能超限，也不丢数据、不伪造摘要
        logger.warning(
            "[Agent] compaction skipped (summary empty), tokens=%d threshold=%d",
            tokens_before, threshold,
        )
        return full_messages

    compacted = [
        full_messages[0],  # system
        {"role": "user", "content": f"[早期对话摘要]\n{summary}"},
        *recent,
    ]
    tokens_after = _estimate_messages_tokens(compacted, llm_cfg["model"])
    # 只记日志，不向前端推提示：append_text 会进 assistant 消息正文被持久化和回传 LLM，
    # 污染对话；data-stream 协议又无 onData 钩子接收结构化事件。压缩对用户透明即可。
    logger.info(
        "[Agent] context compacted: %d msgs → summary, tokens %d → %d (threshold %d)",
        len(to_summarize), tokens_before, tokens_after, threshold,
    )
    return compacted


async def _flush_substreams(controller: ControllerLike) -> None:
    """Wait for all pending add_stream reader tasks to finish."""
    for task in controller._stream_tasks:
        if not task.done():
            await task


def _build_synthesis_messages(
    messages: List[Dict[str, Any]],
    evidence: Optional[List[Dict[str, Any]]] = None,
    playbook: Optional[AnalysisPlaybook] = None,
) -> List[Dict[str, Any]]:
    """Build a text-only transcript for the no-tool final synthesis pass.

    Anthropic rejects a request that contains historical ``tool_use`` messages
    when the request omits ``tools``.  The final pass intentionally has no tool
    access, so tool calls/results are converted into one explicit evidence
    packet while normal conversational context remains intact.
    """
    system_parts: List[str] = []
    dialogue: List[Dict[str, str]] = []
    inferred_evidence: List[Dict[str, Any]] = []
    tool_names_by_id: Dict[str, str] = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        if role == "system":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                system_parts.append(content.strip())
            continue
        if role == "assistant" and msg.get("tool_calls"):
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                tool_names_by_id[str(call.get("id") or "")] = str(fn.get("name") or "")
            # Planning prose produced alongside tool calls is internal work, not
            # an answer that should be repeated in the final synthesis.
            continue
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            inferred_evidence.append({
                "tool": tool_names_by_id.get(call_id) or "unknown_tool",
                "result": msg.get("content"),
            })
            continue
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        cleaned = _strip_progress_markers(content) if role == "assistant" else content.strip()
        if cleaned:
            dialogue.append({"role": role, "content": cleaned})

    synthesis_instruction = (
        "你现在处于最终写作阶段，不能再调用工具。请只依据对话与下方工具证据，"
        "直接完成用户当前问题。不要描述检索过程，不要补造证据中没有的事实。"
        "所有价格、涨跌、估值、财务和技术指标必须逐项存在于 success=true 的本轮工具结果中；"
        "即使是常识或记忆中的历史数字，只要本轮证据没有提供就必须省略，禁止写‘历史约为’；"
        "失败、超时、未解析证券或陈旧数据不得被改写成成功事实。"
        "禁止新增未来披露日期、行业阶段或板块整体走势，除非本轮工具证据明确给出。"
        "若不同来源冲突，明确指出冲突；若证据不足，缩小结论并说明缺口。"
        "投资类问题给条件化判断和风险边界，不给脱离期限与风险承受能力的确定性买卖指令。"
        "交易时段内的实时行情只能称为盘中快照或最新价，禁止写成收盘价；"
        "多公司对比表必须同时列出已核验的公司名称和证券代码。"
    )
    if playbook is None:
        synthesis_instruction += (
            "只覆盖用户明确要求的维度；用户没问技术面时，不要添加 RSI、MACD、均线等技术段落。"
            "答案以高信息密度为准：多公司普通初筛严格控制在 900 个汉字以内；先用一张最多五列的紧凑表"
            "完整列完全部公司（公司/代码、关键数据、判断、触发条件），再写至多三条共性结论与风险。"
            "不要逐家公司重复基本面段落，不要复制工具返回的全部字段。必须留足篇幅用完整句子收尾。"
        )
    else:
        synthesis_instruction += (
            "\n\n" + playbook.system_instruction()
            + "\n最终回答必须证明已覆盖上述每个证据维度和输出项；"
            "先用已取得的证据回答用户真正的问题。补证后仍缺少的关键维度只在结尾集中说明一次，"
            "禁止逐段、逐行重复‘证据缺失’，也禁止用大篇幅缺口清单代替结论。"
        )
        if playbook.id == INDUSTRY_CHAIN.id:
            synthesis_instruction += (
                "产业链回答控制在约 2600 个汉字内：优先级、重点环节、反证、跟踪指标和置信度都必须完成后再停止；"
                "不要为每个环节复制同一套大表，也不要输出独立的数据缺口表。"
            )
    system_text = "\n\n".join(system_parts) or SYSTEM_PROMPT
    result: List[Dict[str, Any]] = [
        {"role": "system", "content": f"{system_text}\n\n{synthesis_instruction}"},
        *dialogue,
    ]
    evidence_packet = list(evidence if evidence is not None else inferred_evidence)
    if playbook is not None and playbook.id == THEME_COMPANY_MAPPING.id and evidence_packet:
        # Discovery tools identify candidate names, but final synthesis has no
        # tool access. Resolve every name appearing in this evidence against
        # the local security master before the model writes the table, so it
        # can never guess a code or leave ``代码待核验`` in a deliverable.
        candidate_text = json.dumps(evidence_packet, ensure_ascii=False, default=str)
        candidate_entities = find_securities_in_text(candidate_text, limit=60)
        evidence_packet.append({
            "tool": "runtime_security_entity_map",
            "result": {
                "success": True,
                "resolved_entities": candidate_entities,
                "instruction": (
                    "公司/代码只能从本表选择；未出现在本表中的候选公司不得列入最终公司表。"
                ),
            },
        })
    if evidence_packet:
        result.append({
            "role": "user",
            "content": (
                "[本轮已核验的工具证据]\n"
                + json.dumps(evidence_packet, ensure_ascii=False, default=str)
            ),
        })
    return result


def _build_professional_decision_fallback(
    batch: Dict[str, Any],
    *,
    decision_requested: Optional[bool] = None,
) -> str:
    """Render a complete seven-dimension decision when model synthesis fails."""

    thesis = str(batch.get("thesis") or "")
    if decision_requested is None:
        decision_requested = bool(re.search(
            r"能买吗|能不能买|是否能买|值得买|买入|抄底|入场|介入|加仓|减仓|卖出|持有|仓位|止损|止盈|追高",
            thesis,
        ))
    heading = "专业买入决策结论" if decision_requested else "综合研究结论"
    intro = (
        "本轮按七个维度逐家公司完成介入条件检查。"
        if decision_requested
        else "本轮按七个维度逐家公司完成基本面与风险研究。"
    )

    def number(value: Any, digits: int = 2, suffix: str = "") -> str:
        try:
            return f"{float(value):,.{digits}f}{suffix}"
        except (TypeError, ValueError):
            return "缺失"

    def short(value: Any, limit: int = 34) -> str:
        text = str(value or "证据缺失").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def amount_yi(value: Any) -> str:
        try:
            return f"{float(value) / 100000000:,.2f}亿"
        except (TypeError, ValueError):
            return "缺失"

    rows: List[str] = []
    details: List[str] = []
    priority_groups: Dict[str, List[str]] = {}
    for item in batch.get("items") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "—")
        name = str(item.get("name") or symbol)
        snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else {}
        quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
        technical = snapshot.get("technical") if isinstance(snapshot.get("technical"), dict) else {}
        indicators = technical.get("indicators") if isinstance(technical.get("indicators"), dict) else {}
        financials = item.get("financials") if isinstance(item.get("financials"), dict) else {}
        periods = financials.get("items") if isinstance(financials.get("items"), list) else []
        latest = periods[-1] if periods and isinstance(periods[-1], dict) else {}
        valuation = item.get("valuation") if isinstance(item.get("valuation"), dict) else {}
        consensus = item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
        estimates = consensus.get("estimates") if isinstance(consensus.get("estimates"), list) else []
        first_estimate = estimates[0] if estimates and isinstance(estimates[0], dict) else {}
        capital = item.get("capital_flow") if isinstance(item.get("capital_flow"), dict) else {}
        flow_10d = ((capital.get("windows") or {}).get("10d") or {}) if isinstance(capital.get("windows"), dict) else {}
        risks = item.get("risk_events") if isinstance(item.get("risk_events"), dict) else {}
        risk_analysis = risks.get("analysis") if isinstance(risks.get("analysis"), dict) else {}
        coverage = item.get("evidence_coverage") if isinstance(item.get("evidence_coverage"), dict) else {}
        profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
        segments = item.get("business_segments") if isinstance(item.get("business_segments"), dict) else {}
        segment_names = [
            str(segment.get("segment_name"))
            for segment in (segments.get("items") or [])[:3]
            if isinstance(segment, dict) and segment.get("segment_name")
        ]

        negative_profit = latest.get("parent_net_profit") is not None and latest.get("parent_net_profit") <= 0
        high_debt = (latest.get("debt_ratio") or 0) >= 80
        high_risk = (risk_analysis.get("active_high_severity_count") or 0) > 0
        weak_growth = latest.get("revenue_yoy") is not None and latest.get("revenue_yoy") < 0
        pe_ttm = valuation.get("pe_ttm")
        industry_pe = (valuation.get("industry_average") or {}).get("pe") if isinstance(valuation.get("industry_average"), dict) else None
        expensive = bool(pe_ttm and industry_pe and pe_ttm > industry_pe * 1.5)
        trend_weak = indicators.get("return_20d_pct") is not None and indicators.get("return_20d_pct") <= -15
        flow_weak = flow_10d.get("main_net_inflow") is not None and flow_10d.get("main_net_inflow") < 0

        if not coverage.get("complete"):
            verdict = "数据源异常（证据链未完成）"
        elif negative_profit or high_debt or high_risk:
            verdict = "风险规避"
        elif expensive and weak_growth:
            verdict = "暂不买入"
        elif expensive or trend_weak or flow_weak:
            verdict = "暂不介入（条件未满足）"
        else:
            verdict = "可研究候选"
        priority_groups.setdefault(verdict, []).append(f"{name} ({symbol})")

        business = short("、".join(segment_names) or profile.get("main_business"), 28)
        financial = (
            f"收入YoY {number(latest.get('revenue_yoy'), 1, '%')}；"
            f"净利YoY {number(latest.get('parent_net_profit_yoy'), 1, '%')}；"
            f"OCF {amount_yi(latest.get('operating_cash_flow'))}"
        )
        expectation = (
            "机构一致预期覆盖0家（查询完成）"
            if consensus.get("coverage_status") == "no_sell_side_coverage"
            else "一致预期来源异常"
        )
        if first_estimate:
            np_forecast = first_estimate.get("net_profit") if isinstance(first_estimate.get("net_profit"), dict) else {}
            expectation = (
                f"{first_estimate.get('year', '—')}净利均值"
                f"{number(np_forecast.get('mean'), 2, '亿')}({first_estimate.get('coverage_count', 0)}家)"
            )
        valuation_text = (
            f"PE(TTM) {number(pe_ttm)}；PB {number(valuation.get('pb_mrq'))}；"
            f"远期PEG {number(valuation.get('peg_forward'))}；{expectation}"
        )
        trading = (
            f"20日 {number(indicators.get('return_20d_pct'), 1, '%')}；"
            f"10日资金 {amount_yi(flow_10d.get('main_net_inflow'))}"
        )
        risk_text = (
            f"规则风险{risks.get('item_count', len(risks.get('items') or []))}项；"
            f"公告覆盖至{(item.get('announcements') or {}).get('data_time') or '缺失'}"
        )
        rows.append(
            f"| {name} ({symbol}) | {business} | {financial} | {valuation_text} | "
            f"{trading} | {risk_text} | **{verdict}** |"
        )

        flags = item.get("screening_flags") if isinstance(item.get("screening_flags"), dict) else {}
        positives = "、".join(str(value) for value in flags.get("positive") or []) or "无明确正面信号"
        negatives = "、".join(str(value) for value in flags.get("negative") or []) or "未识别硬性负面信号"
        missing = "、".join(str(value) for value in coverage.get("missing") or []) or "无"
        details.append(
            f"- **{name} ({symbol})**：支持证据：{positives}。反证/风险：{negatives}。"
            f"数据源执行缺口：{missing}。主题业务订单/收入仍需公司级原文持续核验。"
            "成立条件：主营相关订单或收入可核验、盈利与估值匹配、"
            "交易状态止跌；失效条件：业绩/现金流继续恶化或风险事件升级。"
        )

    priority_order = (
        "可研究候选",
        "暂不介入（条件未满足）",
        "暂不买入",
        "风险规避",
        "数据源异常（证据链未完成）",
    )
    priority_lines = [
        f"- **{verdict}**：{'、'.join(priority_groups[verdict])}"
        for verdict in priority_order
        if priority_groups.get(verdict)
    ]

    return (
        f"## {heading}\n\n"
        f"{intro}结论是研究分层，不是收益承诺。\n\n"
        "| 公司/代码 | 主营与兑现基础 | 财务质量 | 估值与预期 | 交易与资金 | 公告/风险 | 当前结论 |\n"
        "|---|---|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n### 逐家公司成立条件与反证\n\n"
        + "\n".join(details)
        + "\n\n### 横向优先级\n\n"
        + ("\n".join(priority_lines) if priority_lines else "本轮没有可排序的公司证据。")
        + "\n\n### 组合层面的共同边界\n\n"
        "- 业务关联必须以财报收入、正式订单或客户验证为准；主营构成只能证明业务基础，不能自动证明主题收入。\n"
        "- 高估值不会单独终止分析，但需要更高的盈利增速和订单兑现来消化；资金流是成交单大小口径，不是机构持仓。\n"
        "- 数据口径："
        + str(batch.get("data_time") or "时间缺失")
        + "，"
        + str(batch.get("quote_basis") or "行情口径缺失")
        + "。完整来源包括公司资料、财报、主营构成、估值、一致预期、同行、公告、风险和资金流。"
    )


def _build_theme_mapping_fallback(
    result: Dict[str, Any],
    evidence: Optional[List[Dict[str, Any]]] = None,
    semantic_facts: Optional[List[BoundEvidenceFact]] = None,
    semantic_intent: Optional[ResearchIntent] = None,
) -> str:
    """Render company-level evidence plus the complete locally verified recall index."""
    selected_by_symbol: Dict[str, Dict[str, Any]] = {}
    boundary_by_symbol: Dict[str, Dict[str, Any]] = {}
    l3_markers = (
        "批量订单", "订单金额", "获得订单", "实现收入", "营业收入", "营收", "批量供货",
        "实现量产", "已量产", "量产交付", "批量交付", "批量落地", "收入占比",
    )
    l2_markers = (
        "送样", "定点", "客户验证", "关键验证", "性能测试", "小批量试制",
        "小批量供货", "客户测试", "客户认证", "成功切入", "加速放量",
    )
    progress_markers = (*l3_markers, *l2_markers)
    boundary_markers = (
        "不涉及", "暂无相关计划", "尚无相关计划", "收入规模较小", "营收规模较小",
        "收入占比较小", "营收占比较小",
        "尚未形成收入", "未形成收入", "未产生实质性业务", "未有产生实质性业务",
        "未产生实质性订单", "没有实质性订单",
    )
    robot_segment_markers = (
        "滚柱丝杠", "丝杠", "谐波减速器", "RV减速器", "减速器", "力矩传感器",
        "传感器", "伺服电机", "伺服", "空心杯电机", "电机", "执行器", "灵巧手",
        "机器视觉", "轴承", "编码器", "控制器", "整机", "系统集成",
    )
    ai_chip_segment_markers = (
        "训练芯片", "推理芯片", "AI加速芯片", "算力芯片", "GPU", "GPGPU", "NPU",
        "ASIC", "加速卡", "存算一体", "HBM", "先进封装", "Chiplet", "芯片设计",
    )
    theme = str(result.get("theme") or "").strip()
    if not theme:
        matched_board_names = [
            str(board.get("name") or "").strip()
            for board in result.get("matched_boards") or []
            if isinstance(board, dict) and board.get("name")
        ]
        item_board_names = [
            str(board).strip()
            for item in result.get("items") or []
            if isinstance(item, dict)
            for board in item.get("boards") or []
            if board
        ]
        theme = next((name for name in (*matched_board_names, *item_board_names) if name), "")
    lowered_theme = theme.lower()
    if any(marker in lowered_theme for marker in ("人形机器人", "具身智能", "机器人")):
        segment_markers = robot_segment_markers
        theme_markers = tuple(
            marker for marker in (theme, "人形机器人", "具身智能", *segment_markers) if marker
        )
    elif any(marker in lowered_theme for marker in ("ai芯片", "人工智能芯片", "算力芯片")):
        segment_markers = ai_chip_segment_markers
        theme_markers = tuple(
            marker
            for marker in (theme, "AI芯片", "人工智能芯片", "AI算力芯片", *segment_markers)
            if marker
        )
    else:
        # For other themes, require the requested topic itself.  Reusing the
        # humanoid-robot vocabulary here promoted unrelated robot news into
        # every theme's L2/L3 evidence table.
        segment_markers = ()
        theme_markers = (theme,) if theme else ()
    ambiguous_entity_names = {"机器人", "东方财富"}

    if semantic_facts is not None:
        for fact_model in semantic_facts:
            fact = fact_model.model_dump() if isinstance(fact_model, BoundEvidenceFact) else dict(fact_model)
            symbol = str(fact.get("symbol") or "")
            name = str(fact.get("company_name") or symbol)
            stage = str(fact.get("stage") or "")
            if not symbol or not name or stage == "L1":
                continue
            if (
                semantic_intent is not None
                and semantic_intent.selection_mode == "ranked_shortlist"
                and fact.get("thesis_fit") != "exact"
            ):
                continue
            rendered = {
                "symbol": symbol,
                "name": name,
                "segment": str(fact.get("relationship") or f"{theme}相关业务"),
                "level": "反证" if stage == "boundary" else stage,
                "fact": str(fact.get("fact") or fact.get("support_quote") or "").replace("|", "／")[:180],
                "gap": (
                    "反证或业务边界仍需用最新公告、财报与后续订单持续复核"
                    if stage == "boundary"
                    else (
                        "继续核验订单口径、持续性与主题收入占比"
                        if stage == "L3"
                        else "继续核验客户阶段、订单金额与收入贡献"
                    )
                ),
                "source_name": str(fact.get("source_name") or "公开资料"),
                "source_url": str(fact.get("source_url") or ""),
                "source_date": str(fact.get("source_date") or ""),
                "source_date_is_retrieval": bool(fact.get("source_date_is_retrieval")),
            }
            target = boundary_by_symbol if stage == "boundary" else selected_by_symbol
            previous = target.get(symbol)
            if previous is None or (previous.get("level") == "L2" and stage == "L3"):
                target[symbol] = rendered

    def source_date_for_item(source_item: Dict[str, Any], search_result: Dict[str, Any]) -> tuple[str, bool]:
        raw = str(
            source_item.get("published")
            or source_item.get("publish_date")
            or source_item.get("published_date")
            or source_item.get("content_time")
            or ""
        ).strip()
        direct_match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", raw)
        if direct_match:
            return "-".join(
                (direct_match.group(1), direct_match.group(2).zfill(2), direct_match.group(3).zfill(2))
            ), False
        source_text = "\n".join(
            str(source_item.get(key) or "")
            for key in ("title", "snippet", "summary", "content_text", "content")
        )
        for text_match in re.finditer(
            r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})",
            source_text[:1200],
        ):
            vicinity = source_text[max(0, text_match.start() - 24):text_match.end() + 24]
            if text_match.start() > 100 and not re.search(
                r"(?:发布|发布时间|更新|日期|来源|时间)",
                vicinity,
            ):
                continue
            return "-".join(
                (text_match.group(1), text_match.group(2).zfill(2), text_match.group(3).zfill(2))
            ), False
        url_match = re.search(r"/(20\d{2})(\d{2})(\d{2})(?:/|[^0-9])", str(source_item.get("url") or source_item.get("link") or ""))
        if url_match:
            return "-".join(url_match.groups()), False
        retrieved = str(search_result.get("retrieved_at") or "").strip()
        retrieved_match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", retrieved)
        if retrieved_match:
            return "-".join(retrieved_match.groups()), True
        return "", False

    def positive_markers(text: str, markers: tuple[str, ...]) -> List[str]:
        positives: List[str] = []
        for marker in markers:
            for match in re.finditer(re.escape(marker), text):
                vicinity = text[max(0, match.start() - 8):match.end() + 10]
                if marker == "批量供货" and text[max(0, match.start() - 1):match.start()] == "小":
                    continue
                if re.search(r"(?:未|无|没有|尚无|不涉及|未能|尚未).{0,8}" + re.escape(marker), vicinity):
                    continue
                if re.search(re.escape(marker) + r".{0,8}(?:未披露|不确定|不存在|为零)", vicinity):
                    continue
                positives.append(marker)
                break
        return positives

    for packet in evidence or []:
        # Semantic binding is the production path.  An empty list is a valid
        # extraction result and must not silently fall back to wording-based
        # promotion.  The legacy binder runs only when semantic extraction was
        # unavailable (``None``).
        if semantic_facts is not None:
            break
        if not isinstance(packet, dict) or packet.get("tool") not in {
            "search_financial_news", "search_research_library", "websearch",
        }:
            continue
        search_result = packet.get("result")
        if not isinstance(search_result, dict):
            continue
        source_items = search_result.get("items") or search_result.get("results") or []
        for source_item in source_items:
            if not isinstance(source_item, dict):
                continue
            title_text = str(source_item.get("title") or "")
            source_text = "\n".join(
                str(source_item.get(key) or "")
                for key in ("title", "summary", "snippet", "content_text", "content")
            )
            source_url = str(source_item.get("link") or source_item.get("url") or "").strip()
            source_date, date_is_retrieval = source_date_for_item(source_item, search_result)
            if not source_url or not source_date:
                continue
            title_entities = [
                entity
                for entity in find_securities_in_text(title_text, limit=5)
                if str(entity.get("name") or "") not in ambiguous_entity_names
            ]
            title_is_theme_relevant = any(marker in title_text for marker in theme_markers)
            source_sentences = [
                sentence.strip()
                for sentence in re.split(r"(?<=[。！？!?；;])|\n+", source_text)
                if sentence.strip()
            ]
            for window in source_sentences:
                matched_boundary = next(
                    (marker for marker in boundary_markers if marker in window),
                    None,
                )
                if not matched_boundary or not any(marker in window for marker in theme_markers):
                    continue
                window_entities = [
                    entity
                    for entity in find_securities_in_text(window, limit=10)
                    if str(entity.get("name") or "") not in ambiguous_entity_names
                ]
                promotable_entities = window_entities or (
                    title_entities if len(title_entities) == 1 and title_is_theme_relevant else []
                )
                fact = re.sub(r"\s+", " ", window).replace("|", "／").strip()
                if len(fact) > 120:
                    fact = fact[:119] + "…"
                for entity in promotable_entities:
                    symbol = str(entity.get("symbol") or "")
                    entity_name = str(entity.get("name") or "")
                    if not symbol or entity_name in ambiguous_entity_names:
                        continue
                    boundary_by_symbol[symbol] = {
                        "symbol": symbol,
                        "name": entity_name or symbol,
                        "segment": "主题业务边界",
                        "level": "反证",
                        "fact": fact,
                        "gap": "反证或业务边界仍需用最新公告、财报与后续订单持续复核",
                        "source_name": source_item.get("source") or source_item.get("author") or "公司级资料",
                        "source_url": source_url,
                        "source_date": source_date[:10],
                        "source_date_is_retrieval": date_is_retrieval,
                    }
            evidence_windows = [
                sentence.strip()
                for sentence in source_sentences
                if any(marker in sentence for marker in progress_markers)
            ]
            for window in evidence_windows:
                if any(marker in window for marker in boundary_markers):
                    continue
                matched_l3 = positive_markers(window, l3_markers)
                matched_l2 = positive_markers(window, l2_markers)
                window_is_theme_relevant = any(marker in window for marker in theme_markers)
                # Revenue/order language is only L3 when the same evidence
                # sentence explicitly binds it to the requested theme or a
                # named chain segment.  A robot-themed title followed by the
                # company's unrelated total revenue (for example bathrooms or
                # textiles) must not be promoted as robot revenue.
                if matched_l3 and not window_is_theme_relevant:
                    matched_l3 = []
                matched_markers = matched_l3 or matched_l2
                if not matched_markers:
                    continue
                level = "L3" if matched_l3 else "L2"
                window_entities = [
                    entity
                    for entity in find_securities_in_text(window, limit=10)
                    if str(entity.get("name") or "") not in ambiguous_entity_names
                ]
                if window_is_theme_relevant and window_entities:
                    promotable_entities = window_entities
                elif (
                    not window_entities
                    and (window_is_theme_relevant or title_is_theme_relevant)
                    and len(title_entities) == 1
                    and re.search(r"(?:^|[，,；;])公司|该公司|公司称|公司表示|其产品|该产品", window)
                ):
                    # 新闻标题明确绑定唯一公司与主题/环节时，允许把紧随其后的
                    # “该公司/产品/客户”进展句回指到标题公司。不能用整篇文本窗口，
                    # 否则会把文章其他章节出现的公司错误升级为 L2。
                    promotable_entities = title_entities
                else:
                    promotable_entities = []
                for entity in promotable_entities:
                    symbol = str(entity.get("symbol") or "")
                    entity_name = str(entity.get("name") or "")
                    if not symbol or entity_name in ambiguous_entity_names:
                        continue
                    segment = next(
                        (marker for marker in segment_markers if marker in window or marker in title_text),
                        f"{theme}相关业务" if theme and theme in title_text else "待公司级业务核验",
                    )
                    evidence_sentence = re.sub(r"\s+", " ", window).replace("|", "／").strip()
                    if len(evidence_sentence) > 120:
                        evidence_sentence = evidence_sentence[:119] + "…"
                    candidate = {
                        "symbol": symbol,
                        "name": entity_name or symbol,
                        "segment": segment,
                        "level": level,
                        "fact": evidence_sentence or ("本轮公司级资料明确出现" + "、".join(matched_markers[:3]) + "等进展表述"),
                        "gap": (
                            "具体订单口径、持续性与主题收入占比仍需回到公告或财报原文核验"
                            if level == "L3"
                            else "具体客户、验证阶段、订单金额与收入贡献仍需回到公告或财报原文核验"
                        ),
                        "source_name": source_item.get("source") or source_item.get("author") or "公司级资料",
                        "source_url": source_url,
                        "source_date": source_date[:10],
                        "source_date_is_retrieval": date_is_retrieval,
                    }
                    previous = selected_by_symbol.get(symbol)
                    if previous is None or (previous.get("level") == "L2" and level == "L3"):
                        selected_by_symbol[symbol] = candidate

    candidates = [item for item in result.get("items") or [] if isinstance(item, dict)]
    if not candidates:
        return "主题候选池未取得足够的本地证券库交叉结果，本轮不能可靠生成公司名单。"

    selected = sorted(
        selected_by_symbol.values(),
        key=lambda item: (item.get("level") != "L3", item.get("segment") or "", item.get("symbol") or ""),
    )
    boundaries = sorted(
        boundary_by_symbol.values(),
        key=lambda item: (item.get("name") or "", item.get("symbol") or ""),
    )
    def render_evidence_rows(items: List[Dict[str, Any]]) -> List[str]:
        rows: List[str] = []
        for item in items:
            link = (
                f"[{item['source_name']}]({item['source_url']})"
                if item.get("source_url") else str(item.get("source_name") or "来源缺失")
            )
            date_label = (
                f"检索于 {item['source_date']}（原页未标日期）"
                if item.get("source_date_is_retrieval")
                else item["source_date"]
            )
            rows.append(
                f"| {item['name']} ({item['symbol']}) | {item['segment']} | {item['level']} | {item['fact']} | "
                f"{item['gap']} | {link}，{date_label} |"
            )
        return rows

    selected_rows = render_evidence_rows(selected)
    boundary_rows = render_evidence_rows(boundaries)

    evidence_source_lines: List[str] = []
    successful_evidence_tools: set[str] = set()
    for packet in evidence or []:
        if not isinstance(packet, dict):
            continue
        tool_name = str(packet.get("tool") or "")
        search_result = packet.get("result")
        if not isinstance(search_result, dict) or tool_name not in {
            "search_financial_news", "search_research_library", "websearch",
        }:
            continue
        if search_result.get("success") is not False:
            successful_evidence_tools.add(tool_name)
        if tool_name == "search_financial_news":
            evidence_source_lines.append(
                "- RSS/财经资讯："
                f"尝试 {search_result.get('attempted_route_count') or 0} 条路由，"
                f"成功 {search_result.get('successful_route_count') or 0} 条，"
                f"返回 {search_result.get('item_count') or 0} 条。"
            )
        elif tool_name == "search_research_library":
            coverage_rows = search_result.get("source_coverage") or []
            successful_sources = sum(
                1 for row in coverage_rows
                if isinstance(row, dict) and row.get("success")
            )
            evidence_source_lines.append(
                "- 跨机构研报："
                f"尝试 {len(coverage_rows)} 个资料源，成功 {successful_sources} 个，"
                f"返回 {search_result.get('item_count') or 0} 篇。"
            )
        else:
            evidence_source_lines.append(
                "- 网页搜索与正文爬取："
                f"搜索引擎 `{search_result.get('provider') or 'unknown'}` 返回 "
                f"{search_result.get('result_count') or 0} 条，成功抓取正文 "
                f"{search_result.get('content_result_count') or 0} 页。"
            )

    evidence_chain_complete = {
        "search_financial_news", "search_research_library", "websearch",
    }.issubset(successful_evidence_tools)

    grouped: Dict[str, List[str]] = {}
    for item in candidates:
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        if not re.fullmatch(r"\d{6}", symbol) or not name:
            continue
        boards = [str(value) for value in item.get("boards") or [] if value]
        group = boards[0] if boards else "其他主题候选"
        grouped.setdefault(group, []).append(f"{name} ({symbol})")

    inventory_lines: List[str] = []
    for group, entries in grouped.items():
        for index in range(0, len(entries), 20):
            chunk = "、".join(entries[index:index + 20])
            label = f"**{group}**" if index == 0 else f"**{group}（续）**"
            inventory_lines.append(f"- {label}：{chunk}")

    source_lines: List[str] = []
    for board in result.get("matched_boards") or []:
        if not isinstance(board, dict):
            continue
        source_name = str(board.get("source") or "概念板块")
        board_name = str(board.get("name") or "主题板块")
        source_url = str(board.get("url") or "").strip()
        coverage = str(board.get("coverage") or "unknown")
        count = board.get("constituent_count") or board.get("returned_count") or "未知"
        label = f"[{source_name} · {board_name}]({source_url})" if source_url else f"{source_name} · {board_name}"
        source_lines.append(
            f"- {label}：{count} 家，覆盖状态 `{coverage}`，抓取日期 {result.get('data_time') or '日期缺失'}。"
        )

    coverage_complete = bool(result.get("coverage_complete")) and not int(result.get("omitted_count") or 0)
    coverage_text = (
        "至少一个精确主题源已完成全分页抓取，跨来源差异与其他来源覆盖限制见下方"
        if coverage_complete
        else "精确主题源仍有失败页或返回上限，以下仅是本轮完整返回集，不代表主题全量"
    )
    company_evidence = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 仍需核验 | 来源日期 |\n"
        "|---|---|---|---|---|---|\n"
        + "\n".join(selected_rows)
        if selected_rows
        else (
            "本轮已执行 RSS、研报、网页搜索与正文爬取，但没有检出满足 L2/L3 定义的正向公司级事实；"
            "这是证据分级结果，不是‘数据源没有数据’。"
        )
    )
    boundary_evidence = (
        "| 公司/代码 | 产业链环节 | 类型 | 已验证事实 | 仍需核验 | 来源日期 |\n"
        "|---|---|---|---|---|---|\n"
        + "\n".join(boundary_rows)
        if boundary_rows
        else "本轮未检出公司明确否认、尚未形成收入或收入占比较小等反证。"
    )
    if semantic_intent is not None and semantic_intent.selection_mode == "ranked_shortlist":
        requirements = "、".join(semantic_intent.thesis_requirements) or semantic_intent.objective
        shortlist = (
            "| 排名 | 公司/代码 | 精确匹配环节 | 兑现等级 | 已验证事实 | 仍需核验 | 来源日期 |\n"
            "|---|---|---|---|---|---|---|\n"
            + "\n".join(
                row.replace("| ", f"| {index} | ", 1)
                for index, row in enumerate(selected_rows, 1)
            )
            if selected_rows
            else (
                "本轮没有公司同时满足全部命题条件，因此不能从宽泛概念池中强行选出‘最符合’公司。"
            )
        )
        return (
            "## 与投资命题精确匹配的 A 股短名单\n\n"
            f"> 必须同时满足：{requirements}。宽泛主题池召回 **{len(candidates)} 家**，"
            "但概念成员关系不参与最终排名。\n\n"
            + shortlist
            + "\n\n### 结论边界\n\n"
            + (
                f"本轮仅有 **{len(selected)} 家**取得与命题精确匹配的 L2/L3 公司级证据；"
                "排名按兑现等级优先，不把服务器、机器人、通用边缘计算等相邻场景冒充消费终端兑现。"
                if selected
                else "当前证据不足以形成可信短名单；应继续逐家公司补充同口径订单、批量交付和收入证据。"
            )
            + "\n\n### 召回与检索覆盖\n\n"
            + ("\n".join(source_lines) if source_lines else "候选来源明细缺失。")
            + "\n\n"
            + ("\n".join(evidence_source_lines) if evidence_source_lines else "没有取得证据源执行记录。")
        )
    return (
        "## 产业主题 A 股候选公司\n\n"
        f"> 已从本地 **{result.get('local_universe_count') or '全量'} 只**证券中交叉核验代码；"
        f"主题候选池共 **{result.get('candidate_count') or len(candidates)} 家**，本轮返回 "
        f"**{len(candidates)} 家**；{coverage_text}。\n\n"
        "### 已核验公司级业务进展\n\n"
        + company_evidence
        + "\n\n### 已核验反证与业务边界\n\n"
        + boundary_evidence
        + f"\n\n### 完整候选池（L1，共 {len(candidates)} 家）\n\n"
        "> 下列公司只证明主题板块成员关系与证券代码有效，不自动分配产业链环节，也不等同订单或收入兑现。\n\n"
        + "\n".join(inventory_lines)
        + "\n\n### 候选来源与覆盖\n\n"
        + ("\n".join(source_lines) if source_lines else "候选来源明细缺失。")
        + "\n\n### 公司证据检索覆盖\n\n"
        + (
            "证据链已完整执行。\n\n"
            if evidence_chain_complete
            else "证据链存在来源失败，不能据此声称公开资料不足。\n\n"
        )
        + ("\n".join(evidence_source_lines) if evidence_source_lines else "没有取得证据源执行记录。")
        + "\n\n### 证据口径\n\n"
        f"本轮确认 **{len(selected)} 家** L2/L3 正向业务进展，另确认 **{len(boundaries)} 家**反证或业务边界；"
        "其余统一保留为 L1 候选。L1 表示本轮多源主题检索尚未出现满足升级标准的公司级事实，"
        "不表示数据源为空，也不允许把概念关系写成订单或收入。"
    )


def _build_verified_evidence_fallback(
    evidence: Optional[List[Dict[str, Any]]],
    *,
    professional_decision_requested: Optional[bool] = None,
    semantic_facts: Optional[List[BoundEvidenceFact]] = None,
    semantic_intent: Optional[ResearchIntent] = None,
) -> str:
    """Return a complete deterministic answer when final model text is empty.

    A provider can occasionally finish a streaming request without emitting a
    text delta.  Dropping the already verified tool result leaves the user with
    a blank assistant turn.  For the high-value multi-stock path we can still
    provide a compact, auditable screen directly from the batch payload without
    inventing business facts or pretending this mechanical screen is advice.
    """
    batch: Optional[Dict[str, Any]] = None
    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_theme_stock_candidates":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            return _build_theme_mapping_fallback(
                result,
                evidence,
                semantic_facts=semantic_facts,
                semantic_intent=semantic_intent,
            )

    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_multi_stock_decision_evidence":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            answer = _build_professional_decision_fallback(
                result,
                decision_requested=professional_decision_requested,
            )
            market = next(
                (
                    packet.get("result")
                    for packet in evidence or []
                    if isinstance(packet, dict)
                    and packet.get("tool") == "get_market_breadth"
                    and isinstance(packet.get("result"), dict)
                    and packet["result"].get("success") is not False
                ),
                None,
            )
            if isinstance(market, dict):
                answer += (
                    "\n\n### 市场宽度\n\n"
                    f"- 上涨 **{market.get('up_count', '缺失')}** 家，下跌 **{market.get('down_count', '缺失')}** 家，"
                    f"涨跌比 **{market.get('advance_decline_ratio', '缺失')}**；"
                    f"成交额 **{market.get('total_amount', '缺失')} {market.get('total_amount_unit') or ''}**。\n"
                    f"- 数据时间：{market.get('data_time') or market.get('market_date') or '缺失'}；"
                    "市场宽度只用于判断介入环境，不改变单家公司基本面结论。"
                )
            return answer

    if professional_decision_requested is not None:
        return (
            "## 专业决策证据未完成\n\n"
            "本轮未成功取得覆盖全部公司的专业决策证据，因此停止买入判断。"
            "请重试本轮查询；在专业证据工具成功前，不输出买入、持有或卖出结论。"
        )

    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_multi_stock_snapshot":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            batch = result
            break

    if not batch or not isinstance(batch.get("items"), list):
        return (
            "已取得工具证据，但模型本次没有返回最终文本。为避免编造结论，本轮不补写未经"
            "核验的判断；请直接重试当前问题，已取得的证据仍保留在工具卡片中。"
        )

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "缺失"

    rows: List[str] = []
    stale_names: List[str] = []
    for item in batch["items"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("symbol") or "未知")
        symbol = str(item.get("symbol") or "—")
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        financial = item.get("financial") if isinstance(item.get("financial"), dict) else {}
        technical = item.get("technical") if isinstance(item.get("technical"), dict) else {}
        pe = quote.get("pe_dynamic")
        debt = financial.get("debt_ratio_pct")
        profit = financial.get("net_profit")
        if technical.get("is_stale"):
            stale_names.append(f"{name}({symbol})")

        try:
            pe_value = float(pe)
        except (TypeError, ValueError):
            pe_value = 0.0
        try:
            debt_value = float(debt)
        except (TypeError, ValueError):
            debt_value = 0.0
        try:
            profit_value = float(profit)
        except (TypeError, ValueError):
            profit_value = 0.0

        if profit_value < 0 or pe_value <= 0:
            screen = "亏损，先观察"
        elif debt_value >= 70:
            screen = "高负债，先观察"
        elif pe_value >= 100:
            screen = "估值高，等业绩兑现"
        elif pe_value >= 60:
            screen = "估值偏高，谨慎观察"
        else:
            screen = "先核验业务兑现"
        rows.append(
            f"| {name} ({symbol}) | {number(quote.get('price'))} / "
            f"{number(quote.get('change_pct'))}% | {number(pe)} / "
            f"{number(quote.get('pb_ratio'))} | {screen} |"
        )

    warning = ""
    if stale_names:
        warning = "\n- 技术数据陈旧：" + "、".join(stale_names) + "，未据此作判断。"
    warnings = batch.get("warnings")
    warning_text = "；".join(str(value) for value in warnings or [] if value)
    if warning_text and not warning:
        warning = f"\n- 数据警示：{warning_text}。"

    basis = str(batch.get("quote_basis") or "最新行情快照")
    data_time = str(batch.get("data_time") or "时间缺失")
    return (
        "## 当前判断\n\n"
        "**不适合把这组公司整体追买。** 下表只是基于本轮已核验行情、动态 PE/PB 与最新"
        "报告期财务的机械初筛，不代替逐家公司核验本轮投资逻辑相关订单和收入。\n\n"
        "| 公司/代码 | 最新价/涨跌 | 动态PE/PB | 初筛 |\n"
        "|---|---:|---:|---|\n"
        + "\n".join(rows)
        + "\n\n- 成立条件：相关业务出现可核验订单或收入，同时估值与盈利增速匹配。"
        "\n- 失效条件：持续亏损、现金流或负债恶化，或业务仍停留在概念/送样阶段。"
        f"{warning}\n- 数据口径：{data_time}，{basis}；PE 为动态市盈率，不是 PE(TTM)。"
    )


def _build_realtime_quote_answer(
    evidence: Optional[List[Dict[str, Any]]],
    user_text: str = "",
) -> str:
    """Render quote-only turns without letting synthesis invent market context."""
    normalized_user = str(user_text or "").strip()
    if normalized_user:
        if re.search(r"分析|趋势|技术|估值|财务|能买|可以买|能买吗|比较|原因|为什么", normalized_user):
            return ""
        if not re.search(r"现在行情|最新行情|最新价|股价|报价|多少钱", normalized_user):
            return ""
    packets = [item for item in evidence or [] if isinstance(item, dict)]
    quote_only_support_tools = {
        "get_realtime_quotes", "get_market_status", "get_market_breadth",
    }
    if not packets or any(item.get("tool") not in quote_only_support_tools for item in packets):
        return ""
    results = [
        item.get("result")
        for item in packets
        if item.get("tool") == "get_realtime_quotes" and isinstance(item.get("result"), dict)
    ]
    if not results:
        return ""
    result = results[-1]
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    if not items:
        return ""

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    rows: List[str] = []
    for item in items:
        symbol = str(item.get("symbol") or item.get("code") or "")
        name = str(item.get("name") or symbol)
        pct = number(item.get("pct_chg", item.get("change_pct")))
        rows.append(
            f"| {name} ({symbol}) | {number(item.get('price'))} 元 | {pct}% | "
            f"{number(item.get('high'))} / {number(item.get('low'))} | "
            f"{number(item.get('amount'), 0)} 元 |"
        )

    quote_mode = str(result.get("quote_mode") or "")
    is_live = quote_mode == "live" or result.get("is_trading_session") is True
    mode_label = str(result.get("quote_mode_label") or "").strip() or (
        "交易时段实时行情" if is_live else "最近交易日行情快照"
    )
    data_time = str(result.get("data_time") or "时间未知").replace("T", " ")
    sources = result.get("source") or []
    source_text = "、".join(map(str, sources)) if isinstance(sources, list) else str(sources)
    stale_note = "；数据已陈旧，请勿据此判断当前价格" if result.get("is_stale") is True else ""
    return (
        "## 最新行情\n\n"
        "| 股票 | 最新价 | 涨跌幅 | 最高 / 最低 | 成交额 |\n"
        "|---|---:|---:|---:|---:|\n"
        + "\n".join(rows)
        + f"\n\n- 数据时间：{data_time}\n"
        + f"- 行情口径：{mode_label}{stale_note}\n"
        + f"- 数据来源：{source_text or '行情工具返回来源'}\n"
        + ("" if is_live else "- 当前为非交易时段；以上不是当前时刻的实时成交，也不等同于收盘价。\n")
    )


def _build_quantitative_screen_answer(evidence: Optional[List[Dict[str, Any]]]) -> str:
    """Render a validated screen verbatim without changing its conditions."""
    results = [
        item.get("result")
        for item in evidence or []
        if isinstance(item, dict)
        and item.get("tool") == "screen_atr_volatility_stocks"
        and isinstance(item.get("result"), dict)
    ]
    if not results:
        return "## 筛选未完成\n\n量化筛选工具没有返回结构化结果，因此本轮不输出股票结论。"
    result = results[-1]
    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
    coverage_parts = [f"本轮股票范围 {coverage.get('universe', '—')} 只"]
    if coverage.get("history_preexcluded") is not None:
        coverage_parts.append(f"上市历史确定不足预排除 {coverage.get('history_preexcluded')} 只")
    if coverage.get("financial_covered") is not None:
        coverage_parts.append(f"必需财务字段覆盖 {coverage.get('financial_covered')} 只")
    if coverage.get("financial_eligible") is not None:
        coverage_parts.append(f"财务条件后候选 {coverage.get('financial_eligible')} 只")
    if coverage.get("fresh_kline") is not None:
        coverage_parts.append(f"取得行情 {coverage.get('fresh_kline')} 只")
    fallback_count = int(coverage.get("financial_fallback_count") or 0)
    cache_count = int(coverage.get("financial_cache_count") or 0)
    if cache_count:
        coverage_parts.append(f"本日财务快照接管 {cache_count} 只")
    if fallback_count:
        coverage_parts.append(f"财务补源 {fallback_count} 只")
    coverage_text = "；".join(coverage_parts) + "。"
    if result.get("success") is not True:
        errors = [str(item) for item in result.get("errors") or [] if item]
        failed = [str(item) for item in result.get("failed_symbols") or [] if item]
        detail = "\n".join(f"- {item}" for item in errors) or "- 工具没有提供失败原因。"
        failed_text = ("\n- 失败样例：" + "；".join(failed[:10])) if failed else ""
        stage = str(result.get("failure_stage") or "unknown")
        return (
            "## 筛选未完成\n\n"
            "**本轮不输出任何股票结论。** 条件、数据刷新或全市场覆盖没有通过硬校验。\n\n"
            f"- 失败阶段：`{stage}`\n{detail}{failed_text}\n\n- 覆盖校验：{coverage_text}"
        )

    screen_spec = result.get("screen_spec")
    columns = result.get("columns")
    applied_rules = result.get("applied_rules")
    if (
        not isinstance(screen_spec, dict)
        or not isinstance(columns, list)
        or not columns
        or not isinstance(applied_rules, list)
        or not applied_rules
        or coverage.get("complete") is not True
    ):
        return (
            "## 筛选未完成\n\n**本轮不输出任何股票结论。** 工具虽返回成功，"
            "但缺少实际执行规格、动态列定义、规则回显或完整覆盖证明。"
        )

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    def format_value(value: Any, format_name: str) -> str:
        if value is None:
            return "—"
        if format_name == "currency_yuan":
            try:
                return f"{float(value) / 100_000_000:,.2f}亿"
            except (TypeError, ValueError):
                return "—"
        if format_name == "percent":
            return number(value) + "%"
        if format_name == "integer":
            try:
                return f"{int(value):,}"
            except (TypeError, ValueError):
                return "—"
        return str(value)

    normalized_columns: List[Dict[str, str]] = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        field = str(column.get("field") or "").strip()
        label = str(column.get("label") or "").strip()
        if field and label:
            normalized_columns.append({
                "field": field,
                "label": label,
                "format": str(column.get("format") or "text"),
            })
    if not normalized_columns:
        return "## 筛选未完成\n\n工具返回的结果列合同无效，本轮不输出股票结论。"

    preview_items = result.get("items") or []
    for item in preview_items:
        if not isinstance(item, dict) or any(
            column["field"] not in item for column in normalized_columns
        ):
            return (
                "## 筛选未完成\n\n工具返回的预览行缺少请求字段，"
                "结果合同不完整，因此本轮不输出股票结论。"
            )

    rows: List[str] = []
    for item in preview_items:
        cells = [
            format_value(item.get(column["field"]), column["format"])
            for column in normalized_columns
        ]
        rows.append("| " + " | ".join(cells) + " |")
    total = int(result.get("total") or 0)
    preview_limit = int(screen_spec.get("preview_limit") or 10)
    download_url = str(result.get("download_url") or "").strip()
    if total > preview_limit and not download_url:
        return (
            "## 筛选未完成\n\n完整结果超过页面预览上限，但工具没有生成下载文件；"
            "为避免交付不完整名单，本轮不输出股票结论。"
        )
    if rows:
        table = (
            "| " + " | ".join(column["label"] for column in normalized_columns) + " |\n"
            "| " + " | ".join("---" for _ in normalized_columns) + " |\n"
            + "\n".join(rows)
        )
    else:
        table = "完整执行本轮全部条件后，合格股票为 **0 只**。"
    sort_spec = screen_spec.get("sort") if isinstance(screen_spec.get("sort"), dict) else {}
    sort_text = f"{sort_spec.get('field', '工具指定字段')} {sort_spec.get('order', '—')}"
    download = f"\n\n[下载完整 {total} 只筛选结果（CSV）]({download_url})" if download_url else ""
    rules_text = "\n".join(f"- {item}" for item in applied_rules if str(item).strip())
    fingerprint = str(result.get("spec_fingerprint") or "—")
    data_times = result.get("data_times") if isinstance(result.get("data_times"), dict) else {}
    kline_time = str(data_times.get("kline_expected_date") or "").strip()
    financial_period = str(
        data_times.get("financial_report_period")
        or result.get("financial_report_period")
        or ""
    ).strip()
    time_parts: List[str] = []
    if kline_time:
        time_parts.append(f"行情刷新基准日 {kline_time}")
    if financial_period:
        time_parts.append(f"主财务报告期 {financial_period}")
    if not time_parts:
        time_parts.append(f"数据日期 {result.get('data_time', '—')}")
    warnings = [str(item) for item in result.get("warnings") or [] if item]
    warnings_text = ""
    if warnings:
        warnings_text = "\n- 数据源切换：" + "；".join(warnings)
    return (
        f"## 筛选结论\n\n共 **{total} 只**股票满足本轮完整规格，排序为 `{sort_text}`。"
        + (f"下表展示前{preview_limit}只。\n\n" if total > preview_limit else "\n\n")
        + table
        + download
        + "\n\n### 本轮实际执行规格\n\n"
        + rules_text
        + f"\n- 规格指纹：`{fingerprint}`"
        + "\n\n### 数据与覆盖\n\n"
        + f"- 数据日期：{'；'.join(time_parts)}。\n"
        + f"- 覆盖：{coverage_text}\n"
        + f"- 来源：{result.get('source', '工具返回来源')}。"
        + warnings_text
    )


def _build_atr_screen_answer(evidence: Optional[List[Dict[str, Any]]]) -> str:
    """Compatibility alias for existing imports while the tool keeps its name."""
    return _build_quantitative_screen_answer(evidence)


def _unsupported_final_claims(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Catch high-risk claims whose required evidence dimension is absent."""
    tool_names = {
        str(item.get("tool") or "")
        for item in evidence or []
        if isinstance(item, dict)
    }
    reasons: List[str] = []
    has_flow_evidence = (
        any("flow" in name for name in tool_names)
        or "get_multi_stock_decision_evidence" in tool_names
    )
    if not has_flow_evidence and re.search(
        r"(?:主力资金|资金(?:仍在|持续|明显|大幅)?(?:净)?流(?:入|出))",
        content,
    ):
        reasons.append("资金流结论没有资金流工具证据")

    has_channel_price_evidence = bool(tool_names.intersection({
        "search_news", "search_financial_news", "search_research_library", "websearch", "webfetch",
    }))
    if not has_channel_price_evidence and re.search(r"(?:一)?批价", content):
        reasons.append("白酒批价或阈值没有新闻、公告或网页证据")

    has_market_evidence = bool(
        tool_names.intersection({"get_market_status", "get_market_breadth", "get_index_data"})
    )
    if not has_market_evidence and re.search(
        r"(?:上证指数|深证成指|创业板指)[^。\n]{0,30}\d+(?:\.\d+)?%|"
        r"(?:上涨|下跌)(?:个股)?\s*\d+\s*家|"
        r"(?:两市|沪深两市)成交额[^。\n]{0,20}\d|"
        r"抗跌(?:性)?|跑赢大盘|弱于大盘",
        content,
    ):
        reasons.append("大盘涨跌、市场宽度或相对强弱结论没有市场工具证据")

    weekday_labels = "一二三四五六日"
    for match in re.finditer(
        r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?\s*[（(]周([一二三四五六日天])[）)]",
        content,
    ):
        try:
            stated_date = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            ).date()
        except ValueError:
            continue
        stated_weekday = "日" if match.group(4) == "天" else match.group(4)
        actual_weekday = weekday_labels[stated_date.weekday()]
        if stated_weekday != actual_weekday:
            reasons.append(
                f"日期星期不一致：{stated_date.isoformat()} 应为周{actual_weekday}"
            )

    market_snapshot_tools = {
        "get_realtime_quotes", "get_kline", "get_history_data",
        "get_technical_indicators", "get_market_status", "get_market_breadth",
        "get_index_data", "get_multi_stock_snapshot",
    }
    if tool_names and tool_names.issubset(market_snapshot_tools) and re.search(r"今日|当天", content):
        evidence_dates = []
        for packet in evidence or []:
            result = packet.get("result") if isinstance(packet, dict) else None
            if not isinstance(result, dict):
                continue
            for raw_date in (result.get("data_time"), result.get("latest_trade_date")):
                match = re.search(r"20\d{2}-\d{2}-\d{2}", str(raw_date or ""))
                if match:
                    try:
                        evidence_dates.append(datetime.fromisoformat(match.group(0)).date())
                    except ValueError:
                        pass
        if evidence_dates and max(evidence_dates) < datetime.now().astimezone().date():
            reasons.append("行情证据全部来自之前的交易日，不能称为今日或当天数据")

    # Eastmoney single-quarter statements label Q4 as ``flow_basis=single_quarter``.
    # A model must not silently promote that Q4 cash flow into a full-year value.
    for packet in evidence or []:
        result = packet.get("result") if isinstance(packet, dict) else None
        if not isinstance(result, dict):
            continue
        for company in result.get("items") or []:
            if not isinstance(company, dict):
                continue
            financials = company.get("financials")
            if not isinstance(financials, dict):
                continue
            for period in financials.get("items") or []:
                if not isinstance(period, dict):
                    continue
                if period.get("flow_basis") != "single_quarter" or not str(period.get("report_period") or "").endswith("Q4"):
                    continue
                cash_flow = period.get("operating_cash_flow")
                try:
                    amount_yi = f"{float(cash_flow) / 100000000:.2f}"
                except (TypeError, ValueError):
                    continue
                if re.search(
                    rf"(?:全年|年度)[^。\n]{{0,24}}(?:经营现金流|OCF)[^。\n]{{0,16}}{re.escape(amount_yi)}|"
                    rf"(?:经营现金流|OCF)[^。\n]{{0,16}}{re.escape(amount_yi)}[^。\n]{{0,24}}(?:全年|年度)",
                    content,
                    flags=re.I,
                ):
                    reasons.append(
                        f"{period.get('report_period')} 经营现金流是单季度值，不能写成全年数据"
                    )

    batch_results = [
        item.get("result")
        for item in evidence or []
        if isinstance(item, dict)
        and item.get("tool") in {
            "get_multi_stock_snapshot",
            "get_multi_stock_decision_evidence",
        }
        and isinstance(item.get("result"), dict)
    ]
    only_simple_snapshots = batch_results and all(
        result.get("playbook") != "professional_investment_decision"
        for result in batch_results
    )
    has_structured_valuation = "get_valuation_ratios" in tool_names
    only_dynamic_quote_pe = (
        "get_realtime_quotes" in tool_names or bool(only_simple_snapshots)
    ) and not has_structured_valuation
    if only_dynamic_quote_pe and "PE(TTM)" in content:
        reasons.append("实时行情或批量快照只提供动态 PE，不能写成 PE(TTM)")
    if any(result.get("quote_is_intraday") for result in batch_results):
        cleaned = content.replace("不是收盘价", "")
        if re.search(r"(?:今日|当日|截至[^，。；]{0,12})?收盘价", cleaned):
            reasons.append("盘中快照不能写成收盘价")
    return reasons


def _professional_answer_contract_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Prove that a professional decision answer covered every required axis."""
    result = next(
        (
            item.get("result")
            for item in evidence or []
            if isinstance(item, dict)
            and item.get("tool") == "get_multi_stock_decision_evidence"
            and isinstance(item.get("result"), dict)
            and item["result"].get("success") is not False
        ),
        None,
    )
    if not isinstance(result, dict):
        # A buy/hold/sell answer without the comprehensive evidence packet is
        # not allowed to fall back to model memory.  It may only fail closed
        # and explain that no decision can be made yet.
        failure_markers = ("证据不足", "证据缺失", "无法完成", "无法确认", "暂不做买入判断", "不提供买入结论")
        if any(marker in content for marker in failure_markers):
            return []
        return ["专业决策证据未成功取得，必须停止买入判断并说明证据缺口"]
    issues: List[str] = []
    for entity in result.get("resolved_entities") or []:
        if isinstance(entity, dict) and str(entity.get("symbol") or "") not in content:
            issues.append(f"遗漏公司 {entity.get('name') or entity.get('symbol')}")
    required_groups = {
        "业务兑现": ("业务", "主营", "兑现"),
        "财务质量": ("财务", "营收", "净利", "现金流"),
        "估值预期": ("估值", "PE", "PEG", "一致预期"),
        "交易状态": ("交易", "趋势", "技术", "资金"),
        "风险催化": ("风险", "公告", "催化"),
        "决策边界": (
            "成立条件", "失效条件", "等待验证", "暂不介入", "暂不买入", "风险规避",
        ),
    }
    for label, markers in required_groups.items():
        if not any(marker in content for marker in markers):
            issues.append(f"缺少{label}")
    return issues


def _sanitize_mapping_answer(content: str) -> str:
    """Drop unsupported company rows while preserving a valid mapping report.

    A model may include five properly sourced companies and one remembered
    concept stock.  Rejecting the whole report wastes good evidence; keeping
    the bad row violates the Playbook.  This deterministic pass removes only
    rows that fail code/name/source/date verification and records the omission.
    """
    lines = content.splitlines()
    in_company_table = False
    valid_row_count = 0
    removed: List[str] = []
    kept: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_company_table = False
            kept.append(line)
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            kept.append(line)
            continue
        if "公司" in cells[0] and "代码" in cells[0]:
            in_company_table = True
            kept.append(line)
            continue
        if not in_company_table or re.fullmatch(r"[:\- ]+", cells[0] or ""):
            kept.append(line)
            continue

        first_cell = cells[0]
        code_match = re.search(r"(?<!\d)(\d{6})(?!\d)", first_cell)
        resolved = find_securities_in_text(first_cell, limit=5) if code_match else []
        entity_matches = bool(
            code_match
            and any(item.get("symbol") == code_match.group(1) for item in resolved)
        )
        has_source = "http://" in stripped or "https://" in stripped
        has_date = bool(re.search(r"20\d{2}[-年/.]\d{1,2}", stripped))
        if entity_matches and has_source and has_date and "代码待核验" not in first_cell:
            kept.append(line)
            valid_row_count += 1
            continue
        removed.append(re.sub(r"[*_`]", "", first_cell).strip())

    if valid_row_count == 0:
        return content
    # Once rows are removed, any model-written count summary can become false
    # (for example “2 L2 + 2 L1” above a one-row table). Replace it with the
    # count proven by the surviving rows.
    normalized = [
        line
        for line in kept
        if not (
            line.lstrip().startswith("> 运行时逐行复核后")
            or (
                "本轮证据" in line
                and re.search(r"\d+\s*家", line)
                and re.search(r"L[123]", line)
            )
        )
    ]
    note = f"> 运行时逐行复核后，最终保留 **{valid_row_count} 家**代码、来源与日期均完整的代表公司。"
    insert_at = next(
        (
            index
            for index, line in enumerate(normalized)
            if line.startswith("### L") or line.lstrip().startswith("| 公司/代码")
        ),
        0,
    )
    normalized[insert_at:insert_at] = [note, ""]
    suffix = ""
    if removed:
        suffix = (
            "\n\n### 因证据校验未通过而未列入\n\n"
            + "、".join(dict.fromkeys(removed))
            + "：公司/代码、可点击来源或来源日期未同时通过本轮核验，故未列入代表公司；"
            "如需覆盖这些公司，应进一步核验公告、财报或公司级证据。"
        )
    return "\n".join(normalized).rstrip() + suffix


def _prepare_playbook_answer(playbook: Optional[AnalysisPlaybook], content: str) -> str:
    if playbook is not None and playbook.id == THEME_COMPANY_MAPPING.id:
        return _sanitize_mapping_answer(content)
    return content


def _playbook_answer_contract_issues(
    playbook: Optional[AnalysisPlaybook],
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Validate the final prose against the runtime-selected Playbook.

    Tool routing alone is not sufficient: a model can retrieve the right
    evidence and still collapse the answer into a loose opinion.  These checks
    are deliberately structural and conservative; they do not pretend to
    judge investment correctness, but they prevent required dimensions from
    disappearing during synthesis.
    """
    if playbook is None:
        return []
    if playbook.id in {INVESTMENT_DECISION.id, STOCK_DEEP_RESEARCH.id}:
        return _professional_answer_contract_issues(content, evidence)

    issues: List[str] = []
    if playbook.id == INDUSTRY_CHAIN.id:
        required_groups = {
            "受益优先级": ("优先级", "排序", "最受益"),
            "产业链拆解": ("上游", "中游", "下游", "产业链"),
            "受益机制与兑现指标": ("受益机制", "价值量", "兑现指标", "订单", "产能"),
            "反证与风险": ("反证", "风险", "不及预期"),
            "持续跟踪项": ("跟踪", "量化指标", "观察指标"),
            "证据时间与置信度": ("来源", "截至", "数据时间", "置信度"),
        }
        for label, markers in required_groups.items():
            if not any(marker in content for marker in markers):
                issues.append(f"缺少{label}")
        if not (
            (
                "持续跟踪" in content
                or "后续跟踪" in content
                or re.search(r"^#{2,4}\s+.*跟踪", content, re.MULTILINE)
            )
            and ("指标" in content or "观察项" in content)
        ):
            issues.append("缺少独立的持续跟踪指标结尾")
        if "置信度" not in content:
            issues.append("缺少明确的整体置信度")
        source_links = set(re.findall(r"https?://[^\s)\]>]+", content))
        if len(source_links) < 2:
            issues.append("关键事实缺少至少两个可点击的独立来源链接")
        return issues

    if playbook.id == THEME_COMPANY_MAPPING.id:
        valid_entities: List[Dict[str, str]] = []
        valid_codes: set[str] = set()
        data_rows: List[tuple[str, str, List[str]]] = []
        in_company_table = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                if in_company_table:
                    in_company_table = False
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not cells:
                continue
            if "公司" in cells[0] and "代码" in cells[0]:
                in_company_table = True
                continue
            if not in_company_table or re.fullmatch(r"[:\- ]+", cells[0] or ""):
                continue
            data_rows.append((cells[0], stripped, cells))
        for first_cell, row, cells in data_rows:
            code_match = re.search(r"(?<!\d)(\d{6})(?!\d)", first_cell)
            if not code_match:
                issues.append(f"公司第一列缺少六位代码: {first_cell}")
                continue
            code = code_match.group(1)
            resolved = find_securities_in_text(first_cell, limit=5)
            entity = next((item for item in resolved if item.get("symbol") == code), None)
            if entity is None:
                issues.append(f"公司名称与本地证券代码不匹配: {first_cell}")
                continue
            if "http://" not in row and "https://" not in row:
                issues.append(f"公司行缺少可点击来源: {first_cell}")
            if not re.search(r"20\d{2}[-年/.]\d{1,2}", row):
                issues.append(f"公司行缺少来源日期: {first_cell}")
            board_only_source = bool(re.search(
                r"(?:vip\.stock\.finance\.sina\.com\.cn/mkt|q\.10jqka\.com\.cn/gn/detail)",
                row,
            ))
            if board_only_source:
                segment = cells[1] if len(cells) > 1 else ""
                verified_fact = cells[3] if len(cells) > 3 else ""
                if not any(marker in segment for marker in ("待公司级", "概念关联", "主题候选")):
                    issues.append(f"板块证据不能直接确定产业链环节: {first_cell}")
                unsupported_business_markers = (
                    "龙头", "主营", "产品", "用于", "应用于", "订单", "收入", "客户",
                    "伺服", "减速器", "丝杠", "执行器", "整机", "系统集成", "营收", "净利润", "亏损",
                )
                if any(marker in verified_fact for marker in unsupported_business_markers):
                    issues.append(f"板块成员证据被扩写成未经核验的公司业务事实: {first_cell}")
            valid_entities.append(entity)
            valid_codes.add(code)
        candidate_result = next(
            (
                item.get("result")
                for item in evidence or []
                if isinstance(item, dict)
                and item.get("tool") == "get_theme_stock_candidates"
                and isinstance(item.get("result"), dict)
                and item["result"].get("success") is not False
            ),
            None,
        )
        candidate_items = (
            candidate_result.get("items")
            if isinstance(candidate_result, dict) and isinstance(candidate_result.get("items"), list)
            else []
        )
        candidate_codes = {
            str(item.get("symbol") or "")
            for item in candidate_items
            if isinstance(item, dict) and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
        }
        codes_in_answer = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", content))
        missing_candidate_codes = sorted(candidate_codes - codes_in_answer)
        if missing_candidate_codes:
            issues.append(
                f"主题候选池返回{len(candidate_codes)}家公司，最终答案遗漏{len(missing_candidate_codes)}家；"
                "必须在完整候选索引中逐一列出公司/代码"
            )
        if not valid_entities and not (
            candidate_codes
            and not missing_candidate_codes
            and "L2/L3" in content
            and any(marker in content for marker in ("没有形成", "未形成", "0 家"))
        ):
            issues.append("没有把取得公司级直接证据的公司/六位代码放入Markdown表格第一列")
        if "代码待核验" in content or "代码缺失" in content:
            issues.append("存在未核验证券代码")
        required_groups = {
            "产业链环节": ("环节", "上游", "中游", "下游"),
            "L1/L2/L3证据等级": ("L1", "L2", "L3"),
            "已验证事实": ("已验证", "披露", "订单", "送样", "收入"),
            "证据边界": ("待核验", "仅概念", "不等同", "证据口径"),
            "来源与日期": ("来源", "公告", "财报", "日期", "截至", "20"),
        }
        if candidate_codes:
            required_groups["完整候选范围"] = ("完整候选池", "完整候选索引", "全部候选")
        for label, markers in required_groups.items():
            if not any(marker in content for marker in markers):
                issues.append(f"缺少{label}")
        return issues
    return []


def _generic_answer_contract_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Reject structurally incomplete answers even without a Playbook.

    A provider can terminate normally after emitting only a Markdown table
    header.  That is not a valid answer, especially when a successful batch
    tool already returned every requested company.
    """
    issues: List[str] = []
    lines = content.splitlines()
    for index in range(len(lines) - 1):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if not header.startswith("|") or not separator.startswith("|"):
            continue
        separator_cells = [cell.strip() for cell in separator.strip("|").split("|")]
        if not separator_cells or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells):
            continue
        data_row_count = 0
        for row in lines[index + 2:]:
            stripped = row.strip()
            if not stripped:
                break
            if not stripped.startswith("|"):
                break
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and not all(re.fullmatch(r"[:\- ]+", cell or "") for cell in cells):
                data_row_count += 1
        if data_row_count == 0:
            issues.append("Markdown表格只有表头，没有任何数据行")

    batch_result = next(
        (
            packet.get("result")
            for packet in evidence or []
            if isinstance(packet, dict)
            and packet.get("tool") == "get_multi_stock_snapshot"
            and isinstance(packet.get("result"), dict)
            and packet["result"].get("success") is not False
        ),
        None,
    )
    batch_items = batch_result.get("items") if isinstance(batch_result, dict) else None
    if isinstance(batch_items, list):
        expected_codes = {
            str(item.get("symbol") or "")
            for item in batch_items
            if isinstance(item, dict) and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
        }
        answer_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", content))
        missing_codes = sorted(expected_codes - answer_codes)
        if missing_codes:
            issues.append(
                f"多股快照返回{len(expected_codes)}家公司，最终答案遗漏{len(missing_codes)}家："
                + "、".join(missing_codes)
            )
    return issues


def _final_answer_contract_issues(
    playbook: Optional[AnalysisPlaybook],
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    return (
        _unsupported_final_claims(content, evidence)
        + _generic_answer_contract_issues(content, evidence)
        + _playbook_answer_contract_issues(playbook, content, evidence)
    )


async def _stream_final_answer_without_tools(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    *,
    state: Optional[Dict[str, str]] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
    playbook: Optional[AnalysisPlaybook] = None,
) -> str:
    """Force one final synthesis pass without tool use to avoid silent exits.

    state: 可选共享容器,累积最终答案文本,使外层在取消时能取到已生成内容。
    """
    forced_messages = _build_synthesis_messages(messages, evidence, playbook)

    is_professional_decision = playbook is not None and playbook.id == INVESTMENT_DECISION.id
    is_playbook_answer = playbook is not None

    kwargs = _build_llm_kwargs(
        llm_cfg,
        stream=True,
        messages=forced_messages,
        max_tokens=4200 if is_professional_decision else (3200 if is_playbook_answer else 2600),
        temperature=0.1,
    )

    content_text = ""
    finish_reason = ""
    try:
        synthesis_timeout = 120.0 if is_playbook_answer else FINAL_SYNTHESIS_TIMEOUT_SECONDS
        async with asyncio.timeout(synthesis_timeout):
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None
                if choice and getattr(choice, "finish_reason", None):
                    finish_reason = str(choice.finish_reason)
                delta = choice.delta if choice else None
                if not delta or not delta.content:
                    continue
                content_text += delta.content
    except TimeoutError:
        content_text = _prepare_playbook_answer(playbook, content_text)
        logger.warning("[Agent] final synthesis timed out after buffering %d chars", len(content_text))
        # The provider can stop delivering the terminal frame after already
        # streaming a complete answer.  Because output is buffered, accept it
        # only if the same evidence and Playbook validators prove the required
        # structure is complete; otherwise fail closed with the deterministic
        # evidence fallback.
        timed_out_issues = _final_answer_contract_issues(playbook, content_text, evidence)
        if not content_text.strip() or timed_out_issues:
            logger.warning(
                "[Agent] timed-out synthesis incomplete: %s",
                "; ".join(timed_out_issues) if timed_out_issues else "empty response",
            )
            retry_messages = [*forced_messages]
            if content_text.strip():
                retry_messages.append({"role": "assistant", "content": content_text})
            retry_messages.append({
                "role": "user",
                "content": (
                    "[上一次综合超时]\n请基于同一份已核验证据直接给出更紧凑的完整最终答案，"
                    "不要复述任务或调用工具。必须满足 Playbook 输出合同，关键事实保留来源链接；"
                    "缺失证据明确标注，不得猜测。"
                    + (("当前缺项：" + "；".join(timed_out_issues)) if timed_out_issues else "")
                ),
            })
            retry_kwargs = _build_llm_kwargs(
                llm_cfg,
                stream=True,
                messages=retry_messages,
                max_tokens=4200 if is_professional_decision else 2800,
                temperature=0.0,
            )
            retry_text = ""
            retry_finish_reason = ""
            try:
                async with asyncio.timeout(120.0 if is_playbook_answer else FINAL_SYNTHESIS_TIMEOUT_SECONDS):
                    retry_response = await litellm.acompletion(**retry_kwargs)
                    async for chunk in retry_response:
                        choice = chunk.choices[0] if chunk.choices else None
                        if choice and getattr(choice, "finish_reason", None):
                            retry_finish_reason = str(choice.finish_reason)
                        delta = choice.delta if choice else None
                        if delta and delta.content:
                            retry_text += delta.content
            except Exception:
                logger.exception("[Agent] timed-out synthesis retry failed")
            retry_text = _prepare_playbook_answer(playbook, retry_text)
            # Row-level evidence failures in mapping answers are removed
            # deterministically before the whole report is judged.
            retry_issues = _final_answer_contract_issues(playbook, retry_text, evidence)
            if (
                retry_text.strip()
                and retry_finish_reason.lower() not in {"length", "max_tokens"}
                and not retry_issues
            ):
                content_text = retry_text
            else:
                if retry_issues:
                    logger.warning("[Agent] rejected timeout retry: %s", "; ".join(retry_issues))
                content_text = _build_verified_evidence_fallback(evidence)
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        return content_text
    except Exception as e:
        logger.exception("[Agent] Forced final answer failed")
        content_text = _build_verified_evidence_fallback(evidence)
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        return content_text

    content_text = _prepare_playbook_answer(playbook, content_text)
    if content_text.strip() and finish_reason.lower() not in {"length", "max_tokens"}:
        reasons = _final_answer_contract_issues(playbook, content_text, evidence)
        if reasons:
            logger.warning("[Agent] final answer needs repair: %s", "; ".join(reasons))
            repair_messages = [
                *forced_messages,
                {"role": "assistant", "content": content_text},
                {
                    "role": "user",
                    "content": (
                        "[运行时输出验收未通过]\n"
                        + "；".join(reasons)
                        + "。请仅重写最终答案，不再调用工具。必须使用已有证据补齐这些结构；"
                        "证据没有提供的内容明确写‘证据缺失’，禁止猜测。"
                    ),
                },
            ]
            repair_kwargs = _build_llm_kwargs(
                llm_cfg,
                stream=True,
                messages=repair_messages,
                max_tokens=4200 if is_professional_decision else 3600,
                temperature=0.0,
            )
            repaired_text = ""
            repaired_finish_reason = ""
            try:
                repair_timeout = 120.0 if is_playbook_answer else FINAL_SYNTHESIS_TIMEOUT_SECONDS
                async with asyncio.timeout(repair_timeout):
                    repair_response = await litellm.acompletion(**repair_kwargs)
                    async for chunk in repair_response:
                        choice = chunk.choices[0] if chunk.choices else None
                        if choice and getattr(choice, "finish_reason", None):
                            repaired_finish_reason = str(choice.finish_reason)
                        delta = choice.delta if choice else None
                        if delta and delta.content:
                            repaired_text += delta.content
            except Exception:
                logger.exception("[Agent] final answer repair failed")

            repaired_text = _prepare_playbook_answer(playbook, repaired_text)
            repair_issues = _final_answer_contract_issues(playbook, repaired_text, evidence)
            if (
                repaired_text.strip()
                and repaired_finish_reason.lower() not in {"length", "max_tokens"}
                and not repair_issues
            ):
                content_text = repaired_text
            else:
                if repair_issues:
                    logger.warning("[Agent] rejected repaired answer: %s", "; ".join(repair_issues))
                content_text = _build_verified_evidence_fallback(evidence)
        controller.append_text(content_text)
        if state is not None:
            # Final answer replaces any earlier internal planning snapshot.
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        return content_text

    if content_text.strip():
        logger.warning("[Agent] final synthesis truncated: finish_reason=%s", finish_reason)

    if is_playbook_answer:
        logger.warning(
            "[Agent] playbook synthesis returned %s; retrying compact final",
            "truncated text" if content_text.strip() else "empty text",
        )
        retry_messages = [
            *forced_messages,
            {
                "role": "user",
                "content": (
                    "[最终综合没有产生完整文本]\n请直接输出一份紧凑但完整的最终答案，不调用工具。"
                    "逐项满足当前 Playbook 输出合同，关键事实保留可点击来源；"
                    "缺失证据明确写出，不得猜测。"
                ),
            },
        ]
        retry_kwargs = _build_llm_kwargs(
            llm_cfg,
            stream=True,
            messages=retry_messages,
            max_tokens=4200 if is_professional_decision else 2800,
            temperature=0.0,
        )
        retry_text = ""
        retry_finish_reason = ""
        try:
            async with asyncio.timeout(120.0):
                retry_response = await litellm.acompletion(**retry_kwargs)
                async for chunk in retry_response:
                    choice = chunk.choices[0] if chunk.choices else None
                    if choice and getattr(choice, "finish_reason", None):
                        retry_finish_reason = str(choice.finish_reason)
                    delta = choice.delta if choice else None
                    if delta and delta.content:
                        retry_text += delta.content
        except Exception:
            logger.exception("[Agent] empty/truncated synthesis retry failed")
        retry_text = _prepare_playbook_answer(playbook, retry_text)
        retry_issues = _final_answer_contract_issues(playbook, retry_text, evidence)
        if (
            retry_text.strip()
            and retry_finish_reason.lower() not in {"length", "max_tokens"}
            and not retry_issues
        ):
            content_text = retry_text
            controller.append_text(content_text)
            if state is not None:
                state["assistant_text"] = content_text
                controller.assistant_text_snapshot = content_text
            return content_text
        if retry_issues:
            logger.warning("[Agent] rejected empty/truncated retry: %s", "; ".join(retry_issues))

    content_text = _build_verified_evidence_fallback(evidence)
    controller.append_text(content_text)
    if state is not None:
        state["assistant_text"] = content_text
        controller.assistant_text_snapshot = content_text
    return content_text


async def _run_react_loop(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    system_prompt: str = "",
    on_progress=None,
    *,
    state: Optional[Dict[str, str]] = None,
) -> str:
    """Execute the ReAct loop: LLM thinks → calls tools → observes → repeats.

    on_progress: 可选回调 async (assistant_text_so_far: str) -> None,每轮迭代末尾
    调用,用于增量持久化已生成的 assistant 文本(刷新后可恢复)。
    state: 可选共享容器,实时记录已累积 assistant 文本,使外层在取消时能取到。
    """
    policy = _resolve_turn_policy(messages)
    allowed_tools = policy["allowed_tools"]
    tools = [
        schema for schema in _registry.get_all_schemas()
        if allowed_tools is None
        or schema.get("function", {}).get("name") in allowed_tools
    ]
    registered_tool_names = set(_registry.get_tool_names())
    tool_names = (
        registered_tool_names
        if allowed_tools is None
        else registered_tool_names.intersection(allowed_tools)
    )
    max_tool_calls = int(policy["max_tool_calls"])
    max_discovery_calls = policy.get("max_discovery_calls")
    discovery_tool_names = {
        "search_financial_news", "search_research_library", "websearch", "webfetch",
    }

    latest_user_text = _last_user_text(messages)
    latest_user_entities = find_securities_in_text(latest_user_text, limit=20)
    previous_answer_entities = _previous_answer_table_entities(messages)
    semantic_intent: Optional[ResearchIntent] = None
    if llm_cfg.get("semantic_intent_enabled"):
        try:
            async with asyncio.timeout(50.0):
                semantic_intent = await resolve_research_intent(
                    messages,
                    llm_cfg,
                    completion=litellm.acompletion,
                    current_entities=latest_user_entities,
                    previous_answer_entities=previous_answer_entities,
                )
            logger.info(
                "[AgentIntent] source=semantic kind=%s topic=%s discovery_theme=%s entity_scope=%s confidence=%.2f",
                semantic_intent.kind,
                semantic_intent.normalized_topic,
                semantic_intent.normalized_discovery_theme,
                semantic_intent.entity_scope,
                semantic_intent.confidence,
            )
        except Exception:
            logger.warning("[AgentIntent] semantic resolution failed; using availability fallback", exc_info=True)

    if semantic_intent is not None:
        verified_entities = _entities_for_semantic_intent(
            semantic_intent,
            current_entities=latest_user_entities,
            previous_answer_entities=previous_answer_entities,
        )
        entity_context = _entity_context_text(
            verified_entities,
            semantic_scope=semantic_intent.entity_scope,
        )
        referential_followup = semantic_intent.entity_scope == "previous_answer"
        playbook = select_playbook_for_intent(semantic_intent)
        required_playbook_calls = mandatory_tool_calls(
            playbook,
            messages,
            verified_entities,
            intent=semantic_intent,
        )
    else:
        entity_context, verified_entities = _verified_entity_context(messages)
        referential_followup = (
            not latest_user_entities
            and any(
                marker in latest_user_text.lower()
                for marker in ("这些", "上述", "上面", "前面", "它们", "他们", "those", "them")
            )
        )
        playbook = select_analysis_playbook(messages, verified_entities)
        required_playbook_calls = mandatory_tool_calls(playbook, messages, verified_entities)

    if semantic_intent is not None and semantic_intent.needs_clarification:
        clarification = semantic_intent.clarification_question or "请明确本轮要研究的主题或公司范围。"
        controller.append_text(clarification)
        if state is not None:
            state["assistant_text"] = clarification
            controller.assistant_text_snapshot = clarification
        return clarification

    semantic_context = (
        "## 结构化研究意图（语义模型已解析，禁止重新按关键词猜测）\n"
        + json.dumps(semantic_intent.model_dump(), ensure_ascii=False)
        if semantic_intent is not None
        else "## 结构化研究意图\n语义解析不可用，本轮使用兼容降级路径。"
    )
    playbook_context = (
        playbook.system_instruction()
        if playbook is not None
        else "本轮未命中强制研究 Playbook，按通用 Agent 规则执行。"
    )
    full_messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                f"{system_prompt or SYSTEM_PROMPT}\n\n"
                f"## 本轮执行边界（{policy['name']}）\n{policy['guidance']}\n\n"
                f"## 确定性实体上下文\n{entity_context}\n\n"
                f"{semantic_context}\n\n"
                f"{playbook_context}"
            ),
        },
        *_normalize_incoming_messages(messages),
    ]

    if state is not None:
        state["assistant_text"] = ""

    llm_extra: Dict[str, Any] = {"messages": full_messages}
    if tools:
        llm_extra.update({
            "tools": tools,
            "tool_choice": "required" if policy["require_tools"] else "auto",
        })
    kwargs = _build_llm_kwargs(llm_cfg, stream=True, **llm_extra)

    controller.append_text("正在拆解问题并规划研究路径...\n\n")
    started_at = time.monotonic()
    total_tool_calls = 0
    total_discovery_calls = 0
    executed_calls: Dict[str, Dict[str, Any]] = {}
    tool_failure_counts: Dict[str, int] = {}
    evidence: List[Dict[str, Any]] = []
    force_synthesis_reason = "iteration_limit"

    if playbook is not None:
        logger.info(
            "[Agent] selected playbook=%s mandatory_tools=%s",
            playbook.id,
            [call.get("name") for call in required_playbook_calls],
        )

    for iteration in range(MAX_REACT_ITERATIONS):
        if time.monotonic() - started_at >= MAX_AGENT_RUN_SECONDS:
            force_synthesis_reason = "time_budget"
            logger.info("[Agent] forcing synthesis after run time budget")
            break
        if total_tool_calls >= max_tool_calls:
            force_synthesis_reason = "tool_budget"
            logger.info("[Agent] forcing synthesis after %d tool calls", total_tool_calls)
            break

        tool_calls_acc: Dict[int, Dict[str, Any]] = {}
        content_text = ""

        # 每轮调用前检查上下文是否超阈值，超了就把早期对话摘要压缩（含首轮：前端回传的历史可能已超限）
        full_messages = await _compact_history_if_needed(full_messages, llm_cfg)
        kwargs["messages"] = full_messages

        if iteration == 0 and required_playbook_calls:
            # High-stakes research starts from the runtime-owned evidence plan.
            # The model never gets an opportunity to skip required dimensions.
            round_calls = list(required_playbook_calls)
        else:
            try:
                response = await litellm.acompletion(**kwargs)
            except Exception as e:
                logger.exception("[Agent] LLM call failed")
                controller.append_text(f"\n\n分析出错：{e}")
                return ""

            try:
                async with asyncio.timeout(60.0):
                    async for chunk in response:
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if not delta:
                            continue
                        if delta.content:
                            # Buffer until we know whether this is the final answer.
                            # Some models emit planning prose before tool calls; exposing
                            # that prose creates a noisy and misleading user experience.
                            content_text += delta.content
                        if delta.tool_calls:
                            for tc in delta.tool_calls:
                                idx = tc.index
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {
                                        "id": tc.id or "",
                                        "name": "",
                                        "arguments": "",
                                    }
                                if tc.id:
                                    tool_calls_acc[idx]["id"] = tc.id
                                if tc.function and tc.function.name:
                                    tool_calls_acc[idx]["name"] += tc.function.name
                                if tc.function and tc.function.arguments:
                                    tool_calls_acc[idx]["arguments"] += tc.function.arguments
            except TimeoutError:
                logger.warning("[Agent] model stream timed out at iteration %d", iteration + 1)
                force_synthesis_reason = "model_timeout"
                break

            if not tool_calls_acc:
                # A normal ReAct completion used to return here before the
                # evidence-contract checks applied by forced synthesis.  That
                # allowed unsupported market statistics to leak into an
                # otherwise valid quote answer.  Quote-only turns are safer as
                # deterministic rendering; every other turn must pass the same
                # claim and playbook checks before it is emitted.
                final_text = _build_realtime_quote_answer(evidence, latest_user_text) or _strip_progress_markers(content_text)
                if final_text:
                    final_issues = _final_answer_contract_issues(playbook, final_text, evidence)
                    if final_issues:
                        logger.warning(
                            "[Agent] regular ReAct answer needs repair: %s",
                            "; ".join(final_issues),
                        )
                        force_synthesis_reason = "answer_contract_repair"
                        break
                    controller.append_text(final_text)
                    if state is not None:
                        state["assistant_text"] = final_text
                        controller.assistant_text_snapshot = final_text
                    if on_progress is not None:
                        try:
                            await on_progress(final_text)
                        except Exception:
                            logger.debug("[Agent] final progress save failed (non-fatal)", exc_info=True)
                    return final_text
                force_synthesis_reason = "empty_model_answer"
                break

            round_calls = _coalesce_multi_security_calls(
                list(tool_calls_acc.values()),
                verified_entities=verified_entities,
                referential_followup=referential_followup,
            )
            if any(call.get("name") == "get_multi_stock_snapshot" for call in round_calls):
                round_calls = _select_balanced_multi_security_calls(
                    round_calls,
                    latest_user_text=latest_user_text,
                )
        logger.info(
            "[Agent] iteration %d planned tools: %s",
            iteration + 1,
            [call.get("name") for call in round_calls],
        )
        if len(round_calls) > MAX_TOOL_CALLS_PER_ROUND:
            logger.info(
                "[Agent] limiting round tool calls from %d to %d",
                len(round_calls), MAX_TOOL_CALLS_PER_ROUND,
            )

        # Reserve calls before the first await in each worker.  This keeps
        # duplicate calls in the same parallel batch from racing each other,
        # and lets the UI show only requests that are actually executed.
        reserved_signatures = set(executed_calls)
        reserved_single_shot_tools = {
            str(call.get("tool") or "")
            for call in executed_calls.values()
            if isinstance(call, dict)
        }
        single_shot_tool_names = {"get_theme_stock_candidates", "screen_atr_volatility_stocks"}
        reserved_new_calls = 0
        available_call_slots = min(
            MAX_TOOL_CALLS_PER_ROUND,
            max(0, max_tool_calls - total_tool_calls),
        )
        reserved_discovery_calls = 0

        async def _execute_one_tool(tc: Dict[str, Any], call_index: int) -> Dict[str, Any]:
            """Run a single tool call: concurrent fetch (off the event loop) + UI回填.

            取数（registry.execute + 压缩 + 联网兜底）是同步网络 IO，必须丢进线程池，
            否则会阻塞整个 FastAPI 事件循环。多个工具调用通过 asyncio.gather 并发执行，
            UI 气泡（add_tool_call / set_response）在各自协程里独立回填，作用于互不共享的
            tool 对象，无需加锁；messages 按调用顺序回灌。
            """
            tool_name = tc["name"].strip()
            tool_call_id = tc["id"] or f"call_{uuid.uuid4().hex}"
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"].strip() else {}
            except json.JSONDecodeError:
                args = {}
            # Canonicalize transport/model drift before security resolution,
            # deduplication and UI rendering. This keeps the displayed args,
            # execution signature and Python keyword arguments identical.
            normalize_arguments = getattr(_registry, "normalize_arguments", None)
            if callable(normalize_arguments):
                normalized_args = normalize_arguments(tool_name, args)
                if isinstance(normalized_args, dict):
                    args = normalized_args
            args, unresolved_entities = normalize_tool_security_arguments(tool_name, args)
            normalized_arguments = json.dumps(args, ensure_ascii=False)

            if unresolved_entities:
                result_payload = {
                    "success": False,
                    "errors": [
                        "无法从本地证券库确认: " + ", ".join(unresolved_entities)
                        + "。不要猜代码；需要时向用户确认具体证券。"
                    ],
                    "unresolved_entities": unresolved_entities,
                }
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": _format_result(result_payload),
                    "evidence": None,
                    "executed": False,
                    "succeeded": False,
                }

            signature = json.dumps(
                {"tool": tool_name, "arguments": args},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )

            nonlocal reserved_new_calls, reserved_discovery_calls
            if (
                tool_name in single_shot_tool_names
                and tool_name in reserved_single_shot_tools
            ):
                result_payload = {
                    "success": True,
                    "reused": True,
                    "message": f"{tool_name} 本轮已经安排执行，复用同轮主题候选请求，避免主题漂移和重复请求。",
                    "previous_tool": tool_name,
                }
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": _format_result(result_payload),
                    "evidence": None,
                    "executed": False,
                    "succeeded": True,
                }
            if signature in reserved_signatures:
                result_payload = {
                    "success": True,
                    "reused": True,
                    "message": "相同参数已查询，本次复用前序证据，避免重复请求。",
                    "previous_tool": tool_name,
                }
                result_str = _format_result(result_payload)
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": result_str,
                    "evidence": None,
                    "executed": False,
                    "succeeded": True,
                }

            if tool_name not in tool_names:
                result_str = f"工具 '{tool_name}' 不存在，可用工具: {', '.join(sorted(tool_names))}"
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": result_str,
                    "evidence": None,
                    "executed": False,
                    "succeeded": False,
                }

            if tool_failure_counts.get(tool_name, 0) >= 1:
                result_payload = {
                    "success": False,
                    "errors": [f"{tool_name} 本轮已经失败，停止重复调用并改用现有证据或其他工具。"],
                    "circuit_open": True,
                }
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": _format_result(result_payload),
                    "evidence": None,
                    "executed": False,
                    "succeeded": False,
                }

            if reserved_new_calls >= available_call_slots:
                result_payload = {
                    "success": False,
                    "errors": ["本轮研究预算已足够，请基于已有证据形成答案。"],
                    "budget_exhausted": True,
                }
                result_str = _format_result(result_payload)
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": result_str,
                    "evidence": None,
                    "executed": False,
                    "succeeded": False,
                }

            if (
                tool_name in discovery_tool_names
                and max_discovery_calls is not None
                and total_discovery_calls + reserved_discovery_calls >= max_discovery_calls
            ):
                result_payload = {
                    "success": False,
                    "errors": ["发现类检索已足够，请使用公司资料、主营、公告或财务工具核验候选公司。"],
                    "discovery_budget_exhausted": True,
                }
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": _format_result(result_payload),
                    "evidence": None,
                    "executed": False,
                    "succeeded": False,
                }

            reserved_signatures.add(signature)
            if tool_name in single_shot_tool_names:
                reserved_single_shot_tools.add(tool_name)
            reserved_new_calls += 1
            if tool_name in discovery_tool_names:
                reserved_discovery_calls += 1
            tool = await controller.add_tool_call(tool_name, tool_call_id=tool_call_id)
            tool.append_args_text(normalized_arguments)
            tool_timeout = (
                QUANTITATIVE_SCREEN_TIMEOUT_SECONDS
                if tool_name == "screen_atr_volatility_stocks"
                else PROFESSIONAL_EVIDENCE_TIMEOUT_SECONDS
                if (
                    tool_name in {"get_multi_stock_decision_evidence", "get_theme_stock_candidates"}
                    or (tool_name == "websearch" and bool(args.get("includeContent")))
                    or (
                        tool_name == "get_monetary_policy_operations"
                        and bool(args.get("include_content"))
                    )
                )
                else TOOL_EXECUTION_TIMEOUT_SECONDS
            )
            tool_started = time.monotonic()

            try:
                def _sync_fetch() -> Any:
                    # Isolate every tool, not just a static risk list.  Cache
                    # misses can route otherwise simple tools through
                    # AKShare/libmini_racer, whose native abort cannot be caught
                    # inside the API worker.
                    result = execute_tool_isolated(
                        tool_name,
                        args,
                        timeout_seconds=tool_timeout - 3,
                    )
                    llm_result = _compact_tool_result(tool_name, result)
                    return _maybe_attach_search_fallback(tool_name, args, llm_result)

                llm_result = await asyncio.wait_for(
                    asyncio.to_thread(_sync_fetch),
                    timeout=tool_timeout,
                )
                result_str = _format_result(llm_result)
                succeeded = not (
                    isinstance(llm_result, dict) and llm_result.get("success") is False
                )
                response_payload = llm_result if isinstance(llm_result, dict) else {"result": result_str}
                tool.set_response(response_payload, is_error=not succeeded)
                executed_calls[signature] = {
                    "tool": tool_name,
                    "arguments": args,
                    "result": llm_result,
                }
                duration_ms = int((time.monotonic() - tool_started) * 1000)
                logger.info(
                    "[AgentTool] tool=%s success=%s partial=%s fallback=%s item_count=%s duration_ms=%s",
                    tool_name,
                    succeeded,
                    llm_result.get("partial") if isinstance(llm_result, dict) else None,
                    llm_result.get("fallback_used") if isinstance(llm_result, dict) else None,
                    (
                        llm_result.get("item_count", llm_result.get("count"))
                        if isinstance(llm_result, dict)
                        else None
                    ),
                    duration_ms,
                )
                return {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": normalized_arguments,
                    "result_str": result_str,
                    "evidence": executed_calls[signature],
                    "executed": True,
                    "succeeded": succeeded,
                }
            except asyncio.TimeoutError:
                logger.warning(
                    "[AgentTool] tool=%s success=false timeout=true duration_ms=%s",
                    tool_name,
                    int((time.monotonic() - tool_started) * 1000),
                )
                result_str = f"工具执行超时（>{tool_timeout:.0f}s）"
                tool.set_response({"error": result_str}, is_error=True)
            except Exception as e:
                logger.exception(
                    "[AgentTool] tool=%s success=false duration_ms=%s",
                    tool_name,
                    int((time.monotonic() - tool_started) * 1000),
                )
                result_str = f"工具执行失败: {e}"
                tool.set_response({"error": result_str}, is_error=True)

            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": normalized_arguments,
                "result_str": result_str,
                "evidence": None,
                "executed": True,
                "succeeded": False,
            }

        outcomes = await asyncio.gather(
            *[_execute_one_tool(tc, index) for index, tc in enumerate(round_calls)]
        )

        total_tool_calls += sum(1 for outcome in outcomes if outcome["executed"])
        for outcome in outcomes:
            if outcome["executed"] and not outcome["succeeded"]:
                name = outcome["tool_name"]
                tool_failure_counts[name] = tool_failure_counts.get(name, 0) + 1
        total_discovery_calls += sum(
            1
            for outcome in outcomes
            if outcome["executed"] and outcome["tool_name"] in discovery_tool_names
        )
        for outcome in outcomes:
            if outcome["evidence"] is not None:
                evidence.append(outcome["evidence"])
                continue
            if outcome["succeeded"]:
                continue
            try:
                failed_result = json.loads(outcome["result_str"])
            except (TypeError, json.JSONDecodeError):
                failed_result = {
                    "success": False,
                    "error": str(outcome.get("result_str") or "工具没有返回证据"),
                }
            try:
                failed_arguments = json.loads(outcome.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                failed_arguments = {}
            evidence.append({
                "tool": outcome["tool_name"],
                "arguments": failed_arguments,
                "result": failed_result,
            })

        # One assistant message owns the whole batch of tool calls.  Splitting
        # parallel calls into multiple assistant messages is invalid for some
        # Anthropic/OpenAI adapters and can orphan tool results.
        full_messages.append({
            "role": "assistant",
            "content": content_text or None,
            "tool_calls": [
                {
                    "id": oc["tool_call_id"],
                    "type": "function",
                    "function": {"name": oc["tool_name"], "arguments": oc["arguments"]},
                }
                for oc in outcomes
            ],
        })
        for oc in outcomes:
            full_messages.append({
                "role": "tool",
                "tool_call_id": oc["tool_call_id"],
                "content": oc["result_str"],
            })

        await _flush_substreams(controller)

        # 历史轮 tool 结果二次瘦身：本轮刚加的 tool_call_id 跳过，更早轮次的明细数组
        # 已无价值，裁掉可避免 token 随轮次二次方累积。仅影响回灌 LLM 的内容。
        current_tool_ids = {oc["tool_call_id"] for oc in outcomes}
        for msg in full_messages:
            if (
                msg.get("role") == "tool"
                and msg.get("tool_call_id") not in current_tool_ids
            ):
                msg["content"] = _slim_tool_content(msg.get("content", ""))

        if iteration == 0 and required_playbook_calls:
            force_synthesis_reason = f"playbook_{playbook.id if playbook else 'required'}_evidence_collected"
            logger.info("[Agent] mandatory playbook evidence round complete")
            break

        # Referential multi-company follow-ups are a common place where models
        # keep fanning out into one financial/technical call per company after
        # already receiving a complete batch snapshot.  The batch result now
        # includes quote valuation, technicals and latest-period fundamentals,
        # so stop the evidence loop and synthesize from that verified payload.
        multi_security_request = referential_followup or len(latest_user_entities) >= 2
        if multi_security_request and any(
            outcome["tool_name"] == "get_multi_stock_snapshot"
            and outcome["succeeded"]
            for outcome in outcomes
        ):
            force_synthesis_reason = "multi_stock_evidence_complete"
            logger.info("[Agent] batch evidence complete for multi-stock request")
            break

        kwargs["messages"] = full_messages
        # Only the first model turn is forced to gather evidence.  Once at
        # least one tool batch exists, the model may decide that the evidence
        # is sufficient and answer immediately.
        if tools:
            kwargs["tool_choice"] = "auto"

    if playbook is not None and playbook.id == QUANTITATIVE_SCREENING.id:
        content_text = _build_quantitative_screen_answer(evidence)
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        logger.info("[Agent] rendered deterministic quantitative screening answer")
        return content_text

    if playbook is not None and playbook.id == THEME_COMPANY_MAPPING.id:
        # 公司映射的全量候选索引可能包含数百家公司。让模型重写这份索引既慢，
        # 又容易在 token 上限处截断或擅自只保留“代表公司”。运行时已经拥有完整
        # 证券主数据和公司级检索证据，因此直接用确定性渲染器交付，保证不漏代码、
        # 不把否定表述升级成订单，也不会再次卡在最终综合。
        semantic_facts: Optional[List[BoundEvidenceFact]] = None
        if semantic_intent is not None and llm_cfg.get("semantic_evidence_enabled"):
            try:
                async with asyncio.timeout(40.0):
                    semantic_facts = await bind_company_evidence(
                        evidence,
                        semantic_intent,
                        llm_cfg,
                        completion=litellm.acompletion,
                    )
                logger.info(
                    "[AgentEvidence] source=semantic topic=%s accepted_facts=%d",
                    semantic_intent.normalized_topic,
                    len(semantic_facts),
                )
            except Exception:
                # Availability fallback is observable and only used when the
                # semantic binder itself is unavailable, never when it returns
                # a valid empty fact set.
                logger.warning(
                    "[AgentEvidence] semantic binding failed; using availability fallback",
                    exc_info=True,
                )
                semantic_facts = None
        content_text = _build_verified_evidence_fallback(
            evidence,
            semantic_facts=semantic_facts,
            semantic_intent=semantic_intent,
        )
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        logger.info("[Agent] rendered deterministic complete theme-company mapping")
        return content_text

    if playbook is not None and playbook.id in {
        INVESTMENT_DECISION.id,
        STOCK_DEEP_RESEARCH.id,
    }:
        # The professional evidence packet already contains every company and
        # every decision axis required by the playbook. Sending it through a
        # second model synthesis can take minutes, omit rows during repair, or
        # leave a tool-only blank turn if cancelled. Render that verified
        # packet directly so successful tools always produce a persisted body.
        content_text = _build_verified_evidence_fallback(
            evidence,
            professional_decision_requested=(playbook.id == INVESTMENT_DECISION.id),
        )
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        logger.info("[Agent] rendered deterministic professional research answer")
        return content_text

    quote_text = _build_realtime_quote_answer(evidence, latest_user_text)
    if quote_text:
        controller.append_text(quote_text)
        if state is not None:
            state["assistant_text"] = quote_text
            controller.assistant_text_snapshot = quote_text
        logger.info("[Agent] rendered deterministic quote-only answer")
        return quote_text

    logger.info("[Agent] entering final synthesis: %s", force_synthesis_reason)
    return await _stream_final_answer_without_tools(
        controller,
        full_messages,
        llm_cfg,
        state=state,
        evidence=evidence,
        playbook=playbook,
    )


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Chat endpoint using assistant-stream DataStream protocol.

    生成逻辑跑在独立后台 task (detach 于 HTTP 连接),首连接 attach 为第一个订阅者。
    断连不杀生成 —— 后端继续跑完落库,用户刷新后可通过 /agent/chat/resume 续流。
    """
    limits = get_agent_runtime_limits()
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            # JSON UTF-8 can use up to four bytes per character.  Reject a
            # clearly oversized body before parsing it into memory; the exact
            # character limit is enforced again after parsing.
            if int(content_length) > limits.max_request_chars * 4:
                return JSONResponse(
                    status_code=413,
                    content={"error": "request_too_large", "message": "请求内容过大"},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_content_length", "message": "Content-Length 格式不合法"},
            )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体不是有效 JSON"},
        )
    try:
        messages, conversation_id, resume_existing = validate_chat_request_body(body)
    except AgentRequestValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )

    session_service = ChatSessionService(db_manager)

    # Resume is an attachment operation, not a new model request.  It must not
    # require the current model configuration and must never create a new blank
    # conversation when a stale/invalid id is supplied.
    if resume_existing:
        if not conversation_id:
            return JSONResponse(
                status_code=400,
                content={"error": "conversation_id_required", "message": "恢复运行必须提供 conversation_id"},
            )
        if not session_service.get_conversation(conversation_id):
            return JSONResponse(
                status_code=404,
                content={"error": "conversation_not_found", "message": "对话不存在"},
            )
        active_run = active_run_registry.get(conversation_id)
        try:
            replay_from = max(0, int(body.get("after_chunk_index") or 0))
        except (TypeError, ValueError):
            replay_from = 0
        if active_run is not None:
            logger.info(
                "[Agent] chat attach existing run_id=%s conversation_id=%s from chunk %s status=%s",
                active_run.run_id,
                conversation_id,
                replay_from,
                active_run.status,
            )
            return DataStreamResponse(subscriber_stream(active_run, replay_from=replay_from))
        logger.info("[Agent] chat resume requested but no retained run for %s", conversation_id)
        return JSONResponse(
            status_code=409,
            content={"error": "run_not_active", "conversation_id": conversation_id},
        )

    retry_after = agent_request_rate_limiter.check_and_record(
        get_client_ip(request),
        limit=limits.requests_per_minute,
    )
    if retry_after:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "error": "agent_rate_limited",
                "message": "请求过于频繁，请稍后再试",
                "retry_after_seconds": retry_after,
            },
        )

    try:
        llm_cfg = _get_llm_config()
    except AgentModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    conversation = session_service.ensure_conversation(conversation_id)
    conv_id = conversation["id"]

    # 原子地「判定无活跃 run + 创建新 run」(锁内)。把判定与创建合并,消除
    # is_active(无锁)与 start_or_get(锁内)之间的竞态窗口:两个并发请求不会
    # 都通过检查、各自落库 messages 后第二个静默 attach 到第一个 run 而丢消息。
    # 拿到 None 表示已有活跃 run → 409 触发前端续流。
    try:
        run = await active_run_registry.try_claim(
            conv_id,
            max_active_runs=limits.max_active_runs,
        )
    except RunCapacityExceeded:
        logger.warning(
            "[Agent] global capacity exhausted conversation_id=%s active=%s limit=%s",
            conv_id,
            active_run_registry.stats()["active_runs"],
            limits.max_active_runs,
        )
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5"},
            content={
                "error": "agent_busy",
                "message": "AI 助手当前任务较多，请稍后重试",
                "retry_after_seconds": 5,
            },
        )
    if run is None:
        logger.info("[Agent] chat rejected: run already in progress for %s", conv_id)
        return JSONResponse(
            status_code=409,
            content={"error": "run_in_progress", "conversation_id": conv_id},
        )

    logger.info(
        f"[Agent] Chat request with {len(messages)} messages, "
        f"model={llm_cfg['model']}, conversation_id={conv_id}, run_id={run.run_id}"
    )

    # 生成开始前同步落库本次完整 messages(含刚发的 user 消息)。
    # 这一步不能放在后台 task 里:用户一发送就刷新时,conversation detail 会
    # 先于后台 task 执行,如果库里还没有本次 user,前端只能恢复出空白历史。
    try:
        await asyncio.to_thread(
            session_service.save_conversation_snapshot,
            conv_id,
            list(messages),
            skip_title=True,
        )
    except Exception as exc:
        logger.exception("[Agent] failed to persist request snapshot conversation_id=%s", conv_id)
        await active_run_registry.mark_done(conv_id, "failed", error="request_snapshot_failed")
        raise HTTPException(status_code=500, detail="保存对话失败，请重试") from exc

    async def run_callback(controller: RunBroadcaster):
        from src.services.agent_prompt_service import AgentPromptService

        system_prompt, is_fallback = AgentPromptService(db_manager).get_active_system_prompt()
        logger.info(
            "[Agent] system prompt %s",
            "fallback to source default" if is_fallback else f"from template",
        )

        # 增量持久化:节流(>=3s 一次)把已生成 assistant 文本写库,刷新后可恢复
        last_save_ts = 0.0
        state: Dict[str, str] = {"assistant_text": ""}

        async def on_progress(assistant_text_so_far: str) -> None:
            nonlocal last_save_ts
            # 同步镜像到 broadcaster,供续流端点补齐已生成文本
            controller.assistant_text_snapshot = assistant_text_so_far
            now = asyncio.get_running_loop().time()
            if now - last_save_ts < 3.0:
                return
            last_save_ts = now
            await asyncio.to_thread(
                session_service.save_partial_assistant_text,
                conv_id,
                assistant_text_so_far,
            )

        final_response_text = ""
        try:
            final_response_text = await _run_react_loop(
                controller, messages, llm_cfg, system_prompt,
                on_progress=on_progress, state=state,
            )

            persisted_messages = list(messages)
            if final_response_text.strip():
                persisted_messages.append(
                    {
                        "id": f"assistant-{uuid.uuid4().hex}",
                        "role": "assistant",
                        "content": final_response_text,
                        "created_at": datetime.now().isoformat(),
                    }
                )
            await asyncio.to_thread(
                session_service.save_conversation_snapshot,
                conv_id,
                persisted_messages,
            )
            await active_run_registry.mark_done(
                conv_id, "completed", final_text=final_response_text
            )
        except asyncio.CancelledError:
            # 后台 task 不被 HTTP 断连取消,仅进程关闭/显式 cancel 会到这。
            # 把已生成文本落定,避免残留 {conv_id}-assistant-pending 半截消息。
            partial = state.get("assistant_text", "")
            partial = (
                partial.rstrip() + "\n\n[已停止]"
                if partial.strip()
                else "[已停止]"
            )
            try:
                await asyncio.shield(asyncio.to_thread(
                    session_service.save_partial_assistant_text, conv_id, partial,
                ))
            except asyncio.CancelledError:
                try:
                    session_service.save_partial_assistant_text(conv_id, partial)
                except Exception:
                    logger.warning("[Agent] cancel-time partial save failed", exc_info=True)
            except Exception:
                logger.warning("[Agent] cancel-time partial save failed", exc_info=True)
            await active_run_registry.mark_done(conv_id, "cancelled", final_text=partial)
            raise
        except Exception as exc:
            logger.exception("[Agent] background run failed")
            controller.add_error(str(exc))
            partial = state.get("assistant_text", "")
            if partial.strip():
                try:
                    await asyncio.to_thread(
                        session_service.save_partial_assistant_text, conv_id, partial
                    )
                except Exception:
                    logger.debug("[Agent] failed-run partial save failed", exc_info=True)
            await active_run_registry.mark_done(conv_id, "failed", error=str(exc))

    async def factory(broadcaster: RunBroadcaster) -> "asyncio.Task":
        return asyncio.create_task(run_callback(broadcaster))

    # run 已由前面的 try_claim 原子创建(判定 + 创建在同一锁内)。首连接必须先
    # subscribe 再启动后台 task,否则 task 可能在首个订阅者 subscribe 之前就
    # emit 完所有 chunk,导致首连收不到内容。
    first_queue = run.broadcaster.subscribe()
    try:
        await run.start(factory)
    except Exception as exc:
        run.broadcaster.unsubscribe(first_queue)
        await active_run_registry.mark_done(conv_id, "failed", error="run_start_failed")
        logger.exception("[Agent] failed to start run_id=%s", run.run_id)
        raise HTTPException(status_code=500, detail="AI 助手任务启动失败，请重试") from exc
    return DataStreamResponse(subscriber_stream(run, first_queue))


async def subscriber_stream(
    run: ActiveRun,
    queue: "asyncio.Queue | None" = None,
    *,
    replay_from: int | None = None,
):
    """首连/续流共用的订阅流:从指定 chunk 游标继续发送 data-stream。

    queue: 首连传入预先 subscribe 的 queue (确保在后台 task 启动前已订阅);
           续流留空,内部 subscribe。

    刷新恢复时,历史消息由 conversations detail 的 messages/resume_state 恢复;
    resume 只负责从 after_chunk_index 之后继续推增量。生成结束
    broadcaster.mark_finished 向 queue 投 None 哨兵,本 generator 自然结束。
    """
    broadcaster = run.broadcaster
    if queue is None:
        queue = broadcaster.subscribe(replay_from=replay_from)
    try:
        # 先消费游标之后的历史 chunk,再接后续实时 chunk,直到 None 哨兵。
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        broadcaster.unsubscribe(queue)


@router.post("/agent/chat/resume")
async def agent_chat_resume(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """续流端点:attach 到进行中的 run;无活跃 run 返回 {active: false}。"""
    try:
        body = await request.json()
        body_for_validation = dict(body) if isinstance(body, dict) else body
        if isinstance(body_for_validation, dict):
            body_for_validation["resume_existing"] = True
        _, conversation_id, _ = validate_chat_request_body(body_for_validation)
    except AgentRequestValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体不是有效 JSON"},
        )
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")
    session_service = ChatSessionService(db_manager)
    if not session_service.get_conversation(conversation_id):
        # This endpoint is an idempotent attachment probe.  A deleted or stale
        # conversation has no resumable run, which is equivalent to inactive.
        return JSONResponse(status_code=200, content={"active": False})
    run = active_run_registry.get(conversation_id)
    if run is None or not run.is_running:
        return JSONResponse(status_code=200, content={"active": False})
    try:
        replay_from = max(0, int(body.get("after_chunk_index") or 0))
    except (TypeError, ValueError):
        replay_from = 0
    return DataStreamResponse(subscriber_stream(run, replay_from=replay_from))
