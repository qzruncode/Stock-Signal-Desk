# -*- coding: utf-8 -*-
"""Indicator-screening catalog and execution endpoints for the settings UI."""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.services.stock_screening.atr_volatility_screener import run_atr_volatility_screen
from src.services.stock_screening.indicator_plan import (
    IndicatorScreenPlan,
    indicator_screen_plan_schema,
)
from src.services.stock_screening.screen_spec import (
    QuantitativeScreenSpec,
    quantitative_screen_spec_schema,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _default_atr_parameters() -> dict[str, Any]:
    return {
        "atr_period": 14,
        "atr_average": "sma",
        "baseline_period": 60,
        "baseline_average": "sma",
        "threshold_operator": "divide",
        "threshold_value": 1.27,
        "volatility_threshold_pct": 2.8,
        "daily_comparison": "gt",
        "lookback_days": 250,
        "min_qualified_days": 175,
        "min_qualified_ratio_pct": 70,
    }


def _default_atr_screen_spec() -> dict[str, Any]:
    """Return the first UI preset without duplicating the executable contract."""
    return QuantitativeScreenSpec.model_validate(
        {
            "version": "1.0",
            "universe": {
                "status": "active",
                "markets": ["sh", "sz", "bj"],
                "include_st": False,
                "min_listing_trading_days": 250,
                "price_adjustment": "qfq",
            },
            "technical_rule": {
                "strategy": "atr_relative_frequency",
                **_default_atr_parameters(),
            },
            "financial_filters": [
                {"field": "revenue_ttm", "operator": "gt", "value": 500_000_000},
                {"field": "deducted_net_profit_ttm", "operator": "gt", "value": 0},
                {"field": "debt_ratio", "operator": "lt", "value": 70},
            ],
            "sort": {"field": "qualified_ratio_pct", "order": "desc"},
            "output_fields": [
                "current_atr_pct",
                "long_term_mean_pct",
                "dynamic_warning_pct",
                "qualified_days",
                "qualified_ratio_pct",
                "latest_trade_date",
            ],
            "preview_limit": 20,
        }
    ).model_dump(mode="json", exclude_none=True)


_ATR_PARAMETER_SCHEMA: tuple[dict[str, Any], ...] = (
    {
        "key": "volatility_threshold_pct",
        "label": "ATR 相对波动率阈值 (%)",
        "control": "number",
        "min": 0.1,
        "max": 100,
        "step": 0.1,
        "default_value": 2.8,
    },
)

_FINANCIAL_OPERATOR_OPTIONS = [
    {"value": "gt", "label": ">"},
    {"value": "gte", "label": "≥"},
    {"value": "lt", "label": "<"},
    {"value": "lte", "label": "≤"},
    {"value": "eq", "label": "="},
]


def _financial_parameter_schema(*, value_label: str, default_operator: str, default_value: float, step: float = 1) -> list[dict[str, Any]]:
    return [
        {
            "key": "operator",
            "label": "比较",
            "control": "select",
            "options": list(_FINANCIAL_OPERATOR_OPTIONS),
            "default_value": default_operator,
        },
        {
            "key": "value",
            "label": value_label,
            "control": "number",
            "step": step,
            "default_value": default_value,
        },
    ]


_FINANCIAL_INDICATOR_CONFIGS: tuple[dict[str, Any], ...] = (
    {
        "id": "parent_net_profit",
        "label": "归母净利润",
        "description": "按滚动十二个月归母净利润筛选股票。",
        "field": "parent_net_profit_ttm",
        "parameter_schema": _financial_parameter_schema(
            value_label="阈值（元）",
            default_operator="gt",
            default_value=0,
        ),
    },
    {
        "id": "debt_ratio",
        "label": "负债率",
        "description": "按最新财报资产负债率筛选股票。",
        "field": "debt_ratio",
        "parameter_schema": _financial_parameter_schema(
            value_label="阈值（%）",
            default_operator="lt",
            default_value=70,
            step=0.1,
        ),
    },
    {
        "id": "annual_revenue",
        "label": "年营业收入",
        "description": "按滚动十二个月营业收入筛选股票。",
        "field": "revenue_ttm",
        "parameter_schema": _financial_parameter_schema(
            value_label="阈值（元）",
            default_operator="gt",
            default_value=500_000_000,
        ),
    },
)


def _run_atr_relative_volatility(
    screen_spec: dict[str, Any],
    *,
    include_all_items: bool = False,
) -> dict[str, Any]:
    run_options: dict[str, Any] = {}
    if include_all_items:
        run_options["include_all_items"] = True
    result = run_atr_volatility_screen(
        screen_spec=screen_spec,
        refresh_if_stale=True,
        include_matched_codes=True,
        **run_options,
    )
    result.setdefault("matched_codes", [])
    return result


def _atr_condition_to_screen_spec(plan: IndicatorScreenPlan, condition: Any) -> dict[str, Any]:
    """Adapt one catalog condition to the existing audited ATR executor."""
    if condition.indicator != "atr_relative_volatility":
        raise ValueError(f"未注册指标适配器: {condition.indicator}")
    # Keep the audited ATR preset closed.  Only the historical absolute
    # volatility threshold is surfaced by the settings page; all other ATR
    # calculation and qualification parameters stay fixed in the preset.
    screen_spec = _default_atr_screen_spec()
    threshold = condition.parameters.get("volatility_threshold_pct")
    if threshold is not None:
        screen_spec["technical_rule"]["volatility_threshold_pct"] = threshold
    return QuantitativeScreenSpec.model_validate(screen_spec).model_dump(mode="json", exclude_none=True)


def _financial_condition_to_screen_spec(plan: IndicatorScreenPlan, condition: Any) -> dict[str, Any]:
    """Adapt a financial catalog condition to the shared ATR screen executor."""
    config = next(
        (item for item in _FINANCIAL_INDICATOR_CONFIGS if item["id"] == condition.indicator),
        None,
    )
    if config is None:
        raise ValueError(f"未注册指标适配器: {condition.indicator}")

    parameters = condition.parameters
    defaults = {
        definition["key"]: definition["default_value"]
        for definition in config["parameter_schema"]
    }
    operator = parameters.get("operator", defaults["operator"])
    value = parameters.get("value")
    if "value" not in parameters:
        value = defaults["value"]

    screen_spec = _default_atr_screen_spec()
    # The ATR preset has three historical financial gates.  When the user
    # explicitly adds one of these indicators, replace that gate so the new
    # condition is the source of truth for the selected field.
    screen_spec["financial_filters"] = [
        item for item in screen_spec["financial_filters"] if item["field"] != config["field"]
    ]
    screen_spec["financial_filters"].append(
        {"field": config["field"], "operator": operator, "value": value}
    )
    return QuantitativeScreenSpec.model_validate(screen_spec).model_dump(mode="json", exclude_none=True)


_CONDITION_ADAPTERS: dict[str, Callable[[IndicatorScreenPlan, Any], dict[str, Any]]] = {
    "atr_relative_volatility": _atr_condition_to_screen_spec,
    **{
        config["id"]: _financial_condition_to_screen_spec
        for config in _FINANCIAL_INDICATOR_CONFIGS
    },
}
_CONDITION_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "atr_relative_volatility": _run_atr_relative_volatility,
    **{
        config["id"]: _run_atr_relative_volatility
        for config in _FINANCIAL_INDICATOR_CONFIGS
    },
}


