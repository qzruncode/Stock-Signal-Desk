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


class GeminiAnalyzer:
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

    # ========================================
    # 系统提示词 - 决策仪表盘 v2.0
    # ========================================
    # 输出格式升级：从简单信号升级为决策仪表盘
    # 核心模块：核心结论 + 数据透视 + 舆情情报 + 作战计划
    # ========================================

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

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        config: Optional[Config] = None,
    ):
        """Initialize LLM Analyzer.

        模型/鉴权统一由 Anthropic 网关配置（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）决定，
        不再在初始化期构建 Router；配置是否齐全在 :meth:`is_available` / 调用时按需检测。

        Args:
            api_key: Ignored (kept for backward compatibility). Keys come from the gateway config.
        """
        self._config_override = config

    def _get_runtime_config(self) -> Config:
        """Return the runtime config, honoring injected overrides for tests/pipeline."""
        # 延迟从 src.analyzer 命名空间取 get_config，保留测试对 `src.analyzer.get_config` 的 patch 能力
        import src.analyzer as _analyzer_pkg

        return getattr(self, "_config_override", None) or _analyzer_pkg.get_config()

    def _get_analysis_system_prompt(self, report_language: str, stock_code: str = "") -> str:
        """Build the analyzer system prompt with output-language guidance."""
        lang = normalize_report_language(report_language)
        market_role = get_market_role(stock_code, lang)
        market_guidelines = get_market_guidelines(stock_code, lang)
        base_prompt = self.SYSTEM_PROMPT.replace("{market_placeholder}", market_role).replace(
            "{guidelines_placeholder}", market_guidelines
        )
        if lang == "en":
            return (
                base_prompt
                + """

## Output Language (highest priority)

- Keep all JSON keys unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in English.
- Use the common English company name when you are confident; otherwise keep the original listed company name instead of inventing one.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, nested dashboard text, checklist items, and all narrative summaries.
"""
            )
        return (
            base_prompt
            + """

## 输出语言（最高优先级）

- 所有 JSON 键名保持不变。
- `decision_type` 必须保持为 `buy|hold|sell`。
- 所有面向用户的人类可读文本值必须使用中文。
"""
        )

    def is_available(self) -> bool:
        """Check if the Anthropic gateway is fully configured (base_url/token/model all set)."""
        import os

        return bool(
            (os.getenv("ANTHROPIC_BASE_URL") or "").strip()
            and (os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()
            and (os.getenv("ANTHROPIC_MODEL") or "").strip()
        )

    def _dispatch_litellm_completion(
        self,
        call_kwargs: Dict[str, Any],
    ) -> Any:
        """Dispatch a sync LiteLLM completion.

        网关单源后无 Router/多供应商分支：kwargs 已由 ``build_litellm_kwargs`` 组装好
        鉴权字段（api_key/api_base/custom_llm_provider/extra_headers），直接调用即可。
        """
        return litellm.completion(**call_kwargs)

    def _normalize_usage(self, usage_obj: Any) -> Dict[str, Any]:
        """Normalize usage objects from LiteLLM responses/chunks."""
        if not usage_obj:
            return {}

        def _get_value(key: str) -> int:
            if isinstance(usage_obj, dict):
                return int(usage_obj.get(key) or 0)
            return int(getattr(usage_obj, key, 0) or 0)

        return {
            "prompt_tokens": _get_value("prompt_tokens"),
            "completion_tokens": _get_value("completion_tokens"),
            "total_tokens": _get_value("total_tokens"),
        }

    @staticmethod
    def _get_response_field(obj: Any, key: str) -> Any:
        """Read a field from dict-like or object-like LiteLLM payloads."""
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _extract_text_blocks(self, blocks: Any) -> str:
        """Extract text from OpenAI-compatible content block lists."""
        if not blocks:
            return ""

        parts: List[str] = []
        for block in blocks:
            if isinstance(block, str):
                parts.append(block)
                continue

            text = None
            if isinstance(block, dict):
                text = block.get("text")
                if text is None:
                    text = block.get("content")
            else:
                text = getattr(block, "text", None)
                if text is None:
                    text = getattr(block, "content", None)

            if isinstance(text, str) and text:
                parts.append(text)

        return "".join(parts).strip()

    def _extract_completion_text(self, response: Any) -> str:
        """Extract text from non-stream LiteLLM completion responses."""
        choices = self._get_response_field(response, "choices")
        if not choices:
            return ""

        choice = choices[0]
        message = self._get_response_field(choice, "message")

        content_blocks = self._get_response_field(choice, "content_blocks")
        if content_blocks is None and message is not None:
            content_blocks = self._get_response_field(message, "content_blocks")
        block_text = self._extract_text_blocks(content_blocks)
        if block_text:
            return block_text

        content = None
        if message is not None:
            content = self._get_response_field(message, "content")
        if content is None:
            content = self._get_response_field(choice, "content")

        if isinstance(content, list):
            return self._extract_text_blocks(content)
        if isinstance(content, str):
            return content.strip()
        return str(content).strip() if content is not None else ""

    def _extract_stream_text(self, chunk: Any) -> str:
        """Extract provider-agnostic text delta from a LiteLLM streaming chunk."""
        choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
        if not choices:
            return ""

        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else getattr(choice, "delta", None)
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)

        content: Any = None
        if isinstance(delta, dict):
            content = delta.get("content")
        elif isinstance(delta, str):
            content = delta
        elif delta is not None:
            content = getattr(delta, "content", None)

        if content is None:
            if isinstance(message, dict):
                content = message.get("content")
            elif message is not None:
                content = getattr(message, "content", None)

        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)

        return content if isinstance(content, str) else ""

    def _consume_litellm_stream(
        self,
        stream_response: Any,
        *,
        model: str,
        progress_callback: Optional[Callable[[int], None]] = None,
        text_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Consume a LiteLLM stream into a single text payload."""
        chunks: List[str] = []
        usage: Dict[str, Any] = {}
        chars_received = 0
        next_emit_at = 1

        try:
            for chunk in stream_response:
                chunk_usage = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
                normalized_usage = self._normalize_usage(chunk_usage)
                if normalized_usage:
                    usage = normalized_usage

                delta_text = self._extract_stream_text(chunk)
                if not delta_text:
                    continue

                chunks.append(delta_text)
                chars_received += len(delta_text)
                if text_callback:
                    text_callback(delta_text, "".join(chunks))
                if progress_callback and chars_received >= next_emit_at:
                    progress_callback(chars_received)
                    next_emit_at = chars_received + 160
        except Exception as exc:
            raise _LiteLLMStreamError(
                f"{model} stream interrupted: {exc}",
                partial_received=chars_received > 0,
            ) from exc

        response_text = "".join(chunks).strip()
        if not response_text:
            raise _LiteLLMStreamError(
                f"{model} stream returned empty response",
                partial_received=False,
            )

        if progress_callback and chars_received > 0:
            progress_callback(chars_received)

        return response_text, usage

    def _call_litellm(
        self,
        prompt: str,
        generation_config: dict,
        *,
        system_prompt: Optional[str] = None,
        stream: bool = False,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
        stream_text_callback: Optional[Callable[[str, str], None]] = None,
        response_validator: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Call LLM via litellm through the Anthropic gateway (single source).

        模型/鉴权统一由 ``ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL`` 决定，无多模型 fallback
        与 Router 路由。thinking/reasoning_effort/temperature 等正交生成参数仍然生效。
        流式失败时自动回退到非流式。所有失败（含响应校验失败）聚合为
        :class:`_AllModelsFailedError`，保留 ``last_response_text/last_model/last_usage``
        以便上游 :meth:`analyze` 用末次文本兜底。

        Args:
            prompt: User prompt text.
            generation_config: Dict with optional keys: temperature, max_output_tokens, max_tokens.
            response_validator: Optional callable that accepts the raw response text and raises
                an exception if the response is unacceptable (e.g. not valid JSON).

        Returns:
            Tuple of (response text, model_used, usage). On success model_used is the full model
            name and usage is a dict with prompt_tokens, completion_tokens, total_tokens.
        """
        config = self._get_runtime_config()
        max_tokens = generation_config.get("max_output_tokens") or generation_config.get("max_tokens") or 8192
        requested_temperature = generation_config.get("temperature", 0.7)

        # 网关单源：缺失即抛错，不回落（与 AI 助手一致）
        llm_cfg = resolve_anthropic_gateway_config()
        model = llm_cfg["model"]

        last_error: Optional[BaseException] = None
        last_response_text: Optional[str] = None
        last_model: Optional[str] = None
        last_usage: Dict[str, Any] = {}
        effective_system_prompt = system_prompt or self.TEXT_SYSTEM_PROMPT

        try:
            call_kwargs = build_litellm_kwargs(
                llm_cfg,
                stream=False,
                messages=[
                    {"role": "system", "content": effective_system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
            )
            call_kwargs = apply_litellm_generation_params(
                call_kwargs,
                model,
                requested_temperature,
            )

            # Inject thinking mode and reasoning effort if configured
            thinking_enabled = getattr(config, "llm_thinking_enabled", False)
            reasoning_effort = getattr(config, "llm_reasoning_effort", "auto")
            if thinking_enabled and reasoning_effort != "auto":
                extra_body = call_kwargs.get("extra_body", {})
                extra_body["reasoning_effort"] = reasoning_effort
                call_kwargs["extra_body"] = extra_body
                logger.debug(
                    "[LiteLLM] Injecting reasoning_effort=%s for model %s (thinking enabled)",
                    reasoning_effort,
                    model,
                )

            _stream_text: Optional[str] = None
            _stream_usage: Dict[str, Any] = {}

            if stream:
                try:
                    stream_response = call_litellm_with_param_recovery(
                        lambda kwargs: self._dispatch_litellm_completion(kwargs),
                        model=model,
                        call_kwargs={**call_kwargs, "stream": True},
                        model_list=None,
                        cache_recovery=False,
                        logger=logger,
                    )
                    with contextlib.closing(stream_response):
                        _stream_text, _stream_usage = self._consume_litellm_stream(
                            stream_response,
                            model=model,
                            progress_callback=stream_progress_callback,
                            text_callback=stream_text_callback,
                        )
                except _LiteLLMStreamError as exc:
                    if exc.partial_received:
                        logger.warning(
                            "[LiteLLM] %s stream failed after partial output, retrying non-stream: %s",
                            model,
                            exc,
                        )
                    else:
                        logger.warning(
                            "[LiteLLM] %s stream unavailable before first chunk, falling back to non-stream: %s",
                            model,
                            exc,
                        )
                    last_error = exc
                except Exception as exc:
                    logger.warning(
                        "[LiteLLM] %s stream request failed before first chunk, falling back to non-stream: %s",
                        model,
                        exc,
                    )
                    last_error = exc

            if _stream_text is not None:
                last_response_text = _stream_text
                last_model = model
                last_usage = _stream_usage
                if response_validator is not None:
                    response_validator(_stream_text)
                return _stream_text, model, _stream_usage

            response = call_litellm_with_param_recovery(
                lambda kwargs: self._dispatch_litellm_completion(kwargs),
                model=model,
                call_kwargs=call_kwargs,
                model_list=None,
                logger=logger,
            )

            content = self._extract_completion_text(response)
            if content:
                usage = self._normalize_usage(self._get_response_field(response, "usage"))
                last_response_text = content
                last_model = model
                last_usage = usage
                if response_validator is not None:
                    response_validator(content)
                return (content, model, usage)
            raise ValueError("LLM returned empty response")

        except Exception as e:
            logger.warning(f"[LiteLLM] {model} failed: {e}")
            last_error = e

        raise _AllModelsFailedError(
            f"LLM call failed for model {model}. Last error: {last_error}",
            last_response_text=last_response_text,
            last_model=last_model,
            last_usage=last_usage,
        )

    def generate_text(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """Public entry point for free-form text generation.

        External callers (e.g. MarketAnalyzer) must use this method instead of
        calling _call_litellm() directly.

        Args:
            prompt:      Text prompt to send to the LLM.
            max_tokens:  Maximum tokens in the response (default 2048).
            temperature: Sampling temperature (default 0.7).

        Returns:
            Response text, or None if the LLM call fails (error is logged).
        """
        try:
            result = self._call_litellm(
                prompt,
                generation_config={"max_tokens": max_tokens, "temperature": temperature},
            )
            if isinstance(result, tuple):
                text, model_used, usage = result
                persist_llm_usage(usage, model_used, call_type="analysis")
                return text
            return result
        except Exception as exc:
            logger.error("[generate_text] LLM call failed: %s", exc)
            return None

    def analyze(
        self,
        context: Dict[str, Any],
        news_context: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
    ) -> AnalysisResult:
        """
        分析单只股票

        流程：
        1. 格式化输入数据（技术面 + 新闻）
        2. 调用 Gemini API（带重试和模型切换）
        3. 解析 JSON 响应
        4. 返回结构化结果

        Args:
            context: 从 storage.get_analysis_context() 获取的上下文数据
            news_context: 预先搜索的新闻内容（可选）

        Returns:
            AnalysisResult 对象
        """

        def _emit_progress(progress: int, message: str) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(progress, message)
            except Exception as exc:
                logger.debug("[analyzer] progress callback skipped: %s", exc)

        code = context.get("code", "Unknown")
        config = self._get_runtime_config()
        report_language = normalize_report_language(getattr(config, "report_language", "zh"))
        system_prompt = self._get_analysis_system_prompt(report_language, stock_code=code)

        # 请求前增加延时（防止连续请求触发限流）
        request_delay = config.gemini_request_delay
        if request_delay > 0:
            logger.debug(f"[LLM] 请求前等待 {request_delay:.1f} 秒...")
            _emit_progress(65, f"{code}：LLM 请求前等待 {request_delay:.1f} 秒")
            time.sleep(request_delay)

        # 优先从上下文获取股票名称（由 main.py 传入）
        name = context.get("stock_name")
        if not name or name.startswith("股票"):
            # 备选：从 realtime 中获取
            if "realtime" in context and context["realtime"].get("name"):
                name = context["realtime"]["name"]
            else:
                # 最后从映射表获取
                name = STOCK_NAME_MAP.get(code, f"股票{code}")

        # 如果模型不可用，返回默认结果
        if not self.is_available():
            return AnalysisResult(
                code=code,
                name=name,
                sentiment_score=50,
                trend_prediction="Sideways" if report_language == "en" else "震荡",
                operation_advice="Hold" if report_language == "en" else "持有",
                confidence_level="Low" if report_language == "en" else "低",
                analysis_summary=(
                    "AI analysis is unavailable because no API key is configured."
                    if report_language == "en"
                    else "AI 分析功能未启用（未配置 API Key）"
                ),
                risk_warning=(
                    "Configure the Anthropic gateway (ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL) and retry."
                    if report_language == "en"
                    else "请配置 Anthropic 网关（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）后重试"
                ),
                success=False,
                error_message="LLM API key is not configured" if report_language == "en" else "LLM API Key 未配置",
                model_used=None,
                report_language=report_language,
            )

        try:
            # 格式化输入（包含技术面数据和新闻）
            prompt = self._format_prompt(context, name, news_context, report_language=report_language)

            config = self._get_runtime_config()
            import os as _os

            model_name = (_os.getenv("ANTHROPIC_MODEL") or "").strip() or "unknown"
            logger.info(f"========== AI 分析 {name}({code}) ==========")
            logger.info(f"[LLM配置] 模型: {model_name}")
            logger.info(f"[LLM配置] Prompt 长度: {len(prompt)} 字符")
            logger.info(f"[LLM配置] 是否包含新闻: {'是' if news_context else '否'}")

            # 记录完整 prompt 到日志（INFO级别记录摘要，DEBUG记录完整）
            prompt_preview = prompt[:500] + "..." if len(prompt) > 500 else prompt
            logger.info(f"[LLM Prompt 预览]\n{prompt_preview}")
            logger.debug(f"=== 完整 Prompt ({len(prompt)}字符) ===\n{prompt}\n=== End Prompt ===")

            # 设置生成配置
            generation_config = {
                "temperature": config.llm_temperature,
                "max_output_tokens": 8192,
            }

            logger.info(f"[LLM调用] 开始调用 {model_name}...")
            _emit_progress(68, f"{name}：LLM 已接收请求，等待响应")

            # 使用 litellm 调用（支持完整性校验重试）
            current_prompt = prompt
            retry_count = 0
            max_retries = config.report_integrity_retry if config.report_integrity_enabled else 0

            while True:
                start_time = time.time()
                try:
                    response_text, model_used, llm_usage = self._call_litellm(
                        current_prompt,
                        generation_config,
                        system_prompt=system_prompt,
                        stream=True,
                        stream_progress_callback=stream_progress_callback,
                        response_validator=self._validate_json_response,
                    )
                except _AllModelsFailedError as exc:
                    if exc.last_response_text is not None:
                        logger.warning(
                            "[LLM JSON] %s(%s): all models returned invalid JSON, using text fallback",
                            name,
                            code,
                        )
                        response_text = exc.last_response_text
                        model_used = exc.last_model
                        llm_usage = exc.last_usage
                    else:
                        raise
                elapsed = time.time() - start_time

                # 记录响应信息
                logger.info(f"[LLM返回] {model_name} 响应成功, 耗时 {elapsed:.2f}s, 响应长度 {len(response_text)} 字符")
                response_preview = response_text[:300] + "..." if len(response_text) > 300 else response_text
                logger.info(f"[LLM返回 预览]\n{response_preview}")
                logger.debug(
                    f"=== {model_name} 完整响应 ({len(response_text)}字符) ===\n{response_text}\n=== End Response ==="
                )
                # Keep parser/retry progress monotonic so task progress/message never "goes backward".
                parse_progress = min(99, 93 + retry_count * 2)
                _emit_progress(parse_progress, f"{name}：LLM 返回完成，正在解析 JSON")

                # 解析响应
                result = self._parse_response(response_text, code, name)
                result.raw_response = response_text
                result.search_performed = bool(news_context)
                result.market_snapshot = self._build_market_snapshot(context)
                result.model_used = model_used
                result.report_language = report_language

                # 内容完整性校验（可选）
                if not config.report_integrity_enabled:
                    break
                pass_integrity, missing_fields = self._check_content_integrity(result)
                if pass_integrity:
                    break
                if retry_count < max_retries:
                    current_prompt = self._build_integrity_retry_prompt(
                        prompt,
                        response_text,
                        missing_fields,
                        report_language=report_language,
                    )
                    retry_count += 1
                    logger.info(
                        "[LLM完整性] 必填字段缺失 %s，第 %d 次补全重试",
                        missing_fields,
                        retry_count,
                    )
                    retry_progress = min(99, 92 + retry_count * 2)
                    _emit_progress(
                        retry_progress,
                        f"{name}：报告字段不完整，正在补全重试（{retry_count}/{max_retries}）",
                    )
                else:
                    self._apply_placeholder_fill(result, missing_fields)
                    logger.warning(
                        "[LLM完整性] 必填字段缺失 %s，已占位补全，不阻塞流程",
                        missing_fields,
                    )
                    break

            persist_llm_usage(llm_usage, model_used, call_type="analysis", stock_code=code)

            logger.info(f"[LLM解析] {name}({code}) 分析完成: {result.trend_prediction}, 评分 {result.sentiment_score}")

            return result

        except Exception as e:
            logger.error(f"AI 分析 {name}({code}) 失败: {e}")
            return AnalysisResult(
                code=code,
                name=name,
                sentiment_score=50,
                trend_prediction="Sideways" if report_language == "en" else "震荡",
                operation_advice="Hold" if report_language == "en" else "持有",
                confidence_level="Low" if report_language == "en" else "低",
                analysis_summary=(
                    f"Analysis failed: {str(e)[:100]}" if report_language == "en" else f"分析过程出错: {str(e)[:100]}"
                ),
                risk_warning=(
                    "Analysis failed. Please retry later or review manually."
                    if report_language == "en"
                    else "分析失败，请稍后重试或手动分析"
                ),
                success=False,
                error_message=str(e),
                model_used=None,
                report_language=report_language,
            )

    def _format_prompt(
        self,
        context: Dict[str, Any],
        name: str,
        news_context: Optional[str] = None,
        report_language: str = "zh",
    ) -> str:
        """
        格式化分析提示词（决策仪表盘 v2.0）

        包含：技术指标、实时行情（量比/换手率）、筹码分布、趋势分析、新闻

        Args:
            context: 技术面数据上下文（包含增强数据）
            name: 股票名称（默认值，可能被上下文覆盖）
            news_context: 预先搜索的新闻内容
        """
        code = context.get("code", "Unknown")
        report_language = normalize_report_language(report_language)
        use_legacy_default_prompt = False

        # 优先使用上下文中的股票名称（从 realtime_quote 获取）
        stock_name = context.get("stock_name", name)
        if not stock_name or stock_name == f"股票{code}":
            stock_name = STOCK_NAME_MAP.get(code, f"股票{code}")

        today = context.get("today", {})
        unknown_text = get_unknown_text(report_language)
        no_data_text = get_no_data_text(report_language)

        # ========== 构建决策仪表盘格式的输入 ==========
        prompt = f"""# 决策仪表盘分析请求

## 📊 股票基础信息
| 项目 | 数据 |
|------|------|
| 股票代码 | **{code}** |
| 股票名称 | **{stock_name}** |
| 分析日期 | {context.get('date', unknown_text)} |

---

## 📈 技术面数据

### 今日行情
| 指标 | 数值 |
|------|------|
| 收盘价 | {today.get('close', 'N/A')} 元 |
| 开盘价 | {today.get('open', 'N/A')} 元 |
| 最高价 | {today.get('high', 'N/A')} 元 |
| 最低价 | {today.get('low', 'N/A')} 元 |
| 涨跌幅 | {today.get('pct_chg', 'N/A')}% |
| 成交量 | {self._format_volume(today.get('volume'))} |
| 成交额 | {self._format_amount(today.get('amount'))} |

### 均线系统（关键判断指标）
| 均线 | 数值 | 说明 |
|------|------|------|
| MA5 | {today.get('ma5', 'N/A')} | 短期趋势线 |
| MA10 | {today.get('ma10', 'N/A')} | 中短期趋势线 |
| MA20 | {today.get('ma20', 'N/A')} | 中期趋势线 |
| 均线形态 | {context.get('ma_status', unknown_text)} | 多头/空头/缠绕 |
"""

        # 添加实时行情数据（量比、换手率等）
        if "realtime" in context:
            rt = context["realtime"]
            prompt += f"""
### 实时行情增强数据
| 指标 | 数值 | 解读 |
|------|------|------|
| 当前价格 | {rt.get('price', 'N/A')} 元 | |
| **量比** | **{rt.get('volume_ratio', 'N/A')}** | {rt.get('volume_ratio_desc', '')} |
| **换手率** | **{rt.get('turnover_rate', 'N/A')}%** | |
| 市盈率(动态) | {rt.get('pe_ratio', 'N/A')} | |
| 市净率 | {rt.get('pb_ratio', 'N/A')} | |
| 总市值 | {self._format_amount(rt.get('total_mv'))} | |
| 流通市值 | {self._format_amount(rt.get('circ_mv'))} | |
| 60日涨跌幅 | {rt.get('change_60d', 'N/A')}% | 中期表现 |
"""

        # 添加财报与分红（价值投资口径）
        fundamental_context = context.get("fundamental_context") if isinstance(context, dict) else None
        earnings_block = fundamental_context.get("earnings", {}) if isinstance(fundamental_context, dict) else {}
        earnings_data = earnings_block.get("data", {}) if isinstance(earnings_block, dict) else {}
        financial_report = earnings_data.get("financial_report", {}) if isinstance(earnings_data, dict) else {}
        dividend_metrics = earnings_data.get("dividend", {}) if isinstance(earnings_data, dict) else {}
        if isinstance(financial_report, dict) or isinstance(dividend_metrics, dict):
            financial_report = financial_report if isinstance(financial_report, dict) else {}
            dividend_metrics = dividend_metrics if isinstance(dividend_metrics, dict) else {}
            ttm_yield = dividend_metrics.get("ttm_dividend_yield_pct", "N/A")
            ttm_cash = dividend_metrics.get("ttm_cash_dividend_per_share", "N/A")
            ttm_count = dividend_metrics.get("ttm_event_count", "N/A")
            report_date = financial_report.get("report_date", "N/A")
            prompt += f"""
### 财报与分红（价值投资口径）
| 指标 | 数值 | 说明 |
|------|------|------|
| 最近报告期 | {report_date} | 来自结构化财报字段 |
| 营业收入 | {financial_report.get('revenue', 'N/A')} | |
| 归母净利润 | {financial_report.get('net_profit_parent', 'N/A')} | |
| 经营现金流 | {financial_report.get('operating_cash_flow', 'N/A')} | |
| ROE | {financial_report.get('roe', 'N/A')} | |
| 近12个月每股现金分红 | {ttm_cash} | 仅现金分红、税前口径 |
| TTM 股息率 | {ttm_yield} | 公式：近12个月每股现金分红 / 当前价格 × 100% |
| TTM 分红事件数 | {ttm_count} | |

> 若上述字段为 N/A 或缺失，请明确写“数据缺失，无法判断”，禁止编造。
"""

        capital_flow_block = (
            fundamental_context.get("capital_flow", {}) if isinstance(fundamental_context, dict) else {}
        )
        capital_flow_data = capital_flow_block.get("data", {}) if isinstance(capital_flow_block, dict) else {}
        stock_flow = capital_flow_data.get("stock_flow", {}) if isinstance(capital_flow_data, dict) else {}
        sector_flow = capital_flow_data.get("sector_rankings", {}) if isinstance(capital_flow_data, dict) else {}
        has_capital_flow = (isinstance(stock_flow, dict) and any(v is not None for v in stock_flow.values())) or (
            isinstance(sector_flow, dict) and (sector_flow.get("top") or sector_flow.get("bottom"))
        )
        if has_capital_flow:
            top_sectors = sector_flow.get("top", []) if isinstance(sector_flow, dict) else []
            bottom_sectors = sector_flow.get("bottom", []) if isinstance(sector_flow, dict) else []
            top_sector_text = (
                "、".join(
                    str(item.get("name", "")).strip()
                    for item in top_sectors[:3]
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                )
                or "N/A"
            )
            bottom_sector_text = (
                "、".join(
                    str(item.get("name", "")).strip()
                    for item in bottom_sectors[:3]
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                )
                or "N/A"
            )
            prompt += f"""
### 主力资金流向（原始交易证据）
| 指标 | 数值 | 口径 |
|------|------|----------|
| 主力净流入 | {stock_flow.get('main_net_inflow', 'N/A')} | 数据源主力口径 |
| 5日净流入 | {stock_flow.get('inflow_5d', 'N/A')} | 5日累计 |
| 10日净流入 | {stock_flow.get('inflow_10d', 'N/A')} | 10日累计 |
| 资金流入靠前板块 | {top_sector_text} | 原始板块数据 |
| 资金流出靠前板块 | {bottom_sector_text} | 原始板块数据 |

> 这些是原始交易证据。请结合公司、板块、价格位置与数据时间形成判断，不得由正负号直接生成操作结论。
"""

        # 添加筹码分布数据
        if "chip" in context:
            chip = context["chip"]
            profit_ratio = chip.get("profit_ratio", 0)
            prompt += f"""
### 筹码分布数据（原始证据）
| 指标 | 数值 | 说明 |
|------|------|----------|
| **获利比例** | **{profit_ratio:.1%}** | 由模型结合市场阶段研判 |
| 平均成本 | {chip.get('avg_cost', 'N/A')} 元 | 数据源估算 |
| 90%筹码集中度 | {chip.get('concentration_90', 0):.2%} | 数据源原值 |
| 70%筹码集中度 | {chip.get('concentration_70', 0):.2%} | |
| 筹码状态 | {chip.get('chip_status', unknown_text)} | |
"""

        # 添加趋势分析结果（仅隐式内建 bull_trend 默认回退保留旧口径）
        if "trend_analysis" in context:
            trend = _sanitize_trend_analysis_for_prompt(
                context["trend_analysis"],
                volume_change_ratio=context.get("volume_change_ratio"),
            )
            consistency_notes = trend.get("prompt_consistency_notes", [])
            if use_legacy_default_prompt:
                prompt += f"""
### 趋势分析预判（基于交易理念）
| 指标 | 数值 | 判定 |
|------|------|------|
| 趋势状态 | {trend.get('trend_status', unknown_text)} | |
| 均线排列 | {trend.get('ma_alignment', unknown_text)} | 数据源结构描述 |
| 趋势强度 | {trend.get('trend_strength', 0)}/100 | |
| **乖离率(MA5)** | **{trend.get('bias_ma5', 0):+.2f}%** | 由模型结合波动与策略研判 |
| 乖离率(MA10) | {trend.get('bias_ma10', 0):+.2f}% | |
| 量能状态 | {trend.get('volume_status', unknown_text)} | {trend.get('volume_trend', '')} |
| 系统信号 | {trend.get('buy_signal', unknown_text)} | |
| 系统评分 | {trend.get('signal_score', 0)}/100 | |

#### 系统分析理由
**买入理由**：
{chr(10).join('- ' + r for r in trend.get('signal_reasons', ['无'])) if trend.get('signal_reasons') else '- 无'}

**风险因素**：
{chr(10).join('- ' + r for r in trend.get('risk_factors', ['无'])) if trend.get('risk_factors') else '- 无'}
"""
                if consistency_notes:
                    prompt += f"""

**一致性约束**：
{chr(10).join('- ' + note for note in consistency_notes)}
"""
            else:
                prompt += f"""
### 技术与结构分析（供激活技能判断参考）
| 指标 | 数值 | 说明 |
|------|------|------|
| 趋势状态 | {trend.get('trend_status', unknown_text)} | |
| 均线排列 | {trend.get('ma_alignment', unknown_text)} | 结合激活技能判断结构强弱 |
| 趋势强度 | {trend.get('trend_strength', 0)}/100 | |
| **价格位置(MA5)** | **{trend.get('bias_ma5', 0):+.2f}%** | 由模型结合波动与策略研判 |
| 价格位置(MA10) | {trend.get('bias_ma10', 0):+.2f}% | |
| 量能状态 | {trend.get('volume_status', unknown_text)} | {trend.get('volume_trend', '')} |
| 系统信号 | {trend.get('buy_signal', unknown_text)} | |
| 系统评分 | {trend.get('signal_score', 0)}/100 | |

#### 系统分析理由
**支持因素**：
{chr(10).join('- ' + r for r in trend.get('signal_reasons', ['无'])) if trend.get('signal_reasons') else '- 无'}

**风险因素**：
{chr(10).join('- ' + r for r in trend.get('risk_factors', ['无'])) if trend.get('risk_factors') else '- 无'}
"""
                if consistency_notes:
                    prompt += f"""

**一致性约束**：
{chr(10).join('- ' + note for note in consistency_notes)}
"""

        # 添加昨日对比数据
        if "yesterday" in context:
            volume_change = context.get("volume_change_ratio", "N/A")
            prompt += f"""
### 量价变化
- 成交量较昨日变化：{volume_change}倍
- 价格较昨日变化：{context.get('price_change_ratio', 'N/A')}%
"""
            parsed_volume_change = _safe_float(volume_change, default=math.nan)
            if math.isfinite(parsed_volume_change) and parsed_volume_change > 10:
                prompt += """
- ⚠️ 量能异常提示：成交量较昨日放大超过10倍，可能受异常数据或一次性冲量影响，必须降权解读，不能机械视为强确认信号
"""

        # 添加新闻搜索结果（重点区域）
        news_window_days: Optional[int] = None
        context_window = context.get("news_window_days")
        try:
            if context_window is not None:
                parsed_window = int(context_window)
                if parsed_window > 0:
                    news_window_days = parsed_window
        except (TypeError, ValueError):
            news_window_days = None

        if news_window_days is None:
            prompt_config = self._get_runtime_config()
            news_window_days = resolve_news_window_days(
                news_max_age_days=getattr(prompt_config, "news_max_age_days", 3),
                news_strategy_profile=getattr(prompt_config, "news_strategy_profile", "short"),
            )
        prompt += """
---

## 📰 舆情情报
"""
        if news_context:
            prompt += f"""
以下是 **{stock_name}({code})** 近{news_window_days}日的新闻搜索结果，请重点提取：
1. 🚨 **风险警报**：减持、处罚、利空
2. 🎯 **利好催化**：业绩、合同、政策
3. 📊 **业绩预期**：年报预告、业绩快报
4. 🕒 **时间规则（强制）**：
   - 输出到 `risk_alerts` / `positive_catalysts` / `latest_news` 的每一条都必须带具体日期（YYYY-MM-DD）
   - 超出近{news_window_days}日窗口的新闻一律忽略
   - 时间未知、无法确定发布日期的新闻一律忽略

```
{news_context}
```
"""
        else:
            prompt += """
未搜索到该股票近期的相关新闻。请主要依据技术面数据进行分析。
"""

        # 注入缺失数据警告
        if context.get("data_missing"):
            prompt += """
⚠️ **数据缺失警告**
由于接口限制，当前无法获取完整的实时行情和技术指标数据。
请 **忽略上述表格中的 N/A 数据**，重点依据 **【📰 舆情情报】** 中的新闻进行基本面和情绪面分析。
在回答技术面问题（如均线、乖离率）时，请直接说明“数据缺失，无法判断”，**严禁编造数据**。
"""

        # 明确的输出要求
        prompt += f"""
---

## ✅ 分析任务

请为 **{stock_name}({code})** 生成【决策仪表盘】，严格按照 JSON 格式输出。
"""
        if context.get("is_index_etf"):
            prompt += """
> ⚠️ **指数/ETF 分析约束**：该标的为指数跟踪型 ETF 或市场指数。
> - 风险分析仅关注：**指数走势、跟踪误差、市场流动性**
> - 严禁将基金公司的诉讼、声誉、高管变动纳入风险警报
> - 业绩预期基于**指数成分股整体表现**，而非基金公司财报
> - `risk_alerts` 中不得出现基金管理人相关的公司经营风险

"""
        prompt += f"""
### ⚠️ 重要：输出正确的股票名称格式
正确的股票名称格式为“股票名称（股票代码）”，例如“贵州茅台（600519）”。
如果上方显示的股票名称为"股票{code}"或不正确，请在分析开头**明确输出该股票的正确中文全称**。
"""
        if use_legacy_default_prompt:
            prompt += f"""

### 重点关注（必须明确回答）：
1. ❓ 当前均线与价格结构如何，证据之间是否一致？
2. ❓ 当前乖离率在本轮波动、策略周期和风险收益背景下意味着什么？
3. ❓ 量能是否配合（缩量回调/放量突破）？
4. ❓ 筹码结构是否健康？
5. ❓ 消息面有无重大利空？（减持、处罚、业绩变脸等）
"""
        else:
            prompt += f"""

### 重点关注（必须明确回答）：
1. ❓ 当前结构是否满足激活技能的关键触发条件？
2. ❓ 当前入场位置与风险回报是否合理？若偏离过大，请明确说明等待条件
3. ❓ 量能、波动与筹码结构是否支持当前结论？
4. ❓ 消息面有无重大利空或与技能结论冲突的信息？
5. ❓ 若结论成立，具体触发条件、止损位、观察点分别是什么？
"""
        prompt += f"""

### 决策仪表盘要求：
- **股票名称**：必须输出正确的中文全称（如"贵州茅台"而非"股票600519"）
- **核心结论**：一句话说清该买/该卖/该等
- **持仓分类建议**：空仓者怎么做 vs 持仓者怎么做
- **具体狙击点位**：买入价、止损价、目标价（精确到分）
- **检查清单**：每项用 ✅/⚠️/❌ 标记
- **消息面时间合规**：`latest_news`、`risk_alerts`、`positive_catalysts` 不得包含超出近{news_window_days}日或时间未知的信息
- **技术面一致性**：严禁把“空头排列”和“多头排列”等互斥结论同时当作有效依据；若基本面/事件面与技术面冲突，必须明确写“事件先行、技术待确认”或“基本面偏多，但技术面尚未确认”
 
请输出完整的 JSON 格式决策仪表盘。"""

        if report_language == "en":
            prompt += """

### Output language requirements (highest priority)
- Keep every JSON key exactly as defined above; do not translate keys.
- `decision_type` must remain `buy`, `hold`, or `sell`.
- All human-readable JSON values must be in English.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, all nested dashboard text, checklist items, and every summary field.
- Use the common English company name when you are confident. If not, keep the listed company name rather than inventing one.
- When data is missing, explain it in English instead of Chinese.
"""
        else:
            prompt += f"""

### 输出语言要求（最高优先级）
- 所有 JSON 键名必须保持不变，不要翻译键名。
- `decision_type` 必须保持为 `buy`、`hold`、`sell`。
- 所有面向用户的人类可读文本值必须使用中文。
- 当数据缺失时，请使用中文直接说明“{no_data_text}，无法判断”。
"""

        return prompt

    def _format_volume(self, volume: Optional[float]) -> str:
        """格式化成交量显示"""
        if volume is None:
            return "N/A"
        if volume >= 1e8:
            return f"{volume / 1e8:.2f} 亿股"
        elif volume >= 1e4:
            return f"{volume / 1e4:.2f} 万股"
        else:
            return f"{volume:.0f} 股"

    def _format_amount(self, amount: Optional[float]) -> str:
        """格式化成交额显示"""
        if amount is None:
            return "N/A"
        if amount >= 1e8:
            return f"{amount / 1e8:.2f} 亿元"
        elif amount >= 1e4:
            return f"{amount / 1e4:.2f} 万元"
        else:
            return f"{amount:.0f} 元"

    def _format_percent(self, value: Optional[float]) -> str:
        """格式化百分比显示"""
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.2f}%"
        except (TypeError, ValueError):
            return "N/A"

    def _format_price(self, value: Optional[float]) -> str:
        """格式化价格显示"""
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return "N/A"

    def _build_market_snapshot(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """构建当日行情快照（展示用）"""
        today = context.get("today", {}) or {}
        realtime = context.get("realtime", {}) or {}
        yesterday = context.get("yesterday", {}) or {}

        prev_close = yesterday.get("close")
        close = today.get("close")
        high = today.get("high")
        low = today.get("low")

        amplitude = None
        change_amount = None
        if prev_close not in (None, 0) and high is not None and low is not None:
            try:
                amplitude = (float(high) - float(low)) / float(prev_close) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                amplitude = None
        if prev_close is not None and close is not None:
            try:
                change_amount = float(close) - float(prev_close)
            except (TypeError, ValueError):
                change_amount = None

        snapshot = {
            "date": context.get("date", "未知"),
            "close": self._format_price(close),
            "open": self._format_price(today.get("open")),
            "high": self._format_price(high),
            "low": self._format_price(low),
            "prev_close": self._format_price(prev_close),
            "pct_chg": self._format_percent(today.get("pct_chg")),
            "change_amount": self._format_price(change_amount),
            "amplitude": self._format_percent(amplitude),
            "volume": self._format_volume(today.get("volume")),
            "amount": self._format_amount(today.get("amount")),
        }

        if realtime:
            snapshot.update(
                {
                    "price": self._format_price(realtime.get("price")),
                    "volume_ratio": realtime.get("volume_ratio", "N/A"),
                    "turnover_rate": self._format_percent(realtime.get("turnover_rate")),
                    "source": getattr(realtime.get("source"), "value", realtime.get("source", "N/A")),
                }
            )

        return snapshot

    def _check_content_integrity(self, result: AnalysisResult) -> Tuple[bool, List[str]]:
        """Delegate to module-level check_content_integrity."""
        return check_content_integrity(result)

    def _build_integrity_complement_prompt(self, missing_fields: List[str], report_language: str = "zh") -> str:
        """Build complement instruction for missing mandatory fields."""
        report_language = normalize_report_language(report_language)
        if report_language == "en":
            lines = [
                "### Completion requirements: fill the missing mandatory fields below and output the full JSON again:"
            ]
            for f in missing_fields:
                if f == "sentiment_score":
                    lines.append("- sentiment_score: integer score from 0 to 100")
                elif f == "operation_advice":
                    lines.append("- operation_advice: localized action advice")
                elif f == "analysis_summary":
                    lines.append("- analysis_summary: concise analysis summary")
                elif f == "dashboard.core_conclusion.one_sentence":
                    lines.append("- dashboard.core_conclusion.one_sentence: one-line decision")
                elif f == "dashboard.intelligence.risk_alerts":
                    lines.append("- dashboard.intelligence.risk_alerts: risk alert list (can be empty)")
                elif f == "dashboard.battle_plan.sniper_points.stop_loss":
                    lines.append("- dashboard.battle_plan.sniper_points.stop_loss: stop-loss level")
            return "\n".join(lines)

        lines = ["### 补全要求：请在上方分析基础上补充以下必填内容，并输出完整 JSON："]
        for f in missing_fields:
            if f == "sentiment_score":
                lines.append("- sentiment_score: 0-100 综合评分")
            elif f == "operation_advice":
                lines.append("- operation_advice: 买入/加仓/持有/减仓/卖出/观望")
            elif f == "analysis_summary":
                lines.append("- analysis_summary: 综合分析摘要")
            elif f == "dashboard.core_conclusion.one_sentence":
                lines.append("- dashboard.core_conclusion.one_sentence: 一句话决策")
            elif f == "dashboard.intelligence.risk_alerts":
                lines.append("- dashboard.intelligence.risk_alerts: 风险警报列表（可为空数组）")
            elif f == "dashboard.battle_plan.sniper_points.stop_loss":
                lines.append("- dashboard.battle_plan.sniper_points.stop_loss: 止损价")
        return "\n".join(lines)

    def _build_integrity_retry_prompt(
        self,
        base_prompt: str,
        previous_response: str,
        missing_fields: List[str],
        report_language: str = "zh",
    ) -> str:
        """Build retry prompt using the previous response as the complement baseline."""
        complement = self._build_integrity_complement_prompt(missing_fields, report_language=report_language)
        previous_output = previous_response.strip()
        if normalize_report_language(report_language) == "en":
            prefix = "### The previous output is below. Complete the missing fields based on that output and return the full JSON again. Do not omit existing fields:"
        else:
            prefix = "### 上一次输出如下，请在该输出基础上补齐缺失字段，并重新输出完整 JSON。不要省略已有字段："
        return "\n\n".join(
            [
                base_prompt,
                prefix,
                previous_output,
                complement,
            ]
        )

    def _apply_placeholder_fill(self, result: AnalysisResult, missing_fields: List[str]) -> None:
        """Delegate to module-level apply_placeholder_fill."""
        apply_placeholder_fill(result, missing_fields)

    def _parse_response(self, response_text: str, code: str, name: str) -> AnalysisResult:
        """
        解析 Gemini 响应（决策仪表盘版）

        尝试从响应中提取 JSON 格式的分析结果，包含 dashboard 字段
        如果解析失败，尝试智能提取或返回默认结果
        """
        try:
            report_language = normalize_report_language(getattr(self._get_runtime_config(), "report_language", "zh"))
            # 清理响应文本：移除 markdown 代码块标记
            cleaned_text = response_text
            if "```json" in cleaned_text:
                cleaned_text = cleaned_text.replace("```json", "").replace("```", "")
            elif "```" in cleaned_text:
                cleaned_text = cleaned_text.replace("```", "")

            # 尝试找到 JSON 内容
            json_start = cleaned_text.find("{")
            json_end = cleaned_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = cleaned_text[json_start:json_end]

                # 尝试修复常见的 JSON 问题
                json_str = self._fix_json_string(json_str)

                data = json.loads(json_str)

                # Schema validation (lenient: on failure, continue with raw dict)
                try:
                    AnalysisReportSchema.model_validate(data)
                except Exception as e:
                    logger.warning(
                        "LLM report schema validation failed, continuing with raw dict: %s",
                        str(e)[:100],
                    )

                # 提取 dashboard 数据
                dashboard = data.get("dashboard", None)

                # 优先使用 AI 返回的股票名称（如果原名称无效或包含代码）
                ai_stock_name = data.get("stock_name")
                if ai_stock_name and (name.startswith("股票") or name == code or "Unknown" in name):
                    name = ai_stock_name

                # 解析所有字段，使用默认值防止缺失
                # 解析 decision_type，如果没有则根据 operation_advice 推断
                decision_type = data.get("decision_type", "")
                if not decision_type:
                    op = data.get("operation_advice", "Hold" if report_language == "en" else "持有")
                    decision_type = infer_decision_type_from_advice(op, default="hold")

                return AnalysisResult(
                    code=code,
                    name=name,
                    # 核心指标
                    sentiment_score=int(data.get("sentiment_score", 50)),
                    trend_prediction=data.get("trend_prediction", "Sideways" if report_language == "en" else "震荡"),
                    operation_advice=data.get("operation_advice", "Hold" if report_language == "en" else "持有"),
                    decision_type=decision_type,
                    confidence_level=localize_confidence_level(
                        data.get("confidence_level", "Medium" if report_language == "en" else "中"),
                        report_language,
                    ),
                    report_language=report_language,
                    # 决策仪表盘
                    dashboard=dashboard,
                    # 走势分析
                    trend_analysis=data.get("trend_analysis", ""),
                    short_term_outlook=data.get("short_term_outlook", ""),
                    medium_term_outlook=data.get("medium_term_outlook", ""),
                    # 技术面
                    technical_analysis=data.get("technical_analysis", ""),
                    ma_analysis=data.get("ma_analysis", ""),
                    volume_analysis=data.get("volume_analysis", ""),
                    pattern_analysis=data.get("pattern_analysis", ""),
                    # 基本面
                    fundamental_analysis=data.get("fundamental_analysis", ""),
                    sector_position=data.get("sector_position", ""),
                    company_highlights=data.get("company_highlights", ""),
                    # 情绪面/消息面
                    news_summary=data.get("news_summary", ""),
                    market_sentiment=data.get("market_sentiment", ""),
                    hot_topics=data.get("hot_topics", ""),
                    # 综合
                    analysis_summary=data.get(
                        "analysis_summary", "Analysis completed" if report_language == "en" else "分析完成"
                    ),
                    key_points=data.get("key_points", ""),
                    risk_warning=data.get("risk_warning", ""),
                    buy_reason=data.get("buy_reason", ""),
                    # 元数据
                    search_performed=data.get("search_performed", False),
                    data_sources=data.get(
                        "data_sources", "Technical data" if report_language == "en" else "技术面数据"
                    ),
                    success=True,
                )
            else:
                # 没有找到 JSON，标记为失败
                logger.warning(f"无法从响应中提取 JSON，标记为解析失败")
                return self._parse_text_response(response_text, code, name)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}，标记为解析失败")
            return self._parse_text_response(response_text, code, name)

    def _fix_json_string(self, json_str: str) -> str:
        """修复常见的 JSON 格式问题"""
        import re

        # 移除注释
        json_str = re.sub(r"//.*?\n", "\n", json_str)
        json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)

        # 修复尾随逗号
        json_str = re.sub(r",\s*}", "}", json_str)
        json_str = re.sub(r",\s*]", "]", json_str)

        # 确保布尔值是小写
        json_str = json_str.replace("True", "true").replace("False", "false")

        # fix by json-repair
        json_str = repair_json(json_str)

        return json_str

    def _validate_json_response(self, text: str) -> None:
        """Validate that *text* contains a parseable JSON object.

        Used as the ``response_validator`` argument to :meth:`_call_litellm` so
        that a JSON-less or unparseable reply from the primary model is treated
        as a model failure and triggers fallback to the next configured model.

        Raises:
            ValueError: if no JSON object is found in *text*.
            json.JSONDecodeError: if the extracted JSON cannot be parsed (after
                :meth:`_fix_json_string` attempts repair).
        """
        cleaned = text
        if "```json" in cleaned:
            cleaned = cleaned.replace("```json", "").replace("```", "")
        elif "```" in cleaned:
            cleaned = cleaned.replace("```", "")

        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}") + 1

        if json_start < 0 or json_end <= json_start:
            raise ValueError("No JSON object found in LLM response")

        json_str = cleaned[json_start:json_end]
        json_str = self._fix_json_string(json_str)
        json.loads(json_str)

    def _parse_text_response(self, response_text: str, code: str, name: str) -> AnalysisResult:
        """Preserve an unstructured fallback without guessing its semantics."""
        report_language = normalize_report_language(getattr(self._get_runtime_config(), "report_language", "zh"))
        sentiment_score = 50
        trend = "Sideways" if report_language == "en" else "震荡"
        advice = "Hold" if report_language == "en" else "持有"
        decision_type = "hold"

        # 截取前500字符作为摘要
        summary = (
            response_text[:500]
            if response_text
            else ("No analysis result" if report_language == "en" else "无分析结果")
        )

        return AnalysisResult(
            code=code,
            name=name,
            sentiment_score=sentiment_score,
            trend_prediction=trend,
            operation_advice=advice,
            decision_type=decision_type,
            confidence_level="Low" if report_language == "en" else "低",
            analysis_summary=summary,
            key_points=(
                "JSON parsing failed; treat this as best-effort output."
                if report_language == "en"
                else "JSON解析失败，仅供参考"
            ),
            risk_warning=(
                "The result may be inaccurate. Cross-check with other information."
                if report_language == "en"
                else "分析结果可能不准确，建议结合其他信息判断"
            ),
            raw_response=response_text,
            success=False,
            error_message="LLM response is not valid JSON; analysis result will not be persisted",
            report_language=report_language,
        )

    def batch_analyze(self, contexts: List[Dict[str, Any]], delay_between: float = 2.0) -> List[AnalysisResult]:
        """
        批量分析多只股票

        注意：为避免 API 速率限制，每次分析之间会有延迟

        Args:
            contexts: 上下文数据列表
            delay_between: 每次分析之间的延迟（秒）

        Returns:
            AnalysisResult 列表
        """
        results = []

        for i, context in enumerate(contexts):
            if i > 0:
                logger.debug(f"等待 {delay_between} 秒后继续...")
                time.sleep(delay_between)

            result = self.analyze(context)
            results.append(result)

        return results


# 便捷函数
def get_analyzer() -> GeminiAnalyzer:
    """获取 LLM 分析器实例"""
    return GeminiAnalyzer()
