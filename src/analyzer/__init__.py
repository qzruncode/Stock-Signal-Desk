# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - AI分析层
===================================

兼容再导出层：原 `src/analyzer.py` 中的所有公开 / 私有符号在此聚合，
保证旧的 `from src.analyzer import X` 调用与测试 `patch("src.analyzer.<name>")`
完全等价于拆分前的行为。

职责模块拆分概览：
- `_common`               共享低层工具（_safe_float、_is_value_placeholder 等）
- `result`                `AnalysisResult` 数据类
- `integrity`             报告完整性检查与占位符填充
- `trend_prompt`          Prompt 趋势上下文清洗
- `chip_structure`        筹码结构回填
- `price_position`        价格位置回填
- `structural_risk`       结构性风险识别
- `decision_stability`    决策稳态化（含资金流偏向）
- `stock_name`            股票名解析
- `gemini_analyzer`       `GeminiAnalyzer` 主类 + LiteLLM 调用 / 解析 / 分析流程
"""

import logging

# 必须先暴露 get_config / Config 等 src.config 符号到本模块，
# 以保留旧测试 `patch("src.analyzer.get_config", ...)` 的行为。
from src.config import (
    Config,
    extra_litellm_params,
    get_api_keys_for_model,
    get_config,
    get_configured_llm_models,
    resolve_news_window_days,
)
from src.data.stock_mapping import STOCK_NAME_MAP
from src.llm.errors import call_litellm_with_param_recovery
from src.llm.generation_params import apply_litellm_generation_params
from src.market_context import get_market_guidelines, get_market_role
from src.report_language import (
    get_no_data_text,
    get_placeholder_text,
    get_signal_level,
    get_unknown_text,
    infer_decision_type_from_advice,
    localize_chip_health,
    localize_confidence_level,
    normalize_report_language,
)
from src.schemas.report_schema import AnalysisReportSchema
from src.storage import persist_llm_usage

from src.analyzer._common import (
    _CAPITAL_FLOW_UNAVAILABLE_STATUS,
    _RISK_WARNING_PLACEHOLDER_TEXTS,
    _is_meaningful_text,
    _is_value_placeholder,
    _normalize_risk_warning_values,
    _safe_float,
)
from src.analyzer.result import AnalysisResult
from src.analyzer.integrity import apply_placeholder_fill, check_content_integrity
from src.analyzer.trend_prompt import (
    _BEARISH_TREND_HINTS,
    _BULLISH_TREND_HINTS,
    _NEGATION_BREAK_CHARS,
    _NEGATION_LOOKBACK_CHARS,
    _NEGATION_MAX_GAP_CHARS,
    _NEGATION_SCOPE_BREAK_TOKENS,
    _NEGATION_TOKENS,
    _SINGLE_CHAR_NEGATION_GAP_PREFIXES,
    _WEAK_BEARISH_TREND_HINTS,
    _WEAK_BULLISH_TREND_HINTS,
    _contains_trend_hint,
    _filter_conflicting_trend_items,
    _infer_trend_direction,
    _normalize_prompt_reason_items,
    _sanitize_trend_analysis_for_prompt,
)
from src.analyzer.chip_structure import (
    _CHIP_KEYS,
    _build_chip_structure_from_data,
    _derive_chip_health,
    fill_chip_structure_if_needed,
)
from src.analyzer.price_position import _PRICE_POS_KEYS, fill_price_position_if_needed
from src.analyzer.structural_risk import (
    _STRUCTURAL_RISK_PHRASE_HINTS,
    _has_structural_risk_alert,
    _is_significant_structural_risk,
)
from src.analyzer.decision_stability import (
    _apply_hold_watch_dashboard,
    _as_dict_for_decision_guard,
    _bound_hold_watch_sentiment_score,
    _capital_flow_bias,
    _capital_flow_bias_with_status,
    _capital_flow_status_for_stability,
    _coerce_numeric_value,
    _downgrade_buy_without_capital_flow,
    _downgrade_to_structural_hold,
    _first_list_value,
    _first_numeric_value,
    _set_decision_stability_unavailable,
    _set_structural_hold_wording,
    _sync_stability_dashboard_fields,
    stabilize_decision_with_structure,
)
from src.analyzer.stock_name import get_stock_name_multi_source
from src.analyzer.gemini_analyzer import (
    GeminiAnalyzer,
    _AllModelsFailedError,
    _LiteLLMStreamError,
    get_analyzer,
)

logger = logging.getLogger(__name__)


__all__ = [
    # 配置 / 外部符号（保留旧导入路径）
    "Config",
    "STOCK_NAME_MAP",
    "AnalysisReportSchema",
    "apply_litellm_generation_params",
    "call_litellm_with_param_recovery",
    "extra_litellm_params",
    "get_api_keys_for_model",
    "get_config",
    "get_configured_llm_models",
    "get_market_guidelines",
    "get_market_role",
    "get_no_data_text",
    "get_placeholder_text",
    "get_signal_level",
    "get_unknown_text",
    "infer_decision_type_from_advice",
    "localize_chip_health",
    "localize_confidence_level",
    "normalize_report_language",
    "persist_llm_usage",
    "resolve_news_window_days",
    # AnalysisResult / 报告完整性
    "AnalysisResult",
    "apply_placeholder_fill",
    "check_content_integrity",
    # Prompt 趋势清洗
    "_BEARISH_TREND_HINTS",
    "_BULLISH_TREND_HINTS",
    "_NEGATION_BREAK_CHARS",
    "_NEGATION_LOOKBACK_CHARS",
    "_NEGATION_MAX_GAP_CHARS",
    "_NEGATION_SCOPE_BREAK_TOKENS",
    "_NEGATION_TOKENS",
    "_SINGLE_CHAR_NEGATION_GAP_PREFIXES",
    "_WEAK_BEARISH_TREND_HINTS",
    "_WEAK_BULLISH_TREND_HINTS",
    "_contains_trend_hint",
    "_filter_conflicting_trend_items",
    "_infer_trend_direction",
    "_normalize_prompt_reason_items",
    "_sanitize_trend_analysis_for_prompt",
    # 筹码结构 / 价格位置
    "_CHIP_KEYS",
    "_PRICE_POS_KEYS",
    "_build_chip_structure_from_data",
    "_derive_chip_health",
    "fill_chip_structure_if_needed",
    "fill_price_position_if_needed",
    # 结构性风险
    "_STRUCTURAL_RISK_PHRASE_HINTS",
    "_has_structural_risk_alert",
    "_is_significant_structural_risk",
    # 决策稳态化
    "_CAPITAL_FLOW_UNAVAILABLE_STATUS",
    "_RISK_WARNING_PLACEHOLDER_TEXTS",
    "_apply_hold_watch_dashboard",
    "_as_dict_for_decision_guard",
    "_bound_hold_watch_sentiment_score",
    "_capital_flow_bias",
    "_capital_flow_bias_with_status",
    "_capital_flow_status_for_stability",
    "_coerce_numeric_value",
    "_downgrade_buy_without_capital_flow",
    "_downgrade_to_structural_hold",
    "_first_list_value",
    "_first_numeric_value",
    "_is_meaningful_text",
    "_is_value_placeholder",
    "_normalize_risk_warning_values",
    "_safe_float",
    "_set_decision_stability_unavailable",
    "_set_structural_hold_wording",
    "_sync_stability_dashboard_fields",
    "stabilize_decision_with_structure",
    # 股票名 / GeminiAnalyzer
    "get_stock_name_multi_source",
    "GeminiAnalyzer",
    "_AllModelsFailedError",
    "_LiteLLMStreamError",
    "get_analyzer",
]
