# -*- coding: utf-8 -*-
"""Cache, text-processing, theme-profile and stage-inference utilities."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "market_theme_analysis:v3"

THEME_KEYWORDS: dict[str, dict[str, Any]] = {
    "人工智能": {
        "keywords": ["人工智能", "ai", "算力", "大模型", "机器人", "智能驾驶", "服务器", "cpo"],
        "policy": "关注算力基础设施、数据要素、AI 应用落地和产业扶持政策是否继续强化。",
        "industry": "观察模型能力提升、算力资本开支、应用渗透率和订单兑现是否持续。",
        "valuation": "AI 方向通常预期先行，若缺少业绩验证，估值波动会显著放大。",
    },
    "半导体": {
        "keywords": ["半导体", "芯片", "存储", "先进封装", "晶圆", "设备", "光刻"],
        "policy": "重点看国产替代、先进制程支持和产业链安全相关政策。",
        "industry": "观察库存周期、资本开支、终端需求恢复和国产化率提升节奏。",
        "valuation": "景气修复阶段估值可提前反映预期，但需警惕复苏斜率不及预期。",
    },
    "新能源": {
        "keywords": ["新能源", "光伏", "储能", "风电", "锂电", "固态电池", "钒电池", "钠电池", "锂", "锂矿", "硅料", "硅片", "电动车"],
        "policy": "看国内外补贴、装机政策、电网投资和产业出海环境变化。",
        "industry": "需跟踪产能出清、价格企稳、技术迭代和需求恢复强度。",
        "valuation": "板块常见估值与盈利双杀后的修复，关键在于盈利是否真正见底。",
    },
    "医药创新": {
        "keywords": ["创新药", "医药", "医疗", "减肥药", "生物", "器械", "cxo"],
        "policy": "关注集采边际变化、审批提速、医保谈判和创新支持政策。",
        "industry": "看临床进展、BD 授权、出海验证和医院需求恢复。",
        "valuation": "创新药更看管线预期与商业化验证，低估值本身不等于主线成立。",
    },
    "军工": {
        "keywords": ["军工", "商业航天", "低空", "卫星", "无人机", "航空发动机"],
        "policy": "重点关注军费投入、装备列装、低空经济与航天产业政策。",
        "industry": "看订单释放、型号定型、产业链扩产与场景落地。",
        "valuation": "军工通常对远期兑现敏感，预期修正速度往往快于业绩确认。",
    },
    "金融地产": {
        "keywords": ["券商", "保险", "银行", "地产", "房地产", "并购重组"],
        "policy": "主要受稳增长、地产托底、资本市场改革和流动性预期驱动。",
        "industry": "看成交活跃度、信用修复、地产销售和风险偏好回升。",
        "valuation": "这类方向更偏估值修复逻辑，关键是政策力度能否形成持续预期差。",
    },
    "资源周期": {
        "keywords": ["有色", "黄金", "铜", "煤炭", "石油", "化工", "稀土", "小金属", "稀缺资源", "钼", "钨", "锂", "工业金属", "能源金属", "贵金属"],
        "policy": "跟踪供给约束、资源安全和海外通胀/地缘因素。",
        "industry": "看商品价格趋势、库存周期和资本开支节奏。",
        "valuation": "周期股更依赖价格中枢，估值修复持续性取决于景气拐点是否成立。",
    },
    "新材料": {
        "keywords": ["电子化学品", "半导体材料", "新材料", "聚氨酯", "工业气体", "碳纤维", "化工新材料", "非金属材料", "金属新材料"],
        "policy": "重点看国产替代、新材料攻关、先进制造配套和关键材料安全相关政策。",
        "industry": "关注材料涨价、认证导入、下游扩产以及国产替代率提升。",
        "valuation": "新材料更依赖产品渗透与盈利弹性，既不能只看题材，也不能忽视周期属性。",
    },
    "核电电力": {
        "keywords": ["核电", "核能", "电力", "电网", "绿电", "能源发电"],
        "policy": "关注电力投资、电网升级、核电核准和能源安全相关政策。",
        "industry": "看项目核准、设备招标、装机节奏和运营端盈利改善。",
        "valuation": "公用事业属性更强，估值弹性通常弱于成长线，持续性更依赖订单与分红逻辑。",
    },
}

STAGE_DESCRIPTIONS = {
    "预热期": "有题材和催化，但市场还在观察，资金只做试探性定价。",
    "发酵期": "市场开始认可逻辑，板块扩散、资金回流、关注度明显抬升。",
    "加速期": "板块进入一致性最强阶段，涨幅、资金和讨论度同步升温。",
    "分歧期": "高位开始分化，继续上行要看催化和兑现能否支撑，否则容易退潮。",
}

RANK_LABELS = ["主线", "次主线", "伴生线"]

STRATEGIC_NARRATIVES: list[dict[str, Any]] = [
    {
        "name": "科技成长",
        "titles": {"人工智能", "半导体", "新材料"},
        "keywords": [
            "人工智能", "ai", "算力", "半导体", "芯片", "存储", "先进封装", "服务器",
            "电子化学品", "半导体材料", "高带宽内存", "工业气体", "cpo", "机器人",
        ],
        "policy": "核心看科技自立自强、算力基础设施、国产替代和先进制造配套政策是否继续加码。",
        "industry": "核心看算力资本开支、国产替代进度、材料认证导入和下游放量是否形成持续验证。",
        "valuation": "科技成长最怕只有预期没有兑现，后续要区分真景气和纯题材。",
    },
    {
        "name": "资源重估",
        "titles": {"资源周期"},
        "keywords": [
            "有色", "稀缺资源", "小金属", "工业金属", "能源金属", "贵金属", "钼", "钨",
            "铜", "黄金", "锂", "锂矿", "煤炭", "石油",
        ],
        "policy": "核心看资源安全、供给约束、地缘因素与全球再工业化带来的资源重估逻辑。",
        "industry": "核心看商品价格中枢、库存周期、供给扰动与资本开支约束是否继续支撑景气。",
        "valuation": "资源重估不是单纯低估值修复，更依赖价格中枢和供给约束持续性。",
    },
    {
        "name": "电力设备与能源基础设施",
        "titles": {"新能源", "核电电力"},
        "keywords": [
            "新能源", "光伏", "储能", "风电", "钒电池", "固态电池", "核电", "核能", "电网",
            "绿电", "能源发电", "光伏设备", "电力设备", "电缆", "特高压", "智能电表",
        ],
        "policy": "核心看电网投资、能源安全、出海制造和新能源高质量发展相关政策。",
        "industry": "核心看装机、招标、出口订单、产能出清和盈利修复是否进入验证阶段。",
        "valuation": "这一方向介于成长与制造之间，要看订单兑现而不是只看题材热度。",
    },
    {
        "name": "军工与低空安全",
        "titles": {"军工"},
        "keywords": ["军工", "商业航天", "低空", "卫星", "无人机", "航空发动机", "军工电子"],
        "policy": "核心看装备列装、低空经济与安全产业政策是否持续强化。",
        "industry": "核心看订单释放、型号定型、资产整合与场景落地是否跟上。",
        "valuation": "军工与低空更适合看订单和资产变化，单纯题材驱动的持续性通常不够。",
    },
    {
        "name": "创新药",
        "titles": {"医药创新"},
        "keywords": ["创新药", "医药", "医疗", "减肥药", "生物", "器械", "cxo"],
        "policy": "核心看审批、医保、创新支持和出海相关制度环境是否改善。",
        "industry": "核心看临床、BD、商业化和医院需求恢复是否形成验证。",
        "valuation": "创新药的关键不是便宜，而是管线兑现和全球化能力。",
    },
]


def cache_key(layer: str) -> str:
    return f"{_CACHE_PREFIX}:{layer}:{datetime.now().strftime('%Y%m%d%H')}"


def cache_get(layer: str) -> Optional[dict]:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(cache_key(layer))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.debug("market theme cache read failed", exc_info=True)
    return None


def cache_put(layer: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(cache_key(layer), json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.debug("market theme cache write failed", exc_info=True)


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def shorten(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def normalize_texts(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            result.append(text)
    return result


def pick_theme_profile(name: str, headlines: list[str]) -> dict[str, Any]:
    haystack = " ".join([name, *headlines]).lower()
    for title, profile in THEME_KEYWORDS.items():
        if any(keyword.lower() in haystack for keyword in profile["keywords"]):
            return {"title": title, **profile}
    return {
        "title": name,
        "keywords": [],
        "policy": "需要继续确认是否存在持续性的政策加码或制度红利。",
        "industry": "需要继续验证产业景气、订单、招标或需求修复是否形成闭环。",
        "valuation": "需要结合龙头估值、盈利预测和市场预期差判断性价比。",
    }


def infer_stage(change_pct: Optional[float], net_flow: Optional[float], headline_count: int) -> tuple[str, str]:
    pct = change_pct or 0.0
    flow = net_flow or 0.0
    if pct >= 4 and flow > 0:
        return "加速期", "板块涨幅和资金共振，已经从单点异动进入一致性强化阶段。"
    if pct >= 2 or flow > 0 or headline_count >= 2:
        return "发酵期", "资金开始持续回流，题材从个别标的扩散到板块层面。"
    if pct < 0 and headline_count >= 1:
        return "分歧期", "逻辑仍在，但价格反馈转弱，说明资金开始出现分歧。"
    return "预热期", "有催化和线索，但市场定价还不充分，更多停留在试探阶段。"


def profile_by_title(title: str) -> dict[str, Any]:
    profile = THEME_KEYWORDS.get(title)
    if profile:
        return {"title": title, **profile}
    return {
        "title": title,
        "keywords": [title],
        "policy": "需要继续确认是否存在持续性的政策加码或制度红利。",
        "industry": "需要继续验证产业景气、订单、招标或需求修复是否形成闭环。",
        "valuation": "需要结合龙头估值、盈利预测和市场预期差判断性价比。",
    }