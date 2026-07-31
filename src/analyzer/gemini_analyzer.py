# -*- coding: utf-8 -*-
"""GeminiAnalyzer: LiteLLM 调用、提示词构造、响应解析、整体分析流程。"""

import contextlib
import json
import logging
import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import litellm
from json_repair import repair_json

from src.config import (
    Config,
    resolve_news_window_days,
)
from src.data.stock_mapping import STOCK_NAME_MAP
from src.llm.anthropic_gateway import (
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.llm.errors import call_litellm_with_param_recovery
from src.llm.generation_params import apply_litellm_generation_params
from src.market_context import get_market_guidelines, get_market_role
from src.report_language import (
    get_no_data_text,
    get_unknown_text,
    infer_decision_type_from_advice,
    localize_confidence_level,
    normalize_report_language,
)
from src.schemas.report_schema import AnalysisReportSchema
from src.storage import persist_llm_usage

from src.analyzer._common import _safe_float
from src.analyzer.integrity import apply_placeholder_fill, check_content_integrity
from src.analyzer.result import AnalysisResult
from src.analyzer.trend_prompt import _sanitize_trend_analysis_for_prompt

logger = logging.getLogger(__name__)


class _LiteLLMStreamError(RuntimeError):
    """Internal error wrapper that records whether any text was streamed."""

    def __init__(self, message: str, *, partial_received: bool = False):
        super().__init__(message)
        self.partial_received = partial_received


class _AllModelsFailedError(Exception):
    """Raised when every model in the fallback chain fails.

    This includes both LLM call errors and JSON parse errors (when a
    ``response_validator`` is provided to :meth:`GeminiAnalyzer._call_litellm`).

    The ``last_response_text`` attribute holds the raw text from the last model
    that *did* return a response (but whose JSON could not be validated), so
    callers can still attempt a best-effort text fallback.

    ``last_model`` and ``last_usage`` record the model name and token usage
    from the last attempt so callers can persist usage even on fallback.
    """

    def __init__(
        self,
        message: str,
        *,
        last_response_text: Optional[str] = None,
        last_model: Optional[str] = None,
        last_usage: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.last_response_text = last_response_text
        self.last_model = last_model
        self.last_usage = last_usage or {}


from ._gemini_methods1 import _GeminiAnalyzerMethods1
from ._gemini_methods2 import _GeminiAnalyzerMethods2
from ._gemini_methods3 import _GeminiAnalyzerMethods3
from ._gemini_methods4 import _GeminiAnalyzerMethods4

class GeminiAnalyzer(_GeminiAnalyzerMethods1, _GeminiAnalyzerMethods2, _GeminiAnalyzerMethods3, _GeminiAnalyzerMethods4):
        """
        Gemini AI 分析器

        职责：
        1. 调用 Google Gemini API 进行股票分析
        2. 结合预先搜索的新闻和技术面数据生成分析报告
        3. 解析 AI 返回的 JSON 格式结果

        使用方式：
            analyzer = GeminiAnalyzer()
            result = analyzer.analyze(context, news_context)
        """
        SYSTEM_PROMPT = """你是一位{market_placeholder}投资分析师，负责生成专业的【决策仪表盘】分析报告。

    {guidelines_placeholder}




    ## 输出格式：决策仪表盘 JSON

    请严格按照以下 JSON 格式输出，这是一个完整的【决策仪表盘】：

    ```json
    {
        "stock_name": "股票中文名称",
        "sentiment_score": 0-100整数,
        "trend_prediction": "强烈看多/看多/震荡/看空/强烈看空",
        "operation_advice": "买入/加仓/持有/减仓/卖出/观望",
        "decision_type": "buy/hold/sell",
        "confidence_level": "高/中/低",

        "dashboard": {
            "core_conclusion": {
                "one_sentence": "一句话核心结论（30字以内，直接告诉用户做什么）",
                "signal_type": "🟢买入信号/🟡持有观望/🔴卖出信号/⚠️风险警告",
                "time_sensitivity": "立即行动/今日内/本周内/不急",
                "position_advice": {
                    "no_position": "空仓者建议：具体操作指引",
                    "has_position": "持仓者建议：具体操作指引"
                }
            },

            "data_perspective": {
                "trend_status": {
                    "ma_alignment": "均线排列状态描述",
                    "is_bullish": true/false,
                    "trend_score": 0-100
                },
                "price_position": {
                    "current_price": 当前价格数值,
                    "ma5": MA5数值,
                    "ma10": MA10数值,
                    "ma20": MA20数值,
                    "bias_ma5": 乖离率百分比数值,
                    "bias_status": "安全/警戒/危险",
                    "support_level": 支撑位价格,
                    "resistance_level": 压力位价格
                },
                "volume_analysis": {
                    "volume_ratio": 量比数值,
                    "volume_status": "放量/缩量/平量",
                    "turnover_rate": 换手率百分比,
                    "volume_meaning": "量能含义解读（如：缩量回调表示抛压减轻）"
                },
                "chip_structure": {
                    "profit_ratio": 获利比例,
                    "avg_cost": 平均成本,
                    "concentration": 筹码集中度,
                    "chip_health": "健康/一般/警惕"
                }
            },

            "intelligence": {
                "latest_news": "【最新消息】近期重要新闻摘要",
                "risk_alerts": ["风险点1：具体描述", "风险点2：具体描述"],
                "has_structural_risk": true/false,
                "risk_level": "none/low/medium/high/critical",
                "positive_catalysts": ["利好1：具体描述", "利好2：具体描述"],
                "earnings_outlook": "业绩预期分析（基于年报预告、业绩快报等）",
                "sentiment_summary": "舆情情绪一句话总结"
            },

            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "理想入场位：XX元（满足主要技能触发条件）",
                    "secondary_buy": "次优入场位：XX元（更保守或确认后执行）",
                    "stop_loss": "止损位：XX元（失效条件或X%风险）",
                    "take_profit": "目标位：XX元（按阻力位/风险回报比制定）"
                },
                "position_strategy": {
                    "suggested_position": "建议仓位：X成",
                    "entry_plan": "分批建仓策略描述",
                    "risk_control": "风控策略描述"
                },
                "action_checklist": [
                    "✅/⚠️/❌ 检查项1：当前结构是否满足激活技能条件",
                    "✅/⚠️/❌ 检查项2：入场位置与风险回报是否合理",
                    "✅/⚠️/❌ 检查项3：量价/波动/筹码是否支持判断",
                    "✅/⚠️/❌ 检查项4：无重大利空",
                    "✅/⚠️/❌ 检查项5：仓位与止损计划明确",
                    "✅/⚠️/❌ 检查项6：估值/业绩/催化与结论匹配"
                ]
            }
        },

        "analysis_summary": "100字综合分析摘要",
        "key_points": "3-5个核心看点，逗号分隔",
        "risk_warning": "风险提示",
        "buy_reason": "操作理由，引用激活技能或风险框架",

        "trend_analysis": "走势形态分析",
        "short_term_outlook": "短期1-3日展望",
        "medium_term_outlook": "中期1-2周展望",
        "technical_analysis": "技术面综合分析",
        "ma_analysis": "均线系统分析",
        "volume_analysis": "量能分析",
        "pattern_analysis": "K线形态分析",
        "fundamental_analysis": "基本面分析",
        "sector_position": "板块行业分析",
        "company_highlights": "公司亮点/风险",
        "news_summary": "新闻摘要",
        "market_sentiment": "市场情绪",
        "hot_topics": "相关热点",

        "search_performed": true/false,
        "data_sources": "数据来源说明"
    }
    ```

    ## 分析要求

    - 对价格、量能、筹码、资金、基本面、估值、新闻与风险分别独立解释证据。
    - 同时给出支持证据、反证、数据缺口和判断失效条件。
    - 不得把任一数值的正负、固定距离、固定分数或单个标签直接换算为买卖结论。
    - `sentiment_score` 只表达模型综合判断，不承担程序阈值或买卖映射。
    - 结论必须来自证据综合；信息不足时明确降低置信度并说明还需补充什么。

    ## 决策仪表盘核心原则

    1. **核心结论先行**：一句话说清该买该卖
    2. **分持仓建议**：空仓者和持仓者给不同建议
    3. **精确狙击点**：必须给出具体价格，不说模糊的话
    4. **检查清单可视化**：用 ✅⚠️❌ 明确显示每项检查结果
    5. **风险优先级**：舆情中的风险点要醒目标出

    ## 可操作性与稳定性约束

    - 不得仅因为单日涨跌或评分变化就在“买入/卖出”之间剧烈切换。
    - 操作建议必须引用本轮实际取得的证据，并解释证据之间的一致、冲突与时效。
    - 支撑、压力、资金流和风险事件都是待解释证据，不能由程序预设它们对应哪一种操作。
    - 若给出买卖建议，必须同时给出可验证的触发条件、失效条件和风险控制方案。"""
        TEXT_SYSTEM_PROMPT = """你是一位专业的股票分析助手。

    - 回答必须基于用户提供的数据与上下文
    - 若信息不足，要明确指出不确定性
    - 不要编造价格、财报或新闻事实
    """


def _bind_mixin_method(_member):
    import functools
    import types

    if isinstance(_member, staticmethod):
        return staticmethod(_bind_mixin_method(_member.__func__))
    if isinstance(_member, classmethod):
        return classmethod(_bind_mixin_method(_member.__func__))
    if isinstance(_member, property):
        return property(
            _bind_mixin_method(_member.fget) if _member.fget else None,
            _bind_mixin_method(_member.fset) if _member.fset else None,
            _bind_mixin_method(_member.fdel) if _member.fdel else None,
            _member.__doc__,
        )
    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(
        _member.__code__,
        globals(),
        _member.__name__,
        _member.__defaults__,
        _member.__closure__,
    )
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _mixin in (_GeminiAnalyzerMethods1, _GeminiAnalyzerMethods2, _GeminiAnalyzerMethods3, _GeminiAnalyzerMethods4):
    for _name, _member in _mixin.__dict__.items():
        if _name != "__dict__" and _name != "__weakref__":
            setattr(GeminiAnalyzer, _name, _bind_mixin_method(_member))


# 便捷函数
def get_analyzer() -> GeminiAnalyzer:
    """获取 LLM 分析器实例"""
    return GeminiAnalyzer()
