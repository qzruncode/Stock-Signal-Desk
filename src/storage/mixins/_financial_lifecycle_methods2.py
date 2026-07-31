"""Method group extracted from financial_lifecycle."""

from __future__ import annotations

import src.storage.mixins.financial_lifecycle as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


class _FinancialLifecycleMethods2:
    def financial_conclusion_calibration(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        horizon_trading_days: int = 20,
    ) -> dict[str, Any]:
        with self.get_session() as session:
            rows = (
                session.execute(
                    select(
                        AgentFinancialConclusion,
                        AgentFinancialOutcome,
                    )
                    .join(
                        AgentFinancialOutcome,
                        AgentFinancialOutcome.conclusion_id
                        == AgentFinancialConclusion.id,
                    )
                    .where(
                        AgentFinancialConclusion.tenant_id
                        == tenant_id,
                        AgentFinancialConclusion.owner_id
                        == owner_id,
                        AgentFinancialOutcome.horizon_trading_days
                        == horizon_trading_days,
                    )
                )
                .all()
            )
        completed = [
            (conclusion, outcome)
            for conclusion, outcome in rows
            if outcome.status == "completed"
        ]
        buy_rows = [
            outcome
            for conclusion, outcome in completed
            if conclusion.verdict == "buy"
        ]
        not_buy_rows = [
            outcome
            for conclusion, outcome in completed
            if conclusion.verdict == "not_buy"
        ]

        def average(values: Sequence[float | None]) -> float | None:
            clean = [
                float(value)
                for value in values
                if value is not None
            ]
            return (
                round(sum(clean) / len(clean), 6)
                if clean
                else None
            )

        return {
            "horizon_trading_days": horizon_trading_days,
            "conclusions": len(rows),
            "completed_outcomes": len(completed),
            "pending_outcomes": len(rows) - len(completed),
            "buy": {
                "count": len(buy_rows),
                "directional_accuracy": (
                    round(
                        sum(
                            outcome.directional_success is True
                            for outcome in buy_rows
                        )
                        / len(buy_rows),
                        6,
                    )
                    if buy_rows
                    else None
                ),
                "average_return_pct": average(
                    [outcome.return_pct for outcome in buy_rows]
                ),
                "average_max_favorable_excursion_pct": average(
                    [
                        outcome.max_favorable_excursion_pct
                        for outcome in buy_rows
                    ]
                ),
                "average_max_adverse_excursion_pct": average(
                    [
                        outcome.max_adverse_excursion_pct
                        for outcome in buy_rows
                    ]
                ),
            },
            "not_buy": {
                "count": len(not_buy_rows),
                "avoided_drawdown_rate": (
                    round(
                        sum(
                            outcome.outcome_label
                            == "avoided_drawdown"
                            for outcome in not_buy_rows
                        )
                        / len(not_buy_rows),
                        6,
                    )
                    if not_buy_rows
                    else None
                ),
                "missed_upside_rate": (
                    round(
                        sum(
                            outcome.outcome_label == "missed_upside"
                            for outcome in not_buy_rows
                        )
                        / len(not_buy_rows),
                        6,
                    )
                    if not_buy_rows
                    else None
                ),
                "average_forward_return_pct": average(
                    [outcome.return_pct for outcome in not_buy_rows]
                ),
                "semantic_note": (
                    "not_buy 表示未通过买入准入条件，不等同于看空；"
                    "这里只统计规避回撤与错失上涨，不计算方向准确率。"
                ),
            },
            "probability_calibration": {
                "available": False,
                "reason": (
                    "当前结论契约没有输出经过校准的概率，"
                    "系统不会从文本或八维通过数伪造置信度。"
                ),
            },
            "engine_version": OUTCOME_ENGINE_VERSION,
        }


__all__ = ["_FinancialLifecycleMethods2"]
