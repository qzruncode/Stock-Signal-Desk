# -*- coding: utf-8 -*-
"""Executable research standards for the Stock Agent.

The model may write and reason, but it must not decide whether a high-stakes
stock question deserves a professional workflow.  This module selects the
workflow deterministically and supplies the evidence calls and output contract
that the runtime enforces before synthesis.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from src.agent.research_intent import ResearchIntent


@dataclass(frozen=True)
class AnalysisPlaybook:
    id: str
    title: str
    evidence_standard: tuple[str, ...]
    output_contract: tuple[str, ...]

    def system_instruction(self) -> str:
        evidence = "\n".join(f"{index}. {item}" for index, item in enumerate(self.evidence_standard, 1))
        output = "\n".join(f"{index}. {item}" for index, item in enumerate(self.output_contract, 1))
        return (
            f"## 强制分析标准：{self.title}（{self.id}）\n"
            "这是运行时选定的专业 Playbook，不是建议。不得跳步、降级成只看一个指标，"
            "也不得在证据缺失时用常识补齐。\n\n"
            f"### 证据标准\n{evidence}\n\n"
            f"### 输出合同\n{output}"
        )


INDUSTRY_CHAIN = AnalysisPlaybook(
    id="industry_chain_research",
    title="产业链受益环节研究",
    evidence_standard=(
        "至少同时取得近期产业资讯与跨机构行业研究，标明时间、来源和信息级别。",
        "按上游核心部件、中游制造/平台、下游应用与横向基础设施拆解，不以概念标签代替价值传导。",
        "逐环节检查价值量、放量弹性、竞争壁垒、国产替代、订单/产能兑现和降价风险。",
        "同时寻找支持证据与反证；计划值、预测值和已实现数据必须分开。",
        "首轮资料不足时必须继续用价值量、市场空间、国产替代、订单和产能等定向检索补证；只有补证仍失败的关键项才标为待验证。",
    ),
    output_contract=(
        "先给受益优先级与判断口径，再给产业链地图。",
        "每个重点环节写明受益机制、兑现指标、受益节奏和主要反证。",
        "关键事实使用本轮证据中的可点击 Markdown 来源链接，至少引用两个独立来源。",
        "结尾列出最重要的持续跟踪指标、截至时间和置信度；证据限制集中成一小段，禁止用大篇幅‘证据缺失’表格代替产业判断。",
    ),
)


THEME_COMPANY_MAPPING = AnalysisPlaybook(
    id="theme_company_mapping",
    title="产业主题到 A 股公司的证据化映射",
    evidence_standard=(
        "候选发现必须完整遍历主题板块全部分页并与本地 stock_meta 全量证券库交叉召回，再用产业资讯、研报、公告和财报核验；不得只读概念板块第一页、只从几篇新闻里找公司，也不得凭模型记忆扩展名单。",
        "逐家公司按三层证据分级：L3 已披露相关收入/批量订单；L2 客户定点/送样/验证；L1 技术储备或概念关联。",
        "主题板块成员只能作为 L1 候选证据；不能因为属于概念板块就写成业务受益、订单兑现或主营收入。",
        "若某行唯一来源是新浪/同花顺概念板块，该行产业链环节必须写待公司级业务核验，已验证事实只能写板块成员关系和本地代码核验；不得补写龙头、主营、产品用途、订单、收入或客户。",
        "媒体转述、券商推测、公司公告和财报披露必须分开；低等级证据不能写成高等级兑现。",
        "RSS、跨机构研报、通用网页搜索与网页正文爬取必须全部执行并记录覆盖；不得因为 RSS 已返回少量结果就跳过网页搜索，也不得把未执行补证写成公开资料不足。",
        "每家公司至少给一个可核验来源与日期；没有直接证据则明确写缺口。",
        "公司与证券代码必须来自本地证券库核验，禁止猜代码。",
    ),
    output_contract=(
        "用 Markdown 表格列出公司/代码、产业链环节、证据等级、已验证事实、缺失证据与来源日期；公司/代码必须放在第一列，供后续追问确定范围。每家公司来源单元格必须含本轮证据中的可点击链接和日期。",
        "完整列出本轮返回的全部候选公司：L2/L3 公司进入证据表，L1 概念候选用紧凑的公司/代码索引完整展示；禁止固定截成 8 家或 12 家代表公司。",
        "单独列出仅概念关联和证据陈旧的公司，避免与业绩兑现标的混排。",
        "结尾用一小段说明候选源覆盖率、公司级证据覆盖率和最关键的待核验项；不得逐家公司重复同一句证据缺口。",
    ),
)


INVESTMENT_DECISION = AnalysisPlaybook(
    id="professional_investment_decision",
    title="专业股票买入决策",
    evidence_standard=(
        "对每家公司完整检查主题/主营兑现、连续财务趋势、现金流与资产负债质量，不得因某一项不通过而提前停止。",
        "估值同时检查 PE(TTM)、PB、历史/行业相对水平、远期 PE/PEG 与一致预期覆盖，不得只看动态 PE。",
        "交易状态同时检查趋势、波动、量价和 5/10/20 日资金持续性；资金流口径不等同机构持仓。",
        "检查正式公告、规则筛查风险、催化兑现条件和市场宽度；无风险事件不等于公司没有风险。",
        "逐家公司给证据完整度；必查来源执行异常时必须重试或补证。机构一致预期覆盖为0是有效负面事实，不是证据缺失。",
        "结论必须结合期限与风险边界，使用可研究候选/暂不介入（条件未满足）/暂不买入/风险规避；只有来源调用确实失败时才标数据源异常，不承诺收益。",
    ),
    output_contract=(
        "先回答当前是否具备介入条件，再给全部公司的决策矩阵，不能只挑一两家公司。",
        "每家公司至少包含：业务兑现、财务质量、估值与预期、交易状态、催化/风险、结论、成立条件和失效条件。",
        "区分事实与推断，所有价格和比率写明数据时间、口径与来源。",
        "最后给横向优先级、共同风险、继续跟踪指标和证据缺口；不得用简单机械评分代替分析。",
    ),
)


STOCK_DEEP_RESEARCH = AnalysisPlaybook(
    id="stock_deep_research",
    title="完整个股研究",
    evidence_standard=INVESTMENT_DECISION.evidence_standard,
    output_contract=(
        "先给公司质量和核心矛盾，再展开业务、财务、估值、预期、交易状态和风险。",
        "给出支持证据、反证、成立条件、失效条件和需跟踪指标。",
        "不主动给确定性买卖指令；用户明确问买卖时应切换专业股票买入决策 Playbook。",
    ),
)


QUANTITATIVE_SCREENING = AnalysisPlaybook(
    id="quantitative_screening",
    title="全市场确定性量化筛选",
    evidence_standard=(
        "证券候选池必须来自本地 stock_meta 全部 active A股，不得由模型枚举候选。",
        "筛选工具必须先刷新财务和前复权日线，并校验来源覆盖与数据日期。",
        "用户条件必须先解析为完整强类型规格；周期、均线类型、动态线、比较符、窗口、财务条件和排序全部由工具按规格精确计算。",
        "工具必须回传实际执行规格和指纹；任何缺失、越界或不支持的条件都必须失败或澄清，禁止静默套用示例默认值。",
        "任何一项缺失或不满足的股票不得进入结果；模型不得补数、改阈值或扩展名单。",
    ),
    output_contract=(
        "只展示工具返回的完全合格股票，并保留工具排序。",
        "明确列出实际执行规格、公式、规格指纹、数据日期、报告期、全市场覆盖统计和来源。",
        "结果超过10条时展示前10条并提供完整文件下载链接。",
        "工具刷新失败时不得给选股结论，必须展示具体失败阶段和覆盖情况。",
    ),
)


_REFERENTIAL_MARKERS = ("这些", "上述", "上面", "前面", "它们", "他们", "those", "them")
_DECISION_MARKERS = (
    "能买吗", "能不能买", "是否能买", "值得买", "买入", "抄底", "入场", "介入",
    "加仓", "减仓", "卖出", "持有", "仓位", "止损", "止盈", "追高", "buy", "sell",
)


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


def _user_texts(messages: Iterable[dict[str, Any]]) -> list[str]:
    return [
        text
        for message in messages
        if isinstance(message, dict) and message.get("role") == "user"
        if (text := _message_text(message))
    ]


_TOPIC_PREFIX_RE = re.compile(
    r"^(?:帮我|请|麻烦|重新|完整|系统地?|仔细|只|梳理|找出|查下|查一下|"
    r"分析下|分析一下|看看|看下|看一下|看(?=上面|前面)|关于|"
    r"上面(?:说的|提到的|分析的)?|前面(?:说的|提到的|分析的)?|上述|这些)\s*",
    re.IGNORECASE,
)


def _strip_topic_prefixes(value: str) -> str:
    """Remove stacked conversational prefixes without eating the topic."""
    topic = value.strip()
    previous = None
    while topic and topic != previous:
        previous = topic
        topic = _TOPIC_PREFIX_RE.sub("", topic).strip()
    return topic


def _topic_from_text(text: str) -> str:
    """Extract an explicit topic from one user turn.

    A follow-up may name a narrower topic (for example ``AI芯片``) even when
    an older turn contains ``AI产业链``.  Extraction therefore happens one
    turn at a time so that the latest explicit subject can win.
    """
    normalized = re.sub(r"[？?。；;：:]", " ", text).strip()

    industry_match = re.search(r"([\u4e00-\u9fffA-Za-z0-9·+\-\s]{2,40})产业链", normalized)
    if industry_match:
        topic = _strip_topic_prefixes(industry_match.group(1))
        return " ".join(topic.split())

    # Keep the subject before a company-mapping or benefit-ranking request.
    # This turns “看下上面说的 AI芯片，有哪些公司核心受益” into ``AI芯片``
    # instead of falling back to an older, broader ``AI产业链`` topic.
    subject = re.split(
        r"[，,\s]*(?:有)?哪些(?:A股|上市)?(?:公司|标的|股票|个股)|"
        r"[，,\s]*(?:哪些|什么)(?:领域|环节)(?:最|核心)?受益|"
        r"[，,\s]*(?:最|核心)受益(?:的)?(?:A股|上市)?(?:公司|标的|股票|个股)",
        normalized,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    subject = _strip_topic_prefixes(subject)
    subject = re.sub(r"(?:这些|上述)?(?:领域|环节|板块)(?:在)?A股$", "", subject).strip()
    subject = re.sub(r"(?:相关)?(?:公司|标的|股票|个股)$", "", subject).strip()
    subject = " ".join(subject.split())
    if len(subject) >= 2 and subject.lower() not in {
        "a股", "公司", "领域", "环节", "板块", "这些领域", "上述领域",
    }:
        return subject[:80]
    return ""


def infer_research_topic(messages: list[dict[str, Any]]) -> str:
    """Return a stable research topic from the current conversation."""
    texts = _user_texts(messages)
    # Resolve each turn from newest to oldest.  Scanning every historical
    # ``产业链`` mention first made an old broad topic override a narrower
    # subject explicitly named in the current question.
    for text in reversed(texts):
        if topic := _topic_from_text(text):
            return topic
    for text in reversed(texts):
        cleaned = re.sub(
            r"(?:帮我|请|分析下|分析一下|看看|上面提到的|这些|上述|现在能买吗|能不能买|有哪些公司|哪些公司)",
            " ",
            text,
        )
        cleaned = re.sub(r"[？?，,。；;：:]", " ", cleaned)
        cleaned = " ".join(cleaned.split())
        if len(cleaned) >= 2:
            return cleaned[:80]
    return texts[-1][:80] if texts else "A股"


def select_analysis_playbook(
    messages: list[dict[str, Any]],
    verified_entities: list[dict[str, str]],
) -> Optional[AnalysisPlaybook]:
    """Select a high-stakes research workflow without delegating to the LLM."""
    texts = _user_texts(messages)
    latest = texts[-1].lower() if texts else ""
    has_entities = bool(verified_entities)
    referential = any(marker in latest for marker in _REFERENTIAL_MARKERS)

    if any(marker in latest for marker in _DECISION_MARKERS) and (has_entities or referential):
        return INVESTMENT_DECISION
    if (
        ("a股" in latest or "上市公司" in latest or "哪些公司" in latest or "哪些标的" in latest)
        and any(marker in latest for marker in ("公司", "标的", "映射", "名单", "股票", "个股"))
    ):
        return THEME_COMPANY_MAPPING
    # Industry-chain research may mention a listed-company name incidentally
    # (for example the stock ``机器人`` inside ``人形机器人``).  Select the
    # topic workflow before generic entity-based deep research whenever the
    # question itself asks for industry-chain dimensions.
    if "产业链" in latest and any(
        marker in latest
        for marker in (
            "受益", "环节", "领域", "分析", "研究", "价值量", "市场空间",
            "竞争格局", "国产替代", "订单", "产能",
        )
    ):
        return INDUSTRY_CHAIN
    if has_entities and any(
        marker in latest
        for marker in ("分析", "研究", "基本面", "财务", "估值", "风险", "前景", "怎么样", "比较")
    ):
        return STOCK_DEEP_RESEARCH
    return None


def select_playbook_for_intent(intent: ResearchIntent) -> Optional[AnalysisPlaybook]:
    """Map a validated semantic intent to an execution contract.

    Unlike :func:`select_analysis_playbook`, this function does not inspect
    wording.  The model has already resolved the meaning into a closed enum;
    runtime code only selects the corresponding evidence contract.
    """
    return {
        "industry_chain": INDUSTRY_CHAIN,
        "theme_company_mapping": THEME_COMPANY_MAPPING,
        "investment_decision": INVESTMENT_DECISION,
        "stock_research": STOCK_DEEP_RESEARCH,
        "comparison": STOCK_DEEP_RESEARCH,
        "risk_check": STOCK_DEEP_RESEARCH,
        "quantitative_screening": QUANTITATIVE_SCREENING,
    }.get(intent.kind)


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"playbook_{uuid.uuid4().hex}",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def _topic_chain_terms(topic: str) -> str:
    """Return topic-specific discovery dimensions instead of one hard-coded chain."""
    lowered = topic.lower()
    if any(marker in lowered for marker in ("人形机器人", "具身智能", "机器人")):
        return "丝杠 减速器 伺服 电机 传感器 灵巧手 机器视觉"
    if any(marker in lowered for marker in ("ai芯片", "人工智能芯片", "算力芯片")):
        return "GPU NPU ASIC 训练芯片 推理芯片 加速卡 HBM 先进封装 国产替代"
    if lowered in {"ai", "人工智能"} or "ai产业" in lowered or "人工智能产业" in lowered:
        return "AI芯片 AI服务器 光模块 液冷 数据中心 云服务 大模型 应用"
    return "核心环节 上游 中游 下游 价值量 国产替代 订单 产能"


def mandatory_tool_calls(
    playbook: Optional[AnalysisPlaybook],
    messages: list[dict[str, Any]],
    verified_entities: list[dict[str, str]],
    intent: Optional[ResearchIntent] = None,
) -> list[dict[str, Any]]:
    """Build the evidence calls that must run before model synthesis."""
    if playbook is None:
        return []
    if playbook.id == QUANTITATIVE_SCREENING.id:
        if intent is None or intent.quantitative_screen_spec is None:
            return []
        return [_call("screen_atr_volatility_stocks", {
            "screen_spec": intent.quantitative_screen_spec.model_dump(mode="json"),
            "refresh_if_stale": True,
        })]
    topic = intent.normalized_topic if intent is not None else infer_research_topic(messages)
    if not topic:
        topic = infer_research_topic(messages)
    semantic_dimensions = " ".join(
        re.sub(r"[^\u4e00-\u9fffA-Za-z0-9·+\-/]", "", str(item))[:32]
        for item in ((intent.research_dimensions if intent is not None else [])[:12])
        if str(item).strip()
    ).strip()
    chain_terms = semantic_dimensions or _topic_chain_terms(topic)
    if playbook.id == INDUSTRY_CHAIN.id:
        return [
            _call("search_financial_news", {
                "query": f"{topic} 产业链 订单 量产 受益环节 风险 实际交付",
                "topic": "industry",
                "days": 180,
                "limit": 20,
                "include_content": True,
            }),
            _call("search_research_library", {
                "query": f"{topic} 产业链 价值量 市场空间 竞争格局",
                "category": "industry",
                "days": 730,
                "limit": 20,
                "include_content": True,
            }),
            _call("search_research_library", {
                "query": f"{topic} 产业链 价值量 国产替代 降本 {chain_terms}",
                "category": "industry",
                "days": 1095,
                "limit": 20,
                "include_content": True,
            }),
            _call("search_financial_news", {
                "query": f"{topic} 实际订单 产能 收入 批量交付 公司公告",
                "topic": "industry",
                "days": 365,
                "limit": 20,
                "include_content": True,
            }),
        ]
    if playbook.id == THEME_COMPANY_MAPPING.id:
        return [
            _call("get_theme_stock_candidates", {
                "theme": intent.normalized_discovery_theme if intent is not None else topic,
                "limit": 1000,
            }),
            _call("search_financial_news", {
                "query": f"{topic} A股 公司 订单 送样 定点 客户验证 收入 批量供货",
                "topic": "industry",
                "days": 365,
                "limit": 30,
                "include_content": True,
            }),
            _call("search_research_library", {
                "query": f"{topic} 产业链 A股 标的 {chain_terms}",
                "category": "industry",
                "days": 1095,
                "limit": 30,
                "include_content": True,
            }),
            _call("websearch", {
                "query": f"{topic} A股 上市公司 公司公告 互动平台 送样 定点 客户验证 订单 收入 批量供货 量产交付",
                "numResults": 12,
                "livecrawl": "preferred",
                "type": "deep",
                "contextMaxCharacters": 50000,
                "includeContent": True,
            }),
        ]
    if playbook.id in {INVESTMENT_DECISION.id, STOCK_DEEP_RESEARCH.id}:
        symbols = ",".join(item["symbol"] for item in verified_entities[:8])
        if not symbols:
            return []
        calls = [
            _call("get_multi_stock_decision_evidence", {
                "symbols": symbols,
                "thesis": topic,
            }),
        ]
        if playbook.id == INVESTMENT_DECISION.id:
            calls.append(_call("get_market_breadth", {}))
        return calls
    return []


__all__ = [
    "AnalysisPlaybook",
    "INDUSTRY_CHAIN",
    "INVESTMENT_DECISION",
    "STOCK_DEEP_RESEARCH",
    "THEME_COMPANY_MAPPING",
    "QUANTITATIVE_SCREENING",
    "infer_research_topic",
    "mandatory_tool_calls",
    "select_analysis_playbook",
    "select_playbook_for_intent",
]