def _resolve_scope_codes(plan: IndicatorScreenPlan) -> list[str] | None:
    """Resolve the selected group at execution time, keeping the group authoritative."""
    if plan.scope.type == "all":
        return None

    groups = DatabaseManager.get_instance().list_watchlist_groups()
    group = next((item for item in groups if str(item.get("id")) == plan.scope.group_id), None)
    if group is None:
        raise ValueError("筛选范围对应的分组不存在，请刷新分组后重试。")
    return [str(code) for code in group.get("codes", [])]


def _run_indicator_plan(
    plan: IndicatorScreenPlan,
    *,
    include_all_items: bool = False,
) -> dict[str, Any]:
    """Run the generic plan through registered condition adapters.

    Each adapter owns its indicator-specific execution.  The composition layer
    only merges the resulting code sets, so adding a new indicator does not
    require changing the settings page or the boolean-combination protocol.
    """
    condition_results: list[tuple[Any, dict[str, Any]]] = []
    scope_codes = _resolve_scope_codes(plan)
    for condition in plan.conditions:
        adapter = _CONDITION_ADAPTERS.get(condition.indicator)
        runner = _CONDITION_RUNNERS.get(condition.indicator)
        if adapter is None or runner is None:
            raise ValueError(f"未注册指标适配器: {condition.indicator}")
        screen_spec = adapter(plan, condition)
        if scope_codes is not None:
            screen_spec["universe"]["codes"] = scope_codes
            screen_spec = QuantitativeScreenSpec.model_validate(screen_spec).model_dump(
                mode="json",
                exclude_none=True,
            )
        result = runner(screen_spec, include_all_items=include_all_items)
        if result.get("success") is not True:
            result["errors"] = [
                f"条件 {condition.id}（{condition.indicator}）执行失败: {message}"
                for message in result.get("errors", [])
            ] or [f"条件 {condition.id}（{condition.indicator}）执行失败。"]
            result["matched_codes"] = []
            result["total"] = 0
            return result
        condition_results.append((condition, result))

    if len(condition_results) == 1:
        return condition_results[0][1]

    code_sets = [set(str(code) for code in result.get("matched_codes", [])) for _, result in condition_results]
    matched_codes = (
        set.intersection(*code_sets)
        if plan.combination == "all"
        else set.union(*code_sets)
    )
    merged_items: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for _, result in condition_results:
        for item in result.get("items", []):
            code = str(item.get("code") or "")
            if code in matched_codes and code not in seen_codes:
                merged_items.append(item)
                seen_codes.add(code)
    base_result = dict(condition_results[0][1])
    base_result["items"] = merged_items if include_all_items else merged_items[: plan.preview_limit]
    base_result["matched_codes"] = sorted(matched_codes)
    base_result["total"] = len(matched_codes)
    base_result["applied_rules"] = [
        f"组合关系={'全部满足（AND）' if plan.combination == 'all' else '任一满足（OR）'}",
        *[
            f"条件 {condition.id}：{rule}"
            for condition, result in condition_results
            for rule in result.get("applied_rules", [])
        ],
    ]
    base_result["warnings"] = [
        *base_result.get("warnings", []),
        "组合条件按各指标独立计算后合并命中股票；预览字段沿用首个条件。",
    ]
    return base_result


