# -*- coding: utf-8 -*-
"""Price overdraft signal builder — combines PE percentiles, industry comparison, PEG, dividend."""

from __future__ import annotations

from typing import Optional

from api.v1.endpoints.financials._symbol import _safe_float
from api.v1.endpoints.financials._calc import _clamp


def _build_price_overdraft_signal(payload: dict) -> dict:
    pe_ttm = _safe_float(payload.get("pe_ttm"))
    pe_dynamic = _safe_float(payload.get("pe_dynamic"))
    pb = _safe_float(payload.get("pb"))
    peg = _safe_float(payload.get("peg"))
    dividend_yield = _safe_float(payload.get("dividend_yield"))
    pe_percentiles = payload.get("pe_percentiles") or {}
    industry_average = payload.get("industry_average") or {}
    industry_pe = _safe_float(industry_average.get("pe"))
    industry_pb = _safe_float(industry_average.get("pb"))

    pe_1y = _safe_float(pe_percentiles.get("1y"))
    pe_3y = _safe_float(pe_percentiles.get("3y"))
    pe_5y = _safe_float(pe_percentiles.get("5y"))

    pe_premium_vs_industry = (
        round((pe_ttm - industry_pe) / industry_pe * 100, 2)
        if pe_ttm is not None and industry_pe not in (None, 0)
        else None
    )
    pb_premium_vs_industry = (
        round((pb - industry_pb) / industry_pb * 100, 2)
        if pb is not None and industry_pb not in (None, 0)
        else None
    )
    dynamic_pe_discount_vs_ttm = (
        round((pe_ttm - pe_dynamic) / pe_ttm * 100, 2)
        if pe_ttm not in (None, 0) and pe_dynamic is not None
        else None
    )

    expensive_score = 0.0
    expectation_support_score = 50.0
    signals: list[str] = []
    reasoning: list[str] = []
    limitations: list[str] = []

    percentile_anchor = max(v for v in (pe_1y, pe_3y, pe_5y) if v is not None) if any(
        v is not None for v in (pe_1y, pe_3y, pe_5y)
    ) else None
    if percentile_anchor is not None:
        expensive_score += _clamp((percentile_anchor - 50) * 0.7, 0, 35)
        if percentile_anchor >= 90:
            signals.append("pe_percentile_extremely_high")
            reasoning.append(f"PE 历史分位处于高位（最高分位 {percentile_anchor:.2f}%），说明当前定价接近历史偏贵区间。")
        elif percentile_anchor >= 75:
            signals.append("pe_percentile_high")
            reasoning.append(f"PE 历史分位偏高（最高分位 {percentile_anchor:.2f}%），估值安全边际正在收窄。")
    else:
        limitations.append("缺少足够的历史 PE 分位数据，无法完整评估当前估值所处区间。")

    if pe_premium_vs_industry is not None:
        expensive_score += _clamp(pe_premium_vs_industry * 0.25, 0, 25)
        if pe_premium_vs_industry >= 40:
            signals.append("pe_premium_vs_industry_high")
            reasoning.append(f"当前 PE 相对行业平均溢价 {pe_premium_vs_industry:.2f}%，市场已经计入更高成长预期。")
        elif pe_premium_vs_industry <= -15:
            signals.append("pe_discount_vs_industry")
            reasoning.append(f"当前 PE 低于行业平均 {abs(pe_premium_vs_industry):.2f}%，纯估值层面的透支压力有限。")
    else:
        limitations.append("缺少可比行业 PE，行业相对估值判断不完整。")

    if pb_premium_vs_industry is not None:
        expensive_score += _clamp(pb_premium_vs_industry * 0.12, 0, 10)
        if pb_premium_vs_industry >= 35:
            signals.append("pb_premium_vs_industry_high")
            reasoning.append(f"PB 相对行业也存在 {pb_premium_vs_industry:.2f}% 溢价，说明高定价不只体现在盈利倍数。")
    else:
        limitations.append("缺少可比行业 PB，资产端估值溢价无法充分验证。")

    if dynamic_pe_discount_vs_ttm is not None:
        if dynamic_pe_discount_vs_ttm >= 25:
            expectation_support_score += 20
            signals.append("forward_pe_improving")
            reasoning.append(f"动态 PE 较 TTM 下降 {dynamic_pe_discount_vs_ttm:.2f}%，说明市场预期未来盈利改善能够部分消化高估值。")
        elif dynamic_pe_discount_vs_ttm >= 10:
            expectation_support_score += 10
        elif dynamic_pe_discount_vs_ttm <= 0:
            expectation_support_score -= 15
            signals.append("forward_pe_not_improving")
            reasoning.append("动态 PE 没有明显低于 TTM PE，意味着盈利改善预期对当前高估值的消化能力有限。")
    else:
        limitations.append("缺少动态 PE 或 TTM PE，无法判断未来盈利预期是否显著改善。")

    if peg is not None:
        if peg <= 1:
            expectation_support_score += 20
            signals.append("peg_supportive")
            reasoning.append(f"PEG 为 {peg:.2f}，估值与增长匹配度较好。")
        elif peg <= 1.5:
            expectation_support_score += 5
        elif peg <= 2:
            expectation_support_score -= 10
            signals.append("peg_elevated")
            reasoning.append(f"PEG 为 {peg:.2f}，增长对估值的支撑开始偏弱。")
        else:
            expectation_support_score -= 25
            signals.append("peg_above_2")
            reasoning.append(f"PEG 为 {peg:.2f}，当前估值对增长兑现的要求较高。")
    else:
        limitations.append("缺少 PEG，无法直接衡量估值与增长预期是否匹配。")

    if dividend_yield is not None:
        if dividend_yield >= 3:
            expectation_support_score += 8
            signals.append("dividend_buffer_strong")
        elif dividend_yield < 1:
            expectation_support_score -= 8
            signals.append("dividend_buffer_weak")
    else:
        limitations.append("缺少股息率，无法评估现金回报对高估值的缓冲作用。")

    expensive_score = round(_clamp(expensive_score, 0, 100), 2)
    expectation_support_score = round(_clamp(expectation_support_score, 0, 100), 2)

    evidence_count = sum(
        metric is not None
        for metric in (
            percentile_anchor,
            pe_premium_vs_industry,
            pb_premium_vs_industry,
            dynamic_pe_discount_vs_ttm,
            peg,
            dividend_yield,
        )
    )
    confidence = round(_clamp(evidence_count / 6 * 100, 0, 100), 2)
    overdraft_score = round(_clamp(expensive_score * 0.65 + (100 - expectation_support_score) * 0.35, 0, 100), 2)

    if evidence_count < 2:
        status = "uncertain"
        reasoning.append("可用估值证据较少，当前更适合把结果视作提示信号而非明确结论。")
    elif overdraft_score >= 75:
        status = "high"
    elif overdraft_score >= 55:
        status = "medium"
    elif overdraft_score >= 35:
        status = "watch"
    else:
        status = "low"

    if not reasoning:
        reasoning.append("现有估值与预期信号没有出现明显背离，短期内未观察到强烈的透支特征。")

    return {
        "status": status,
        "score": overdraft_score,
        "confidence": confidence,
        "valuation_expensive_score": expensive_score,
        "expectation_support_score": expectation_support_score,
        "signals": signals,
        "metrics": {
            "pe_ttm": pe_ttm,
            "pe_dynamic": pe_dynamic,
            "pb": pb,
            "peg": peg,
            "dividend_yield": dividend_yield,
            "pe_percentile_1y": pe_1y,
            "pe_percentile_3y": pe_3y,
            "pe_percentile_5y": pe_5y,
            "industry_pe": industry_pe,
            "industry_pb": industry_pb,
            "pe_premium_vs_industry": pe_premium_vs_industry,
            "pb_premium_vs_industry": pb_premium_vs_industry,
            "dynamic_pe_discount_vs_ttm": dynamic_pe_discount_vs_ttm,
        },
        "reasoning": reasoning[:4],
        "limitations": limitations[:4],
    }