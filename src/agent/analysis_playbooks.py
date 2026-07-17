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
        "逐家公司给证据完整度；缺少业务收入、历史估值或一致预期时只能给等待验证，不能给确定性买入。",
        "结论必须结合期限与风险边界，使用可研究候选/等待验证/暂不买入/风险规避，不承诺收益。",
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


def infer_research_topic(messages: list[dict[str, Any]]) -> str:
    """Return a stable research topic from the current conversation."""
    texts = _user_texts(messages)
    for text in reversed(texts):
        match = re.search(r"([\u4e00-\u9fffA-Za-z0-9·+\-]{2,24})产业链", text)
        if match:
            topic = match.group(1)
            # Strip conversational prefixes repeatedly.  A single alternation
            # only removed ``帮我`` from ``帮我分析下人形机器人`` and leaked
            # ``分析下`` into every discovery query.
            previous = None
            while topic != previous:
                previous = topic
                topic = re.sub(r"^(?:帮我|请|分析下|分析一下|看看)", "", topic)
            return topic or match.group(1)
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
        and any(marker in latest for marker in ("公司", "标的", "映射", "名单"))
    ):
        return THEME_COMPANY_MAPPING
    if "产业链" in latest and any(marker in latest for marker in ("受益", "环节", "领域", "分析")):
        return INDUSTRY_CHAIN
    if has_entities and any(
        marker in latest
        for marker in ("分析", "研究", "基本面", "财务", "估值", "风险", "前景", "怎么样", "比较")
    ):
        return STOCK_DEEP_RESEARCH
    return None


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"playbook_{uuid.uuid4().hex}",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def mandatory_tool_calls(
    playbook: Optional[AnalysisPlaybook],
    messages: list[dict[str, Any]],
    verified_entities: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Build the evidence calls that must run before model synthesis."""
    if playbook is None:
        return []
    topic = infer_research_topic(messages)
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
                "query": f"{topic} 核心零部件 价值量 国产替代 降本 丝杠 减速器 伺服 传感器",
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
                "theme": topic,
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
                "query": f"{topic} 产业链 A股 标的 丝杠 减速器 伺服 电机 传感器 灵巧手 机器视觉",
                "category": "industry",
                "days": 1095,
                "limit": 30,
                "include_content": True,
            }),
            _call("search_financial_news", {
                "query": f"{topic} A股 上市公司 公告 主营构成 量产交付 订单金额 营业收入",
                "topic": "industry",
                "days": 730,
                "limit": 30,
                "include_content": True,
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
    "infer_research_topic",
    "mandatory_tool_calls",
    "select_analysis_playbook",
]
