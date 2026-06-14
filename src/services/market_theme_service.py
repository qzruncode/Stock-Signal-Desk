# -*- coding: utf-8 -*-
"""Market mainline analysis based on public multi-source data only.

The public-data stack may transitively touch native dependencies inside third-party
libraries. To keep the FastAPI worker healthy, the mainline analysis is executed
inside an isolated subprocess and the parent process only parses the structured
result. If the child crashes or times out, the API falls back to cached/minimal
output instead of taking down the serving process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

import litellm

from src.ai_caller import call_ai_structured, current_shanghai_timestamp
from src.config import extra_litellm_params, get_api_keys_for_model, get_config
from src.llm.generation_params import apply_litellm_generation_params
from src.storage import DatabaseManager, persist_llm_usage

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "market_theme_analysis:v3"
_JSON_MARKER = "__MARKET_THEME_JSON__="

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


def _cache_key(layer: str) -> str:
    return f"{_CACHE_PREFIX}:{layer}:{datetime.now().strftime('%Y%m%d%H')}"


def _cache_get(layer: str) -> Optional[dict]:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(layer))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.debug("market theme cache read failed", exc_info=True)
    return None


def _cache_put(layer: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(_cache_key(layer), json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.debug("market theme cache write failed", exc_info=True)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _shorten(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _normalize_texts(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            result.append(text)
    return result


def _pick_theme_profile(name: str, headlines: list[str]) -> dict[str, Any]:
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


def _infer_stage(change_pct: Optional[float], net_flow: Optional[float], headline_count: int) -> tuple[str, str]:
    pct = change_pct or 0.0
    flow = net_flow or 0.0
    if pct >= 4 and flow > 0:
        return "加速期", "板块涨幅和资金共振，已经从单点异动进入一致性强化阶段。"
    if pct >= 2 or flow > 0 or headline_count >= 2:
        return "发酵期", "资金开始持续回流，题材从个别标的扩散到板块层面。"
    if pct < 0 and headline_count >= 1:
        return "分歧期", "逻辑仍在，但价格反馈转弱，说明资金开始出现分歧。"
    return "预热期", "有催化和线索，但市场定价还不充分，更多停留在试探阶段。"


def _profile_by_title(title: str) -> dict[str, Any]:
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


class MarketThemeService:
    """Build a market-mainline analysis using public market and information sources."""

    REPORT_KEY = "market_mainline"

    def analyze(self, *, force: bool = False, use_llm: bool = True) -> dict[str, Any]:
        del use_llm
        if not force:
            cached = _cache_get("all")
            if cached:
                cached["_cached"] = True
                cached["llm_used"] = False
                cached["model_used"] = None
                cached["fallback_used"] = True
                return cached

        result = self._run_isolated(force=force, layer="all")
        if result is None:
            cached = _cache_get("all")
            if cached:
                cached["_cached"] = True
                cached["llm_used"] = False
                cached["model_used"] = None
                cached["fallback_used"] = True
                cached.setdefault("degraded_reason", "isolated_runner_failed")
                return cached

            result = self._build_minimal_fallback()

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        result.setdefault("data_time", datetime.now().date().isoformat())
        result["llm_used"] = False
        result["model_used"] = None
        result["fallback_used"] = True
        if not result.get("_cached"):
            _cache_put("all", result)
        return result

    def get_model_report(self, *, force: bool = False) -> dict[str, Any]:
        del force
        latest = self._get_latest_report()
        if latest:
            latest["_cached"] = True
            latest["report_pending"] = False
            latest["llm_used"] = bool(latest.get("llm_used"))
            latest.setdefault("model_used", None)
            return latest
        payload = self._build_minimal_model_report()
        payload["_cached"] = False
        payload["llm_used"] = False
        payload["model_used"] = None
        payload["report_pending"] = True
        return payload

    def get_model_report_for_tool(self, *, include_debug_input: bool = False) -> dict[str, Any]:
        """Return the same market-mainline report object the page consumes.

        This tool-facing view intentionally reuses the exact report payload from
        the page/cache layer so the model reasons over the same structured data
        the user sees, instead of a separately summarized projection.
        """
        payload = dict(self.get_model_report(force=False))
        if not include_debug_input:
            payload.pop("debug_input", None)
        return payload

    def submit_model_report_task(self, *, force: bool = True):
        from src.services.task_queue import get_task_queue

        task_queue = get_task_queue()
        task_id = __import__("uuid").uuid4().hex

        def _run_task() -> dict[str, Any]:
            return self._generate_model_report_stream(force=force, task_queue=task_queue, task_id=task_id)

        task_info = task_queue.submit_background_task(
            _run_task,
            stock_code="MARKET_MAINLINE",
            stock_name="市场主线",
            report_type="market_mainline_report",
            message="市场主线模型研判任务已加入队列",
            task_id=task_id,
        )
        return task_info

    def _generate_model_report_isolated_task(
        self,
        *,
        force: bool,
        task_queue: Any,
        task_id: str,
    ) -> dict[str, Any]:
        task_queue.update_task_progress(task_id, 5, "正在启动独立研判进程")
        task_queue.update_task_result(
            task_id,
            {
                "phase": "starting_subprocess",
                "stream_text": "",
                "report_draft": {},
            },
            progress=12,
            message="市场主线模型研判已切换到隔离进程执行",
        )

        payload = self._run_isolated(layer="report_llm", force=force, timeout=180)
        if payload is None:
            raise RuntimeError("独立研判进程异常退出，未生成报告")

        task_queue.update_task_result(
            task_id,
            {
                "phase": "finalizing",
                "stream_text": str(payload.get("full_report") or ""),
                "report_draft": {
                    "overview": payload.get("overview"),
                    "full_report": payload.get("full_report"),
                    "as_of_date": payload.get("as_of_date"),
                    "market_stage": payload.get("market_stage"),
                },
                "debug_input": payload.get("debug_input"),
            },
            progress=95,
            message="独立研判进程已完成，正在整理结果",
        )

        return {
            "phase": "completed",
            "stream_text": str(payload.get("full_report") or ""),
            "report_draft": {
                "overview": payload.get("overview"),
                "full_report": payload.get("full_report"),
                "as_of_date": payload.get("as_of_date"),
                "market_stage": payload.get("market_stage"),
            },
            "debug_input": payload.get("debug_input"),
            "report": payload,
            "llm_used": payload.get("llm_used", False),
            "model_used": payload.get("model_used"),
        }

    def get_summary(self, *, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = _cache_get("summary")
            if cached:
                cached["_cached"] = True
                return cached

        context = self._collect_context(force=force, include_rss=False)
        result = self._build_summary_response(context)
        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        if not result.get("_cached"):
            _cache_put("summary", result)
        return result

    def get_evidence(self, *, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = _cache_get("evidence")
            if cached:
                cached["_cached"] = True
                return cached

        result = self._run_isolated(force=force, layer="evidence")
        if result is None:
            cached = _cache_get("evidence")
            if cached:
                cached["_cached"] = True
                cached.setdefault("degraded_reason", "isolated_runner_failed")
                return cached
            result = self._build_minimal_evidence_fallback()

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        if not result.get("_cached"):
            _cache_put("evidence", result)
        return result

    def get_insight(self, *, force: bool = False, use_llm: bool = False) -> dict[str, Any]:
        cache_layer = "insight_llm" if use_llm else "insight"
        if not force:
            cached = _cache_get(cache_layer)
            if cached:
                cached["_cached"] = True
                cached.setdefault("llm_used", use_llm)
                return cached

        if use_llm:
            result = self._run_isolated(force=force, layer="insight_llm")
            if result is None:
                cached = _cache_get(cache_layer)
                if cached:
                    cached["_cached"] = True
                    cached.setdefault("llm_used", use_llm)
                    cached.setdefault("degraded_reason", "isolated_runner_failed")
                    return cached
                evidence = self.get_evidence(force=force)
                result = self._build_insight_response(evidence)
        else:
            result = self._run_isolated(force=force, layer="insight")
            if result is None:
                cached = _cache_get(cache_layer)
                if cached:
                    cached["_cached"] = True
                    cached.setdefault("llm_used", use_llm)
                    cached.setdefault("degraded_reason", "isolated_runner_failed")
                    return cached
                evidence = self.get_evidence(force=force)
                result = self._build_insight_response(evidence)

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        result.setdefault("llm_used", False)
        if not result.get("_cached"):
            _cache_put(cache_layer, result)
        return result

    def _run_isolated(self, *, force: bool, layer: str, timeout: int = 35) -> Optional[dict]:
        cmd = [sys.executable, "-m", "src.services.market_theme_service"]
        if force:
            cmd.append("--force")
        cmd.extend(["--layer", layer])
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("market theme isolated runner timed out: layer=%s timeout=%ss", layer, timeout)
            return None
        except Exception:
            logger.exception("market theme isolated runner failed to start")
            return None

        stdout = completed.stdout or ""
        payload_line = next((line for line in stdout.splitlines() if line.startswith(_JSON_MARKER)), None)
        if completed.returncode != 0 or not payload_line:
            logger.error(
                "market theme isolated runner failed: code=%s stderr=%s stdout_tail=%s",
                completed.returncode,
                (completed.stderr or "").strip()[-1200:],
                stdout.strip()[-1200:],
            )
            return None

        try:
            payload = json.loads(payload_line[len(_JSON_MARKER):])
            if isinstance(payload, dict):
                return payload
        except Exception:
            logger.exception("market theme isolated runner returned invalid json")
        return None

    def _build_minimal_fallback(self) -> dict[str, Any]:
        return {
            "generated_at": current_shanghai_timestamp(),
            "headline": "市场主线研判暂时降级为基础模式，请稍后重试。",
            "market_regime": "服务降级",
            "primary_judgement": "本次请求未能完成完整的多源公开数据聚合，已返回最小可用结果以避免页面整体不可用。",
            "investment_takeaway": "建议稍后刷新，或先关注已验证的主流板块和官方/交易所新增信息。",
            "policy_watchlist": [
                "检查服务运行状态是否正常",
                "稍后重试主线研判接口",
                "优先参考官方/交易所最新披露信息",
            ],
            "current_themes": [],
            "next_themes": [],
            "source_notes": ["当前为降级结果，未完成本轮完整聚合"],
            "source_summary": {"official_count": 0, "news_count": 0, "report_count": 0, "source_catalog": []},
            "source_snapshot": {},
            "_cached": False,
            "degraded_reason": "isolated_runner_failed",
        }

    def _build_minimal_model_report(self) -> dict[str, Any]:
        return {
            "generated_at": current_shanghai_timestamp(),
            "as_of_date": datetime.now().date().isoformat(),
            "overview": "市场主线模型研判暂不可用，请稍后重试。",
            "full_report": "本次未能完成模型直出研判，当前仅保留最小可用结果。",
            "market_stage": {
                "label": "服务降级",
                "description": "模型报告暂时不可用。",
            },
            "current_mainlines": [],
            "future_mainlines": [],
            "action_summary": ["稍后重试模型研判接口。"],
            "evidence_digest": {"policy": [], "industry": [], "market": []},
            "source_summary": {"official_count": 0, "news_count": 0, "report_count": 0, "source_catalog": []},
            "llm_used": False,
            "model_used": None,
        }

    def _build_minimal_evidence_fallback(self) -> dict[str, Any]:
        return {
            "generated_at": current_shanghai_timestamp(),
            "market_stage": {"label": "服务降级", "description": "证据层暂时不可用，请稍后刷新。"},
            "current_themes": [],
            "next_themes": [],
            "policy_watchlist": [],
            "source_notes": ["当前为降级结果，未完成证据聚合"],
            "source_summary": {"official_count": 0, "news_count": 0, "report_count": 0, "source_catalog": []},
            "source_snapshot": {},
            "_cached": False,
            "degraded_reason": "isolated_runner_failed",
        }

    def _collect_context(self, *, force: bool, include_rss: bool = True) -> dict[str, Any]:
        from api.v1.endpoints.macro import get_market_breadth, get_sector_flow
        from api.v1.endpoints.market_status import get_market_status
        from api.v1.endpoints.sectors import get_sector_list

        market_status = get_market_status(force=force)
        breadth = get_market_breadth()
        industry_sectors = get_sector_list(type="industry", force=force)
        concept_sectors = get_sector_list(type="concept", force=force)
        industry_flow = get_sector_flow(type="industry", top_n=8)
        concept_flow = get_sector_flow(type="concept", top_n=8)

        rss_context: dict[str, Any] = {}
        if include_rss:
            from api.v1.endpoints.rss import get_rss_feeds

            rss_sources = [
                ("policy_calendar", {"source": "wallstreetcn_calendar", "limit": 8, "force": force}),
                ("market_news", {"source": "cls", "category": "telegraph", "limit": 8, "force": force}),
                ("strategy_reports", {"source": "eastmoney_report", "category": "strategyreport", "limit": 6, "force": force}),
                ("macro_reports", {"source": "eastmoney_report", "category": "macresearch", "limit": 6, "force": force}),
                ("industry_reports", {"source": "eastmoney_report", "category": "industry", "limit": 6, "force": force}),
                ("exchange_inquire", {"source": "sse_inquire", "limit": 6, "force": force}),
                ("exchange_disclosure", {"source": "sse_disclosure", "limit": 6, "force": force}),
                ("money_center", {"source": "chinamoney", "limit": 6, "force": force}),
            ]
            for key, params in rss_sources:
                try:
                    rss_context[key] = get_rss_feeds(**params)
                except Exception as exc:
                    logger.warning("market theme rss source failed: %s", key, exc_info=True)
                    rss_context[key] = {
                        "source": params["source"],
                        "items": [],
                        "errors": [str(exc)],
                        "_fetched_at": datetime.now().isoformat(),
                        "_cached": False,
                    }

        source_catalog = self._build_source_catalog(rss_context)
        return {
            "generated_at": current_shanghai_timestamp(),
            "source_snapshot": {
                "market_status": market_status,
                "market_breadth": breadth,
                "industry_sectors": (industry_sectors.get("items") or [])[:12],
                "concept_sectors": (concept_sectors.get("items") or [])[:12],
                "industry_flow": {
                    "inflow_top": (industry_flow.get("inflow_top") or [])[:8],
                    "outflow_top": (industry_flow.get("outflow_top") or [])[:8],
                },
                "concept_flow": {
                    "inflow_top": (concept_flow.get("inflow_top") or [])[:8],
                    "outflow_top": (concept_flow.get("outflow_top") or [])[:8],
                },
                "rss": rss_context,
                "source_catalog": source_catalog,
            },
        }

    def _get_latest_report(self) -> Optional[dict[str, Any]]:
        try:
            current_as_of_date = self._current_report_as_of_date()
            return DatabaseManager.get_instance().get_latest_market_mainline_report(
                report_key=self.REPORT_KEY,
                mode="llm",
                as_of_date=current_as_of_date,
            )
        except Exception:
            logger.exception("读取最新市场主线模型报告失败")
            return None

    def _current_report_as_of_date(self) -> str:
        try:
            from api.v1.endpoints.market_status import get_market_status

            market_status = get_market_status(force=False)
            data_time = (market_status or {}).get("data_time")
            if data_time:
                return str(data_time)[:10]
        except Exception:
            logger.exception("读取市场主线当前分析日期失败")
        return datetime.now().date().isoformat()

    def _build_report_evidence_pack(self, context: dict[str, Any]) -> dict[str, Any]:
        snapshot = context["source_snapshot"]
        rss = snapshot.get("rss") or {}
        return {
            "generated_at": context["generated_at"],
            "as_of_date": snapshot.get("market_status", {}).get("data_time") or datetime.now().date().isoformat(),
            "market_status": snapshot.get("market_status") or {},
            "market_breadth": snapshot.get("market_breadth") or {},
            "industry_inflow_top": (snapshot.get("industry_flow") or {}).get("inflow_top") or [],
            "industry_outflow_top": (snapshot.get("industry_flow") or {}).get("outflow_top") or [],
            "concept_inflow_top": (snapshot.get("concept_flow") or {}).get("inflow_top") or [],
            "concept_outflow_top": (snapshot.get("concept_flow") or {}).get("outflow_top") or [],
            "industry_sectors": snapshot.get("industry_sectors") or [],
            "concept_sectors": snapshot.get("concept_sectors") or [],
            "policy_headlines": self._summarize_feed_items(rss.get("policy_calendar")),
            "market_news": self._summarize_feed_items(rss.get("market_news")),
            "strategy_reports": self._summarize_feed_items(rss.get("strategy_reports")),
            "macro_reports": self._summarize_feed_items(rss.get("macro_reports")),
            "industry_reports": self._summarize_feed_items(rss.get("industry_reports")),
            "exchange_disclosure": self._summarize_feed_items(rss.get("exchange_disclosure")),
            "exchange_inquire": self._summarize_feed_items(rss.get("exchange_inquire")),
            "money_center": self._summarize_feed_items(rss.get("money_center")),
            "source_summary": self._summarize_sources(snapshot),
        }

    def _summarize_feed_items(self, feed: Optional[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
        items = (feed or {}).get("items") or []
        results: list[dict[str, str]] = []
        for item in items[:limit]:
            results.append({
                "title": str(item.get("title") or "").strip(),
                "summary": _shorten(_strip_html(str(item.get("summary") or "")), 180),
                "published": str(item.get("published") or ""),
            })
        return results

    def _build_summary_response(self, context: dict[str, Any]) -> dict[str, Any]:
        snapshot = context["source_snapshot"]
        themes = self._build_rule_themes(snapshot, [])
        next_themes = self._build_next_themes(snapshot, [])
        market_regime = self._summarize_market_regime(snapshot["market_status"], snapshot["market_breadth"])
        leading = themes[0] if themes else None
        return {
            "generated_at": context["generated_at"],
            "headline": (
                f"当前A股主线不是单线，更像 { ' / '.join(theme['name'] for theme in themes[:3]) } 并行。"
                if themes else "当前市场主线仍偏轮动，尚未形成特别稳定的单一方向。"
            ),
            "market_regime": market_regime,
            "market_stage": self._build_market_stage(snapshot["market_status"], snapshot["market_breadth"], themes),
            "current_themes": [
                {
                    "name": theme["name"],
                    "stage": theme["stage"],
                    "rank_label": theme.get("rank_label"),
                    "components": theme.get("components", []),
                    "stage_reason": theme.get("stage_reason"),
                }
                for theme in themes[:3]
            ],
            "next_theme_pool": [candidate["name"] for candidate in next_themes[:4]],
            "investment_takeaway": (
                f"当前更该围绕 {leading['name']} 这类仍有产业和资金共振的方向做取舍，"
                "而不是追逐纯情绪题材。"
                if leading else "当前更适合等待更清晰的主线聚焦。"
            ),
            "source_snapshot": {
                "market_status": snapshot["market_status"],
                "market_breadth": snapshot["market_breadth"],
            },
        }

    def _build_response(self, context: dict[str, Any]) -> dict[str, Any]:
        snapshot = context["source_snapshot"]
        headlines = self._collect_headlines(snapshot["rss"])
        themes = self._build_rule_themes(snapshot, headlines)
        next_themes = self._build_next_themes(snapshot, headlines)

        market_status = snapshot["market_status"]
        breadth = snapshot["market_breadth"]
        market_regime = self._summarize_market_regime(market_status, breadth)

        headline = (
            f"当前市场不是单一主线，而是由“{themes[0]['name']}”领衔、"
            f"“{themes[1]['name']}”与“{themes[2]['name']}”共同构成主线梯队。"
            if len(themes) >= 3
            else (f"当前更像“{themes[0]['name']}”领衔的结构性主线。" if themes else "当前市场更像多方向轮动，尚未形成特别清晰且稳定的单一主线。")
        )
        primary = (
            "这版结果不是在猜下一个概念名，而是把板块涨跌、资金流、官方公开信息、行业研报和政策线索"
            "收敛成几个能被用户直接理解的叙事级主线，再判断它们分别处在预热、发酵、加速还是分歧阶段。"
        )
        takeaway = (
            "优先关注具备政策催化、产业趋势验证和估值性价比三者共振的方向；"
            "对只有短线热度、缺少中期逻辑支撑的题材保持克制。"
        )
        return {
            "generated_at": context["generated_at"],
            "headline": headline,
            "market_regime": market_regime,
            "primary_judgement": primary,
            "investment_takeaway": takeaway,
            "policy_watchlist": self._build_policy_watchlist(headlines),
            "current_themes": themes,
            "next_themes": next_themes,
            "source_notes": [
                "公开市场数据：市场状态、市场宽度、行业/概念板块、板块资金流向",
                "官方/交易所信息：上交所问询、上交所披露、中国外汇交易中心公开信息",
                "公共资讯与研报：财联社电报、华尔街见闻日历、东方财富策略/宏观/行业研报",
            ],
            "source_summary": self._summarize_sources(snapshot),
            "source_snapshot": snapshot,
        }

    def _build_evidence_response(self, context: dict[str, Any]) -> dict[str, Any]:
        full = self._build_response(context)
        snapshot = context["source_snapshot"]
        return {
            "generated_at": context["generated_at"],
            "market_stage": self._build_market_stage(snapshot["market_status"], snapshot["market_breadth"], full["current_themes"]),
            "current_themes": full["current_themes"],
            "next_themes": full["next_themes"],
            "policy_watchlist": full["policy_watchlist"],
            "source_notes": full["source_notes"],
            "source_summary": full["source_summary"],
            "source_snapshot": full["source_snapshot"],
        }

    def _build_insight_response(self, evidence: dict[str, Any]) -> dict[str, Any]:
        current_themes = evidence.get("current_themes") or []
        next_themes = evidence.get("next_themes") or []
        market_stage = evidence.get("market_stage") or {"label": "结构轮动", "description": "当前市场仍在多方向轮动。"}
        leading = current_themes[0] if current_themes else None
        second = current_themes[1] if len(current_themes) > 1 else None
        third = current_themes[2] if len(current_themes) > 2 else None

        overview = (
            f"我的判断是：当前A股不是单一主线，而是“{leading['name']} + {second['name']} + {third['name']}”三条线并行，"
            f"其中最强主线是 {leading['name']}，市场整体处在{market_stage.get('label')}。"
            if leading and second and third
            else "我的判断是：当前市场仍以结构轮动为主，尚未形成完全单一的主线。"
        )
        lifecycle_notes = []
        for theme in current_themes[:3]:
            lifecycle_notes.append({
                "theme": theme["name"],
                "stage": theme["stage"],
                "judgement": theme.get("thesis") or "",
                "reason": theme.get("stage_reason") or "",
                "action": self._build_trade_action(theme),
            })
        future_outlook = [
            {
                "name": item["name"],
                "why_now": item["why_now"],
                "stage_hint": "候选观察期",
            }
            for item in next_themes[:4]
        ]
        return {
            "generated_at": evidence.get("generated_at") or current_shanghai_timestamp(),
            "overview": overview,
            "market_stage": market_stage,
            "lifecycle_notes": lifecycle_notes,
            "future_outlook": future_outlook,
            "deep_summary": self._build_deep_summary(market_stage, lifecycle_notes, future_outlook),
            "llm_used": False,
            "model_used": None,
        }

    def _build_llm_insight_response(self, evidence: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            from src.analyzer import get_analyzer
        except Exception:
            logger.exception("market insight LLM analyzer import failed")
            return None

        analyzer = get_analyzer()
        if not getattr(analyzer, "is_available", lambda: False)():
            logger.info("market insight LLM skipped because analyzer is unavailable")
            return None

        payload = {
            "generated_at": evidence.get("generated_at"),
            "market_stage": evidence.get("market_stage"),
            "current_themes": [
                {
                    "name": item.get("name"),
                    "rank_label": item.get("rank_label"),
                    "stage": item.get("stage"),
                    "components": item.get("components"),
                    "thesis": item.get("thesis"),
                    "stage_reason": item.get("stage_reason"),
                    "policy_signal": item.get("policy_signal"),
                    "industry_trend": item.get("industry_trend"),
                    "valuation_view": item.get("valuation_view"),
                    "expectation_view": item.get("expectation_view"),
                    "risks": item.get("risks"),
                    "evidence": item.get("evidence"),
                }
                for item in (evidence.get("current_themes") or [])[:3]
            ],
            "next_themes": (evidence.get("next_themes") or [])[:4],
            "policy_watchlist": evidence.get("policy_watchlist") or [],
        }
        system_prompt = (
            "你是A股策略分析师。你不能编造数据，也不能引入材料包之外的新事实。"
            "你的任务是基于给定证据包，把零散证据整理成像资深研究员写的市场主线判断。"
            "输出必须是严格 JSON，不要包含 markdown，不要包含代码块。"
            "请使用简洁、专业、带交易含义的中文。"
        )
        user_prompt = (
            "请根据下面的证据包，输出 JSON，字段必须完全匹配：\n"
            "{\n"
            '  "overview": "一句话总判断，说明当前不是单一主线还是多主线并行，以及最强主线是谁",\n'
            '  "market_stage": {"label": "整个市场所处阶段", "description": "1-2句说明"},\n'
            '  "lifecycle_notes": [\n'
            '    {"theme":"主线名","stage":"所处阶段","judgement":"这条线是什么","reason":"为什么这么判断","action":"交易含义"}\n'
            "  ],\n"
            '  "future_outlook": [\n'
            '    {"name":"候选方向","why_now":"为什么值得观察","stage_hint":"候选观察期/早中期/左侧观察期"}\n'
            "  ],\n"
            '  "deep_summary": "最后一句收束，告诉用户当前最值得深挖和未来更有预期差的方向"\n'
            "}\n\n"
            "要求：\n"
            "1. lifecycle_notes 只保留最重要的 3 条主线。\n"
            "2. 阶段描述尽量用更像交易语言的表达，例如 主升初期 / 主升中期 / 主升后段 / 高位分歧 / 候选观察期。\n"
            "3. 主线命名优先使用更像策略会结论的叙事名称，例如：科技成长、资源重估、电力设备与能源基础设施、军工与低空安全、创新药。\n"
            "4. 如果一个主线下包含 半导体 / AI / 新材料 等多个科技分支，优先上提为“科技成长”，不要拘泥于单个细行业名称。\n"
            "5. 如果证据不足，不要瞎拔高，明确写观察期或分歧期。\n"
            "6. future_outlook 最多 4 条。\n"
            "7. 只基于以下证据包作答：\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            response_text, model_used, _usage = call_ai_structured(
                analyzer,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                call_type="market_mainline_insight",
                temperature=0.2,
                max_tokens=4096,
            )
            parsed = json.loads(response_text)
            if not isinstance(parsed, dict):
                return None
            parsed["generated_at"] = evidence.get("generated_at") or current_shanghai_timestamp()
            parsed["llm_used"] = True
            parsed["model_used"] = model_used
            parsed.setdefault("market_stage", evidence.get("market_stage") or {})
            parsed.setdefault("lifecycle_notes", [])
            parsed.setdefault("future_outlook", [])
            parsed.setdefault("deep_summary", "")
            return parsed
        except Exception:
            logger.exception("market insight LLM generation failed")
            return None

    def _build_llm_model_report(self, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            from src.analyzer import get_analyzer
        except Exception:
            logger.exception("market mainline report analyzer import failed")
            return None

        analyzer = get_analyzer()
        if not getattr(analyzer, "is_available", lambda: False)():
            logger.info("market mainline model report skipped because analyzer is unavailable")
            return None

        evidence_pack = self._build_report_evidence_pack(context)
        system_prompt, user_prompt = self._build_model_report_prompts(evidence_pack)

        try:
            response_text, model_used, _usage = call_ai_structured(
                analyzer,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                call_type="market_mainline_report",
                temperature=0.2,
                max_tokens=8192,
            )
            parsed = json.loads(response_text)
            if not isinstance(parsed, dict):
                return None
            parsed.setdefault("generated_at", context["generated_at"])
            parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
            parsed["llm_used"] = True
            parsed["model_used"] = model_used
            parsed.setdefault("current_mainlines", [])
            parsed.setdefault("future_mainlines", [])
            parsed.setdefault("action_summary", [])
            parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
            parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
            parsed["debug_input"] = {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            }
            DatabaseManager.get_instance().save_market_mainline_report(
                report_key=self.REPORT_KEY,
                as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
                mode="llm",
                payload=parsed,
                raw_response=response_text,
                model_used=model_used,
            )
            return parsed
        except Exception:
            logger.exception("market mainline model report generation failed")
            return None

    def _build_model_report_prompts(self, evidence_pack: dict[str, Any]) -> tuple[str, str]:
        system_prompt = (
            "你是A股市场策略研究员。你只能基于给定证据包分析，不能编造材料包之外的事实。"
            "你的任务不是分类关键词，而是从政策、产业、资金、估值、景气和市场风格中直接归纳当前主线。"
            "输出必须是严格 JSON。不要使用 markdown 代码块。"
        )
        user_prompt = (
            "请基于以下证据包，直接完成A股市场主线分析，并输出严格 JSON。\n"
            "字段必须完全匹配：\n"
            "{\n"
            '  "generated_at": "生成时间",\n'
            '  "as_of_date": "YYYY-MM-DD",\n'
            '  "overview": "一句话总判断，直接回答当前主线是什么",\n'
            '  "full_report": "3到6段完整中文研判，像策略会观点，不要列表式流水账",\n'
            '  "market_stage": {"label": "整个市场所处阶段", "description": "1到3句解释"},\n'
            '  "current_mainlines": [\n'
            '    {"name":"主线名称","rank":1,"stage":"生命周期阶段","reason":"为什么它是主线","branches":["核心分支1","核心分支2"],"focus":"当前该看什么","risks":["风险1","风险2"],"evidence":["证据1","证据2","证据3"]}\n'
            "  ],\n"
            '  "future_mainlines": [\n'
            '    {"name":"未来候选主线","stage_hint":"候选观察期/早中期/左侧观察期","reason":"为什么值得跟踪","triggers":["触发条件1","触发条件2"]}\n'
            "  ],\n"
            '  "action_summary": ["交易结论1","交易结论2","交易结论3"],\n'
            '  "evidence_digest": {"policy":["..."],"industry":["..."],"market":["..."]}\n'
            "}\n\n"
            "要求：\n"
            "1. current_mainlines 保留 2 到 4 条，不要只写一条。\n"
            "2. 主线名称由你自己归纳，不要机械照抄细行业名；允许使用 AI科技链、资源重估、出海制造、电力设备与能源基础设施、创新药、军工与低空安全 这类研究表述。\n"
            "3. 生命周期要用更像交易语言的表达，例如 主升初期 / 主升中期 / 主升后段 / 高位分歧 / 候选观察期。\n"
            "4. 如果证据不足，就明确写观察期，不要硬拔高。\n"
            "5. future_mainlines 只保留真正有跟踪价值的方向，不要凑数。\n"
            "6. full_report 要直接回答：当前主线是什么、已经走到什么阶段、未来主线可能是什么。\n"
            f"7. 证据包如下：{json.dumps(evidence_pack, ensure_ascii=False)}"
        )
        return system_prompt, user_prompt

    def _extract_partial_json_string_field(self, raw_text: str, field_name: str) -> Optional[str]:
        marker = f'"{field_name}"'
        field_pos = raw_text.find(marker)
        if field_pos < 0:
            return None
        colon_pos = raw_text.find(":", field_pos + len(marker))
        if colon_pos < 0:
            return None

        quote_pos = None
        for index in range(colon_pos + 1, len(raw_text)):
            if raw_text[index] == '"':
                quote_pos = index
                break
            if not raw_text[index].isspace():
                return None
        if quote_pos is None:
            return None

        chars: list[str] = []
        escaping = False
        closed = False
        for index in range(quote_pos + 1, len(raw_text)):
            ch = raw_text[index]
            if escaping:
                chars.append("\\" + ch)
                escaping = False
                continue
            if ch == "\\":
                escaping = True
                continue
            if ch == '"':
                closed = True
                break
            chars.append(ch)

        fragment = "".join(chars)
        if not fragment and not closed:
            return None

        try:
            if closed:
                return json.loads(f'"{fragment}"')
            repaired = (
                fragment
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
            return repaired.strip() or None
        except Exception:
            return None

    def _build_streaming_report_draft(self, raw_text: str) -> dict[str, Any]:
        draft: dict[str, Any] = {}
        for field_name in ("overview", "full_report", "as_of_date"):
            value = self._extract_partial_json_string_field(raw_text, field_name)
            if value:
                draft[field_name] = value

        stage_label = self._extract_partial_json_string_field(raw_text, "label")
        stage_description = self._extract_partial_json_string_field(raw_text, "description")
        if stage_label or stage_description:
            draft["market_stage"] = {
                "label": stage_label or "",
                "description": stage_description or "",
            }

        return draft

    def _generate_model_report_stream(
        self,
        *,
        force: bool,
        task_queue: Any,
        task_id: str,
    ) -> dict[str, Any]:
        task_queue.update_task_progress(task_id, 5, "正在准备市场主线证据包")
        try:
            context = self._collect_context(force=force, include_rss=True)
        except Exception as exc:
            raise RuntimeError(f"市场数据采集失败: {exc}") from exc

        evidence_pack = self._build_report_evidence_pack(context)
        system_prompt, user_prompt = self._build_model_report_prompts(evidence_pack)
        task_queue.update_task_result(
            task_id,
            {
                "phase": "collecting",
                "stream_text": "",
                "report_draft": {
                    "as_of_date": str((context.get("source_snapshot") or {}).get("market_status", {}).get("data_time") or ""),
                },
                "debug_input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "evidence_pack": evidence_pack,
                },
            },
            progress=18,
            message="证据包已准备完成，等待模型连接",
        )

        task_queue.update_task_result(
            task_id,
            {
                "phase": "waiting_model",
                "debug_input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "evidence_pack": evidence_pack,
                },
            },
            progress=24,
            message="正在连接模型服务",
        )

        try:
            result = self._build_llm_model_report_streaming(
                context,
                evidence_pack=evidence_pack,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                on_text=lambda accumulated_text, draft: task_queue.update_task_result(
                    task_id,
                    {
                        "phase": "generating",
                        "stream_text": accumulated_text,
                        "report_draft": draft,
                        "debug_input": {
                            "system_prompt": system_prompt,
                            "user_prompt": user_prompt,
                            "evidence_pack": evidence_pack,
                        },
                    },
                    progress=min(92, 24 + max(1, len(accumulated_text) // 120)),
                    message="模型已连接，正在生成研判内容",
                ),
            )
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        if not result:
            raise RuntimeError("模型研判生成失败")

        return {
            "phase": "completed",
            "stream_text": result.get("raw_stream_output") or result.get("raw_response") or result.get("full_report") or "",
            "report_draft": {
                "overview": result.get("overview"),
                "full_report": result.get("full_report"),
                "as_of_date": result.get("as_of_date"),
                "market_stage": result.get("market_stage"),
            },
            "debug_input": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            },
            "report": result,
            "llm_used": result.get("llm_used", True),
            "model_used": result.get("model_used"),
        }

    def _build_llm_model_report_streaming(
        self,
        context: dict[str, Any],
        *,
        evidence_pack: Optional[dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        on_text: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        evidence_pack = evidence_pack or self._build_report_evidence_pack(context)
        if system_prompt is None or user_prompt is None:
            system_prompt, user_prompt = self._build_model_report_prompts(evidence_pack)

        accumulated_text = ""
        last_emitted_length = 0

        def _on_stream_text(_delta_text: str, full_text: str) -> None:
            nonlocal accumulated_text, last_emitted_length
            accumulated_text = full_text
            if on_text and (
                len(full_text) - last_emitted_length >= 180
                or len(full_text) < 180
            ):
                last_emitted_length = len(full_text)
                on_text(full_text, self._build_streaming_report_draft(full_text))

        try:
            raw_response_text, response_text, model_used, usage = self._stream_market_mainline_report_via_litellm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.2,
                max_tokens=8192,
                on_text=_on_stream_text,
            )
            persist_llm_usage(usage, model_used, call_type="market_mainline_report")
            json_payload_text = self._extract_json_object_from_text(raw_response_text) or response_text
            parsed = json.loads(json_payload_text)
            if not isinstance(parsed, dict):
                return None
            parsed.setdefault("generated_at", context["generated_at"])
            parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
            parsed["llm_used"] = True
            parsed["model_used"] = model_used
            parsed.setdefault("current_mainlines", [])
            parsed.setdefault("future_mainlines", [])
            parsed.setdefault("action_summary", [])
            parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
            parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
            parsed["raw_stream_output"] = raw_response_text
            parsed["raw_response"] = raw_response_text
            parsed["debug_input"] = {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            }
            if on_text and raw_response_text and len(raw_response_text) != last_emitted_length:
                on_text(raw_response_text, self._build_streaming_report_draft(raw_response_text))
            DatabaseManager.get_instance().save_market_mainline_report(
                report_key=self.REPORT_KEY,
                as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
                mode="llm",
                payload=parsed,
                raw_response=raw_response_text,
                model_used=model_used,
            )
            return parsed
        except Exception:
            logger.warning(
                "market mainline direct stream failed, falling back to non-stream completion",
                exc_info=True,
            )
            if on_text and accumulated_text and len(accumulated_text) != last_emitted_length:
                on_text(accumulated_text, self._build_streaming_report_draft(accumulated_text))

        try:
            from src.analyzer import get_analyzer
        except Exception:
            logger.exception("market mainline report analyzer import failed")
            raise RuntimeError("模型服务初始化失败")

        analyzer = get_analyzer()
        if not getattr(analyzer, "is_available", lambda: False)():
            logger.info("market mainline model report skipped because analyzer is unavailable")
            raise RuntimeError("模型服务当前不可用")

        try:
            response_text, model_used, _usage = call_ai_structured(
                analyzer,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                call_type="market_mainline_report",
                temperature=0.2,
                max_tokens=8192,
                stream=False,
            )
            parsed = json.loads(response_text)
            if not isinstance(parsed, dict):
                return None
            parsed.setdefault("generated_at", context["generated_at"])
            parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
            parsed["llm_used"] = True
            parsed["model_used"] = model_used
            parsed.setdefault("current_mainlines", [])
            parsed.setdefault("future_mainlines", [])
            parsed.setdefault("action_summary", [])
            parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
            parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
            parsed["raw_stream_output"] = accumulated_text or response_text
            parsed["raw_response"] = accumulated_text or response_text
            parsed["debug_input"] = {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            }
            if on_text and response_text and len(response_text) != last_emitted_length:
                on_text(response_text, self._build_streaming_report_draft(response_text))
            DatabaseManager.get_instance().save_market_mainline_report(
                report_key=self.REPORT_KEY,
                as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
                mode="llm",
                payload=parsed,
                raw_response=accumulated_text or response_text,
                model_used=model_used,
            )
            return parsed
        except Exception as exc:
            logger.exception("market mainline model report streaming generation failed")
            if on_text and accumulated_text and len(accumulated_text) != last_emitted_length:
                on_text(accumulated_text, self._build_streaming_report_draft(accumulated_text))
            raise RuntimeError(f"模型连接失败: {exc}") from exc

    def _resolve_market_mainline_litellm_config(self) -> dict[str, Any]:
        config = get_config()
        model = (config.litellm_model or "").strip()
        if not model:
            raise RuntimeError("模型服务当前不可用")

        api_key: Optional[str] = None
        api_base: Optional[str] = None
        extra_headers: Optional[dict[str, Any]] = None

        for entry in (config.llm_model_list or []):
            if not isinstance(entry, dict):
                continue
            params = entry.get("litellm_params")
            if not isinstance(params, dict):
                continue
            model_name = str(entry.get("model_name") or "").strip()
            wire_model = str(params.get("model") or "").strip()
            if model_name == model or wire_model == model:
                api_key = params.get("api_key") or api_key
                api_base = params.get("api_base") or api_base
                extra_headers = params.get("extra_headers") or extra_headers
                break

        if not api_key:
            keys = get_api_keys_for_model(model, config)
            if keys:
                api_key = keys[0]

        extra = extra_litellm_params(model, config)
        if extra.get("api_base") and not api_base:
            api_base = extra["api_base"]
        if extra.get("extra_headers") and not extra_headers:
            extra_headers = extra["extra_headers"]

        return {
            "model": model,
            "api_key": api_key,
            "api_base": api_base,
            "extra_headers": extra_headers,
            "thinking_enabled": bool(getattr(config, "llm_thinking_enabled", False)),
            "reasoning_effort": getattr(config, "llm_reasoning_effort", "auto"),
            "model_list": config.llm_model_list,
        }

    @staticmethod
    def _extract_market_mainline_stream_parts(delta: Any) -> tuple[str, str]:
        if not delta:
            return "", ""

        content = getattr(delta, "content", None)
        if isinstance(delta, dict):
            content = delta.get("content")

        content_text = ""
        if isinstance(content, str):
            content_text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                else:
                    text = getattr(item, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
            content_text = "".join(parts)

        reasoning = getattr(delta, "reasoning_content", None)
        if isinstance(delta, dict):
            reasoning = delta.get("reasoning_content")
        reasoning_text = reasoning if isinstance(reasoning, str) else ""

        raw_text = "".join(part for part in (reasoning_text, content_text) if part)
        return raw_text, content_text

    @staticmethod
    def _extract_json_object_from_text(raw_text: str) -> Optional[str]:
        text = (raw_text or "").strip()
        if not text:
            return None

        for start, ch in enumerate(text):
            if ch != "{":
                continue
            candidate = text[start:].strip()
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return candidate
        return None

    @staticmethod
    def _normalize_market_mainline_usage(raw_usage: Any) -> dict[str, Any]:
        if raw_usage is None:
            return {}

        def _read(name: str) -> int:
            if isinstance(raw_usage, dict):
                value = raw_usage.get(name)
            else:
                value = getattr(raw_usage, name, None)
            try:
                return int(value or 0)
            except Exception:
                return 0

        usage = {
            "prompt_tokens": _read("prompt_tokens"),
            "completion_tokens": _read("completion_tokens"),
            "total_tokens": _read("total_tokens"),
        }
        return usage if any(usage.values()) else {}

    def _stream_market_mainline_report_via_litellm(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        on_text: Optional[Callable[[str, str], None]] = None,
    ) -> tuple[str, str, str, dict[str, Any]]:
        llm_cfg = self._resolve_market_mainline_litellm_config()

        async def _run() -> tuple[str, str, str, dict[str, Any]]:
            call_kwargs: dict[str, Any] = {
                "model": llm_cfg["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": True,
                "max_tokens": max_tokens,
            }
            if llm_cfg.get("api_key"):
                call_kwargs["api_key"] = llm_cfg["api_key"]
            if llm_cfg.get("api_base"):
                call_kwargs["api_base"] = llm_cfg["api_base"]
            if llm_cfg.get("extra_headers"):
                call_kwargs["extra_headers"] = llm_cfg["extra_headers"]

            call_kwargs = apply_litellm_generation_params(
                call_kwargs,
                llm_cfg["model"],
                temperature,
                model_list=llm_cfg.get("model_list"),
            )

            if llm_cfg["thinking_enabled"] and llm_cfg["reasoning_effort"] != "auto":
                extra_body = call_kwargs.get("extra_body", {})
                extra_body["reasoning_effort"] = llm_cfg["reasoning_effort"]
                call_kwargs["extra_body"] = extra_body

            response = await litellm.acompletion(**call_kwargs)
            raw_chunks: list[str] = []
            content_chunks: list[str] = []
            usage: dict[str, Any] = {}

            async for chunk in response:
                normalized_usage = self._normalize_market_mainline_usage(getattr(chunk, "usage", None))
                if normalized_usage:
                    usage = normalized_usage

                delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                raw_delta_text, content_delta_text = self._extract_market_mainline_stream_parts(delta)
                if not raw_delta_text and not content_delta_text:
                    continue

                if raw_delta_text:
                    raw_chunks.append(raw_delta_text)
                if content_delta_text:
                    content_chunks.append(content_delta_text)
                if on_text:
                    full_text = "".join(raw_chunks)
                    on_text(raw_delta_text or content_delta_text, full_text)

            raw_response_text = "".join(raw_chunks).strip()
            response_text = "".join(content_chunks).strip()
            if not raw_response_text:
                raise RuntimeError(f"{llm_cfg['model']} stream returned empty response")

            return raw_response_text, response_text, llm_cfg["model"], usage

        return asyncio.run(_run())

    def _summarize_market_regime(self, market_status: dict[str, Any], breadth: dict[str, Any]) -> str:
        regime_bits = []
        if (market_status.get("north_flow") or 0) > 0:
            regime_bits.append("北向资金偏正")
        if (breadth.get("advance_decline_ratio") or 0) >= 1.2:
            regime_bits.append("风险偏好扩散")
        if (breadth.get("limit_down_count") or 0) >= (breadth.get("limit_up_count") or 0):
            regime_bits.append("高低切或防御倾向")
        if (breadth.get("advance_decline_ratio") or 0) < 0.5 and (market_status.get("limit_up_count") or 0) >= 50:
            return "普跌环境下的少数主线抱团"
        if regime_bits:
            return " / ".join(regime_bits)
        return "结构性轮动"

    def _build_market_stage(self, market_status: dict[str, Any], breadth: dict[str, Any], themes: list[dict[str, Any]]) -> dict[str, str]:
        leading_stage = themes[0]["stage"] if themes else "预热期"
        adv_ratio = breadth.get("advance_decline_ratio") or 0
        if adv_ratio < 0.5 and leading_stage in ("发酵期", "加速期", "分歧期"):
            return {
                "label": "结构牛中后段 / 主线抱团期",
                "description": "大盘并不是全面普涨，而是少数强主线在普跌环境里抱团推进，市场已经进入强弱分化很明显的阶段。",
            }
        if adv_ratio >= 1 and leading_stage in ("发酵期", "加速期"):
            return {
                "label": "主线扩散期",
                "description": "主线不再只是个别品种活跃，而是开始向分支扩散，市场正在从方向确认走向板块共振。",
            }
        return {
            "label": "业绩验证期",
            "description": "市场已经过了单纯拔估值阶段，接下来更看重产业趋势、订单和盈利兑现是否跟得上。",
        }

    def _build_trade_action(self, theme: dict[str, Any]) -> str:
        stage = str(theme.get("stage") or "")
        name = str(theme.get("name") or "该主线")
        if stage == "加速期":
            return f"{name} 已经进入一致性很强的阶段，更适合做核心分支和龙头，而不是追边缘题材。"
        if stage == "分歧期":
            return f"{name} 已经出现高位分化，后续只能看兑现能力更强的细分，不能再按普涨思路交易。"
        if stage == "发酵期":
            return f"{name} 还在板块扩散阶段，适合沿着产业趋势和资金共振去找中军与低位补涨。"
        return f"{name} 仍在方向确认阶段，更适合观察是否有进一步政策和产业验证。"

    def _build_deep_summary(self, market_stage: dict[str, Any], lifecycle_notes: list[dict[str, Any]], future_outlook: list[dict[str, Any]]) -> str:
        current = "、".join(f"{item['theme']}处于{item['stage']}" for item in lifecycle_notes[:3]) or "当前主线仍不够清晰"
        future = "、".join(item["name"] for item in future_outlook[:3]) or "暂时没有特别明确的接棒方向"
        return f"整体来看，市场处在{market_stage.get('label')}，当前最值得关注的是{current}；未来更有预期差的方向主要看{future}。"

    def _collect_headlines(self, rss_snapshot: dict[str, Any]) -> list[str]:
        headlines: list[str] = []
        for feed in rss_snapshot.values():
            for item in (feed.get("items") or [])[:6]:
                title = str(item.get("title") or "").strip()
                summary = _strip_html(str(item.get("summary") or ""))
                if title:
                    headlines.append(f"{title} {summary}".strip())
        return _normalize_texts(headlines)

    def _build_rule_themes(self, snapshot: dict[str, Any], headlines: list[str]) -> list[dict[str, Any]]:
        candidates = (
            list(snapshot["concept_flow"]["inflow_top"])[:12]
            + list(snapshot["industry_flow"]["inflow_top"])[:12]
            + list(snapshot["concept_sectors"])[:12]
            + list(snapshot["industry_sectors"])[:12]
        )
        grouped: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for item in candidates:
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            matched = [h for h in headlines if any(key.lower() in h.lower() for key in [name, name.replace("概念", ""), name.replace("行业", "")])][:3]
            profile = _pick_theme_profile(name, matched)
            key = profile["title"]
            bucket = grouped.setdefault(key, {
                "title": key,
                "profile": profile,
                "items": [],
                "headlines": [],
                "score": 0.0,
                "flow": 0.0,
                "pct_values": [],
            })
            pct = _safe_float(item.get("change_pct") or item.get("pct_chg"))
            flow = _safe_float(item.get("main_net_inflow") or item.get("net_flow"))
            bucket["items"].append(item)
            bucket["headlines"].extend(matched)
            bucket["pct_values"].append(pct or 0.0)
            bucket["flow"] += flow or 0.0
            bucket["score"] += (pct or 0.0) * 8 + (1.5 if (flow or 0.0) > 0 else 0) + len(matched) * 2

        narratives: list[dict[str, Any]] = []
        for narrative in STRATEGIC_NARRATIVES:
            matched_buckets: list[dict[str, Any]] = []
            for title, bucket in grouped.items():
                component_names = [str(entry.get("name") or "").strip() for entry in bucket["items"] if entry.get("name")]
                haystack = " ".join([title, *component_names, *bucket["headlines"]]).lower()
                if title in narrative["titles"] or any(keyword.lower() in haystack for keyword in narrative["keywords"]):
                    matched_buckets.append(bucket)

            if not matched_buckets:
                continue
            all_items = []
            all_components: list[str] = []
            all_headlines: list[str] = []
            total_flow = 0.0
            total_score = 0.0
            pct_values: list[float] = []
            for bucket in matched_buckets:
                all_items.extend(bucket["items"])
                all_components.extend(str(entry.get("name") or "").strip() for entry in bucket["items"] if entry.get("name"))
                all_headlines.extend(bucket["headlines"])
                total_flow += bucket["flow"]
                total_score += bucket["score"]
                pct_values.extend(bucket["pct_values"])

            dedup_components = list(dict.fromkeys([name for name in all_components if name]))[:6]
            headline_hits = _normalize_texts(all_headlines)[:5]
            avg_change = sum(pct_values) / max(len(pct_values), 1)
            stage, stage_reason = _infer_stage(avg_change, total_flow, len(headline_hits))
            thesis = (
                f"{narrative['name']} 当前能进入主线梯队，核心不是单个板块异动，而是 "
                f"{' / '.join(dedup_components[:4]) or narrative['name']} 这些分支在同一叙事里形成共振。"
            )
            evidence = [
                f"分支共振：{' / '.join(dedup_components) if dedup_components else '--'}",
                f"板块均值涨幅：{avg_change:.2f}%",
                f"资金净流入：{total_flow:.0f}",
            ]
            evidence.extend(_shorten(headline, 90) for headline in headline_hits[:2])
            narratives.append({
                "name": narrative["name"],
                "stage": stage,
                "thesis": thesis,
                "policy_signal": narrative["policy"],
                "industry_trend": narrative["industry"],
                "valuation_view": f"{narrative['valuation']} {self._build_valuation_basis(narrative['name'], narrative['name'])}",
                "expectation_view": "后续重点看政策细则、订单兑现、盈利上修三者里至少有一项是否继续强化。",
                "risks": [
                    "如果只有短线资金博弈、没有中期产业或盈利验证，主线持续性会明显下降。",
                    "如果估值走在基本面前面太多，后续很容易提前进入分歧期。",
                ],
                "evidence": evidence[:5],
                "stage_note": STAGE_DESCRIPTIONS.get(stage, ""),
                "stage_reason": f"{stage_reason} 当前主要由 {' / '.join(dedup_components[:4]) or narrative['name']} 贡献强度。",
                "components": dedup_components,
                "_score": total_score + len(dedup_components) * 2,
            })
        narratives.sort(key=lambda item: item.get("_score", 0), reverse=True)
        themes: list[dict[str, Any]] = []
        for index, item in enumerate(narratives[:3]):
            item["rank_label"] = RANK_LABELS[index] if index < len(RANK_LABELS) else f"主线{index + 1}"
            item.pop("_score", None)
            themes.append(item)
        return themes

    def _build_next_themes(self, snapshot: dict[str, Any], headlines: list[str]) -> list[dict[str, str]]:
        current_titles = {theme["name"] for theme in self._build_rule_themes(snapshot, headlines)}
        candidates: list[dict[str, Any]] = []
        for narrative in STRATEGIC_NARRATIVES:
            title = narrative["name"]
            if title in current_titles:
                continue
            count = sum(1 for headline in headlines if any(keyword.lower() in headline.lower() for keyword in narrative["keywords"]))
            if count <= 0:
                continue
            candidates.append({
                "name": title,
                "score": count,
                "why_now": f"最近公开信息里已经出现与“{title}”相关的政策、产业或研究线索，但还没形成足够强的资金与板块共振，因此更适合放进候选池而不是直接定义成主线。",
                "trigger": "需要再看到更明确的政策落地、订单/招标验证，或者龙头盈利预期上修，才有机会升级成下一阶段主线。",
            })
        candidates.sort(key=lambda item: item["score"], reverse=True)
        results = [{"name": item["name"], "why_now": item["why_now"], "trigger": item["trigger"]} for item in candidates[:4]]
        if results:
            return results

        first_concepts = [item["name"] for item in STRATEGIC_NARRATIVES if item["name"] not in current_titles][:3]
        return [
            {
                "name": name,
                "why_now": "当前板块热度已经抬升，但还需要政策和产业证据进一步确认。",
                "trigger": "观察是否出现连续催化、龙头超预期表现或行业基本面改善。",
            }
            for name in first_concepts
        ]

    def _build_policy_watchlist(self, headlines: list[str]) -> list[str]:
        watchlist: list[str] = []
        samples = [
            ("政策表述是否从方向性鼓励升级到可执行细则", ["政策", "会议", "方案", "意见", "支持"]),
            ("产业订单、招标、资本开支是否开始验证主线逻辑", ["订单", "招标", "扩产", "资本开支", "投资"]),
            ("龙头公司业绩指引是否带来未来两个季度的盈利上修", ["业绩", "预告", "财报", "超预期", "指引"]),
            ("估值修复是否已经透支未来预期", ["估值", "涨停", "新高", "大涨"]),
        ]
        haystack = " ".join(headlines).lower()
        for text, keys in samples:
            if any(key.lower() in haystack for key in keys):
                watchlist.append(text)
        if not watchlist:
            watchlist.extend([
                "后续政策是否有更明确的执行细则",
                "行业需求和订单是否开始验证景气改善",
                "龙头公司估值是否已经提前透支未来预期",
            ])
        return watchlist[:4]

    def _build_source_catalog(self, rss_context: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"name": "同花顺行业板块汇总", "category": "公开市场数据", "credibility": "高", "used": True},
            {"name": "东方财富板块异动/资金流", "category": "公开市场数据", "credibility": "中高", "used": True},
            {"name": "上交所问询与披露", "category": "交易所", "credibility": "高", "used": bool((rss_context.get("exchange_inquire") or {}).get("items") or (rss_context.get("exchange_disclosure") or {}).get("items"))},
            {"name": "中国外汇交易中心公开信息", "category": "官方公开信息", "credibility": "高", "used": bool((rss_context.get("money_center") or {}).get("items"))},
            {"name": "财联社电报 / 华尔街见闻日历", "category": "公共资讯", "credibility": "中高", "used": bool((rss_context.get("market_news") or {}).get("items") or (rss_context.get("policy_calendar") or {}).get("items"))},
            {"name": "东方财富策略/宏观/行业研报", "category": "卖方公开研报", "credibility": "中", "used": bool((rss_context.get("strategy_reports") or {}).get("items") or (rss_context.get("macro_reports") or {}).get("items") or (rss_context.get("industry_reports") or {}).get("items"))},
        ]

    def _summarize_sources(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        rss = snapshot.get("rss") or {}
        return {
            "official_count": sum(1 for key in ("exchange_inquire", "exchange_disclosure", "money_center") if (rss.get(key) or {}).get("items")),
            "news_count": sum(len((rss.get(key) or {}).get("items") or []) for key in ("market_news", "policy_calendar")),
            "report_count": sum(len((rss.get(key) or {}).get("items") or []) for key in ("strategy_reports", "macro_reports", "industry_reports")),
            "source_catalog": snapshot.get("source_catalog") or [],
        }

    def _build_valuation_basis(self, theme_name: str, theme_profile_title: str) -> str:
        if theme_profile_title in ("金融地产", "资源周期"):
            return "当前更适合结合行业 PB、股息率或资产重估逻辑评估估值安全边际。"
        if theme_profile_title in ("人工智能", "半导体", "医药创新", "军工"):
            return "当前更适合结合龙头 PE/PS 与盈利兑现速度，判断预期是否透支。"
        if "新能源" in theme_name or theme_profile_title == "新能源":
            return "当前更适合结合行业出清进度、单位盈利和龙头估值分位判断修复空间。"
        return "建议继续补充龙头公司估值分位和盈利预测变化，确认性价比。"


def run_public_analysis(force: bool = False) -> dict[str, Any]:
    service = MarketThemeService()
    context = service._collect_context(force=force, include_rss=True)
    result = service._build_response(context)
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    result["llm_used"] = False
    result["model_used"] = None
    result["fallback_used"] = True
    return result


def run_public_evidence(force: bool = False) -> dict[str, Any]:
    service = MarketThemeService()
    context = service._collect_context(force=force, include_rss=True)
    result = service._build_evidence_response(context)
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    return result


def run_public_insight(force: bool = False) -> dict[str, Any]:
    evidence = run_public_evidence(force=force)
    service = MarketThemeService()
    result = service._build_insight_response(evidence)
    result["_cached"] = False
    result["data_time"] = evidence.get("data_time")
    return result


def run_public_insight_llm(force: bool = False) -> dict[str, Any]:
    evidence = run_public_evidence(force=force)
    service = MarketThemeService()
    result = service._build_llm_insight_response(evidence) or service._build_insight_response(evidence)
    result["_cached"] = False
    result["data_time"] = evidence.get("data_time")
    return result


def run_public_model_report(force: bool = False) -> dict[str, Any]:
    service = MarketThemeService()
    context = service._collect_context(force=force, include_rss=True)
    result = service._build_llm_model_report(context) or service._build_minimal_model_report()
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    return result


def _main() -> int:
    force = "--force" in sys.argv[1:]
    layer = "all"
    if "--layer" in sys.argv[1:]:
        try:
            layer = sys.argv[sys.argv.index("--layer") + 1]
        except (ValueError, IndexError):
            layer = "all"

    if layer == "summary":
        payload = MarketThemeService().get_summary(force=force)
    elif layer == "evidence":
        payload = run_public_evidence(force=force)
    elif layer == "insight":
        payload = run_public_insight(force=force)
    elif layer == "insight_llm":
        payload = run_public_insight_llm(force=force)
    elif layer == "report_llm":
        payload = run_public_model_report(force=force)
    else:
        payload = run_public_analysis(force=force)
    sys.stdout.write(f"{_JSON_MARKER}{json.dumps(payload, ensure_ascii=False)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
