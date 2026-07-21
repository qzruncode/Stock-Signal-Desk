"""Deterministic final gate: entry location and risk/reward."""

from __future__ import annotations

from typing import Any

from src.services.buy_criteria.base import (
    BaseCriterionEvaluator,
    CriterionEvidence,
    CriterionResult,
)
from src.services.buy_criteria.data_service import DataService


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


class EntryRiskRewardEvaluator(BaseCriterionEvaluator):
    """Use current quote and daily indicators; no model may invent prices."""

    criterion_id = "entry_risk_reward"
    criterion_name = "买入位置与风险收益比"
    index = 8

    def collect_data(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionEvidence:
        del stock_info
        ds = DataService()
        technical = (
            pre_fetched_data.get("technical")
            if pre_fetched_data and isinstance(pre_fetched_data.get("technical"), dict)
            else ds.get_technical_indicators(symbol, count=120)
        )
        quote = (
            pre_fetched_data.get("quote")
            if pre_fetched_data and isinstance(pre_fetched_data.get("quote"), dict)
            else ds.get_realtime_quote(symbol)
        )
        indicators = technical.get("indicators") or {}
        summary = (
            f"现价={quote.get('price') or indicators.get('close')}；"
            f"MA20={indicators.get('ma20')}；MA60={indicators.get('ma60')}；"
            f"布林下轨={indicators.get('boll_lower')}；20日低点={indicators.get('low_20d')}；"
            f"布林上轨={indicators.get('boll_upper')}；20/60日高点="
            f"{indicators.get('high_20d')}/{indicators.get('high_60d')}；"
            f"ATR14={indicators.get('atr14')}（{indicators.get('atr14_pct')}%）；"
            f"RSI14={indicators.get('rsi14')}"
        )
        return CriterionEvidence(
            raw_data={"technical": technical, "quote": quote},
            data_summary=summary,
        )

    def get_rubric(self) -> str:
        return (
            "本项由程序根据当前行情和前复权日线指标确定性计算，不调用模型。"
            "现价必须处于支撑位上方半个ATR以内，目标阻力位到止损位的风险收益比不得低于2，"
            "同时RSI不得超过75且ATR占现价不得超过6%；任一数据缺失或条件不满足均判为不通过。"
        )

    def evaluate(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionResult:
        evidence = self.collect_data(symbol, stock_info, pre_fetched_data)
        technical = evidence.raw_data.get("technical") or {}
        quote = evidence.raw_data.get("quote") or {}
        indicators = technical.get("indicators") or {}

        current = _number(quote.get("price")) or _number(indicators.get("close"))
        atr = _number(indicators.get("atr14"))
        atr_pct = _number(indicators.get("atr14_pct"))
        rsi = _number(indicators.get("rsi14"))
        freshness = quote.get("is_stale") if quote else technical.get("is_stale")
        data_time = quote.get("data_time") or technical.get("data_time")
        details: dict[str, Any] = {
            "calculation_basis": "前复权日线；支撑/阻力和ATR均来自工具确定性计算",
            "data_time": data_time,
            "is_stale": freshness,
        }

        if (
            current is None
            or atr is None
            or atr_pct is None
            or rsi is None
            or not data_time
            or freshness is not False
        ):
            details.update({
                "recommended_initial_position_pct": 0,
                "recommended_max_position_pct": 0,
                "invalidation_conditions": ["行情或技术指标缺失，禁止据此建立仓位"],
            })
            return CriterionResult(
                criterion_id=self.criterion_id,
                criterion_name=self.criterion_name,
                index=self.index,
                passed=False,
                verdict=(
                    "现价、ATR、ATR占比、RSI或数据时间不完整，无法验证买入位置和风险收益比"
                    if freshness is False
                    else "当前行情新鲜度未通过校验，不能用过期或时间不明的数据判断现在能否买入"
                ),
                evidence=evidence,
                details=details,
            )

        support_points = {
            label: value
            for label, raw in (
                ("MA20", indicators.get("ma20")),
                ("MA60", indicators.get("ma60")),
                ("布林下轨", indicators.get("boll_lower")),
                ("20日低点", indicators.get("low_20d")),
                ("60日低点", indicators.get("low_60d")),
            )
            if (value := _number(raw)) is not None and value <= current
        }
        resistance_points = {
            label: value
            for label, raw in (
                ("20日高点", indicators.get("high_20d")),
                ("60日高点", indicators.get("high_60d")),
                ("布林上轨", indicators.get("boll_upper")),
            )
            if (value := _number(raw)) is not None and value > current
        }
        if not support_points or not resistance_points:
            details.update({
                "current_price": _rounded(current),
                "recommended_initial_position_pct": 0,
                "recommended_max_position_pct": 0,
                "invalidation_conditions": ["没有可验证的下方支撑或上方阻力，风险收益比无法闭环"],
            })
            return CriterionResult(
                criterion_id=self.criterion_id,
                criterion_name=self.criterion_name,
                index=self.index,
                passed=False,
                verdict="缺少可验证的支撑位或目标阻力位，不能给出可执行的风险收益比",
                evidence=evidence,
                details=details,
            )

        support_label, support = max(support_points.items(), key=lambda item: item[1])
        resistance_label, target = min(resistance_points.items(), key=lambda item: item[1])
        entry_low = support
        entry_high = support + atr * 0.5
        stop_loss = support - atr
        entry_reference = max(current, entry_high)
        risk = entry_reference - stop_loss
        reward = target - entry_reference
        ratio = reward / risk if risk > 0 else None
        near_entry = current <= entry_high
        rr_ok = ratio is not None and ratio >= 2.0
        rsi_ok = rsi <= 75.0
        volatility_ok = atr_pct <= 6.0
        passed = bool(near_entry and rr_ok and rsi_ok and volatility_ok)

        if passed and ratio is not None and ratio >= 3.0 and atr_pct <= 3.5:
            initial_position, max_position = 10, 20
        elif passed and atr_pct <= 5.0:
            initial_position, max_position = 5, 10
        elif passed:
            initial_position, max_position = 3, 5
        else:
            initial_position, max_position = 0, 0

        failure_reasons = []
        if not near_entry:
            failure_reasons.append("现价高于买入区间上沿")
        if not rr_ok:
            failure_reasons.append("潜在收益/止损风险低于2")
        if not rsi_ok:
            failure_reasons.append("RSI超过75，短线过热")
        if not volatility_ok:
            failure_reasons.append("ATR占现价超过6%，波动过大")

        details.update({
            "current_price": _rounded(current),
            "entry_zone_low": _rounded(entry_low),
            "entry_zone_high": _rounded(entry_high),
            "stop_loss": _rounded(stop_loss),
            "target_reference": _rounded(target),
            "risk_reward_ratio": _rounded(ratio),
            "support_basis": support_label,
            "resistance_basis": resistance_label,
            "atr14": _rounded(atr),
            "atr14_pct": _rounded(atr_pct),
            "rsi14": _rounded(rsi),
            "recommended_initial_position_pct": initial_position,
            "recommended_max_position_pct": max_position,
            "invalidation_conditions": [
                f"收盘有效跌破止损位{stop_loss:.2f}",
                "前八项基本面或风险门槛出现新的不通过证据",
                "6—12个月催化被取消、显著延期或证伪",
            ],
        })
        verdict = (
            f"进入{entry_low:.2f}—{entry_high:.2f}买入区间，止损{stop_loss:.2f}，"
            f"目标参考{target:.2f}，风险收益比{ratio:.2f}，可用{initial_position}%试仓、"
            f"最高{max_position}%"
            if passed and ratio is not None
            else "；".join(failure_reasons)
        )
        return CriterionResult(
            criterion_id=self.criterion_id,
            criterion_name=self.criterion_name,
            index=self.index,
            passed=passed,
            verdict=verdict,
            evidence=evidence,
            details=details,
        )