_INDICATORS: tuple[dict[str, Any], ...] = (
    {
        "id": "atr_relative_volatility",
        "label": "ATR 相对波动率",
        "description": "按 ATR/收盘价计算相对波动率，并统计最近窗口内超过阈值的股票。",
        "available": True,
        "parameter_schema": list(_ATR_PARAMETER_SCHEMA),
        "combination_ready": True,
        "spec_schema": quantitative_screen_spec_schema(),
        "default_spec": _default_atr_screen_spec(),
    },
) + tuple(
    {
        "id": config["id"],
        "label": config["label"],
        "description": config["description"],
        "available": True,
        "parameter_schema": list(config["parameter_schema"]),
        "combination_ready": True,
        "spec_schema": quantitative_screen_spec_schema(),
        "default_spec": _default_atr_screen_spec(),
    }
    for config in _FINANCIAL_INDICATOR_CONFIGS
)
_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "atr_relative_volatility": _run_atr_relative_volatility,
}


class IndicatorScreenRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator: str | None = Field(default=None, min_length=1, max_length=80)
    screen_spec: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    include_all_items: bool = False

    @model_validator(mode="after")
    def _validate_payload_shape(self) -> "IndicatorScreenRunRequest":
        has_legacy = self.indicator is not None or self.screen_spec is not None
        if self.plan is None and not (self.indicator is not None and self.screen_spec is not None):
            raise ValueError("必须提供完整的 plan，或同时提供 indicator 和 screen_spec")
        if self.plan is not None and has_legacy:
            raise ValueError("plan 不能与旧版 indicator/screen_spec 同时提供")
        return self


@router.get("/indicators", summary="列出可用指标选股指标")
def list_indicator_screening_indicators() -> dict[str, Any]:
    """Return the extensible indicator catalog used by the settings page."""
    return {
        "plan_schema": indicator_screen_plan_schema(),
        "indicators": [dict(item) for item in _INDICATORS],
    }


@router.post("/run", summary="执行指标选股")
def run_indicator_screening(body: IndicatorScreenRunRequest) -> dict[str, Any]:
    """Execute one catalogued indicator and return preview plus all match codes."""
    if body.plan is not None:
        try:
            plan = IndicatorScreenPlan.model_validate(body.plan)
            result = _run_indicator_plan(plan, include_all_items=body.include_all_items)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_indicator_plan", "message": str(exc)},
            ) from exc
        except Exception as exc:
            logger.error("Indicator plan screening failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": "indicator_screening_failed", "message": "指标选股执行失败，请查看服务日志。"},
            ) from exc
        return {"indicator": plan.conditions[0].indicator, "plan": plan.model_dump(mode="json"), **result}

    assert body.indicator is not None and body.screen_spec is not None
    runner = _RUNNERS.get(body.indicator)
    if runner is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "unsupported_indicator", "message": f"不支持的指标选股指标: {body.indicator}"},
        )
    try:
        result = runner(body.screen_spec, include_all_items=body.include_all_items)
    except Exception as exc:
        logger.error("Indicator screening failed for %s: %s", body.indicator, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "indicator_screening_failed", "message": "指标选股执行失败，请查看服务日志。"},
        ) from exc
    return {"indicator": body.indicator, **result}


__all__ = ["router"]
