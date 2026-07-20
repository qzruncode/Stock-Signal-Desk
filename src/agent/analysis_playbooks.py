# -*- coding: utf-8 -*-
"""Executable research standards for the Stock Agent.

The model may write and reason, but it must not decide whether a high-stakes
stock question deserves a professional workflow.  This module selects the
workflow deterministically and supplies the evidence calls and output contract
that the runtime enforces before synthesis.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional

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
        "候选发现必须按用户指定的每个领域分别遍历结构化概念板块成分，并与本地 stock_meta 全量证券库交叉核验；不得用 search_stocks 的名称搜索或通用网页搜索生成候选。",
        "主题板块成员只能作为 L1 候选证据；不能因为属于概念板块就写成业务受益、订单兑现、主营收入或投资建议。",
        "产品级领域没有同名板块时，只能使用运行时声明的最窄结构化板块别名，并明确展示映射口径。",
        "公司与证券代码必须来自本地证券库核验，禁止凭模型记忆补充或改写名单。",
    ),
    output_contract=(
        "按领域分别列出本轮返回的全部公司/代码，不得固定截成 8 家或 12 家代表公司。",
        "每个领域同时展示实际使用的结构化板块、覆盖状态和候选数量；相同公司可出现在多个领域。",
        "明确说明名单是 L1 板块候选，不自动代表相关订单、收入兑现或适合买入。",
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


COLLECTION_FINANCIAL_FILTER = AnalysisPlaybook(
    id="collection_financial_filter",
    title="上文股票集合财务阈值筛选",
    evidence_standard=(
        "股票范围只取紧邻上一条回答表格中明确列出的全部公司，不扩展到全市场。",
        "只读取用户指定的财务字段；资产负债率筛选不得附带实时行情、K线或技术指标。",
        "集合超过12只时按每批最多12只执行，并核对所有批次与原集合的完整覆盖。",
        "任一批失败或字段缺失时必须列出缺失代码，不得把部分结果冒充完整筛选结论。",
    ),
    output_contract=(
        "明确回显阈值、比较符和筛除/保留语义。",
        "列出筛除项、筛选后保留项、报告期、同步时间和数据来源。",
        "给出请求数、成功覆盖数和缺失数；恰好等于阈值时按比较符精确处理。",
    ),
)


def select_playbook_for_intent(intent: ResearchIntent) -> Optional[AnalysisPlaybook]:
    """Map a validated semantic intent to an execution contract.

    The semantic resolver has already produced a closed enum; runtime code only
    selects the corresponding evidence contract.
    """
    return {
        "industry_chain": INDUSTRY_CHAIN,
        "theme_company_mapping": THEME_COMPANY_MAPPING,
        "investment_decision": INVESTMENT_DECISION,
        "stock_research": STOCK_DEEP_RESEARCH,
        "comparison": STOCK_DEEP_RESEARCH,
        "risk_check": STOCK_DEEP_RESEARCH,
        "collection_financial_filter": COLLECTION_FINANCIAL_FILTER,
        "quantitative_screening": QUANTITATIVE_SCREENING,
    }.get(intent.kind)


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"playbook_{uuid.uuid4().hex}",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def mandatory_tool_calls(
    playbook: Optional[AnalysisPlaybook],
    verified_entities: list[dict[str, str]],
    intent: ResearchIntent,
) -> list[dict[str, Any]]:
    """Build the evidence calls that must run before model synthesis."""
    if playbook is None:
        return []
    if playbook.id == QUANTITATIVE_SCREENING.id:
        if intent.quantitative_screen_spec is None:
            return []
        return [_call("screen_atr_volatility_stocks", {
            "screen_spec": intent.quantitative_screen_spec.model_dump(mode="json"),
            "refresh_if_stale": True,
        })]
    if playbook.id == COLLECTION_FINANCIAL_FILTER.id:
        symbols = [item["symbol"] for item in verified_entities if item.get("symbol")]
        return [
            _call("get_multi_stock_financials", {
                "symbols": ",".join(symbols[index:index + 12]),
            })
            for index in range(0, len(symbols), 12)
        ]
    topic = intent.normalized_topic
    semantic_dimensions = " ".join(
        " ".join(str(item).split())[:32]
        for item in intent.research_dimensions[:12]
        if str(item).strip()
    ).strip()
    chain_terms = semantic_dimensions
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
        domains = intent.resolved_domains
        if intent.company_mapping_mode == "business_evidence":
            discovery_theme = intent.normalized_discovery_theme or topic
            return [
                _call("get_theme_stock_candidates", {
                    "theme": discovery_theme,
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
        return [
            _call("get_domain_stock_candidates", {
                "domains": domains,
                # ``topic`` is the human-readable research subject and may be
                # a long phrase that is not a real concept board. Candidate
                # intersection must use the semantic layer's normalized board
                # name, otherwise the broad unfiltered domain pool survives.
                "context_theme": intent.normalized_discovery_theme or topic,
                "limit_per_domain": 300,
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
    "COLLECTION_FINANCIAL_FILTER",
    "mandatory_tool_calls",
    "select_playbook_for_intent",
]
