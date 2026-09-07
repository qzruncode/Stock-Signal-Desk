"""Method group extracted from financial_lifecycle."""

from __future__ import annotations

import src.storage.mixins.financial_lifecycle as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


class _FinancialLifecycleMethods1:
    def _register_financial_conclusions_in_session(
        self,
        session,
        *,
        run_id: str,
        conversation_id: str,
        tenant_id: str,
        owner_id: str,
        conclusions: Sequence[Mapping[str, Any]],
    ) -> int:
        created = 0
        for raw in conclusions:
            task_id = str(raw.get("task_id") or "").strip()
            symbol = str(raw.get("symbol") or "").strip()
            conclusion_type = str(
                raw.get("conclusion_type") or ""
            ).strip()
            verdict = str(raw.get("verdict") or "").strip()
            if (
                not task_id
                or not symbol
                or conclusion_type not in {"buy_gate", "research"}
                or verdict not in {"buy", "not_buy", "watch", "avoid"}
            ):
                continue
            identity = (
                f"{run_id}:{task_id}:{conclusion_type}:{symbol}"
            )
            conclusion_id = hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()
            if session.get(AgentFinancialConclusion, conclusion_id):
                continue
            raw_as_of = raw.get("as_of_at")
            as_of_at = (
                raw_as_of
                if isinstance(raw_as_of, datetime)
                else datetime.now()
            )
            baseline = (
                session.execute(
                    select(StockDaily)
                    .where(
                        StockDaily.code == symbol,
                        StockDaily.date < as_of_at.date(),
                        StockDaily.close.is_not(None),
                        StockDaily.close > 0,
                    )
                    .order_by(StockDaily.date.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            evidence = raw.get("evidence")
            evidence = (
                dict(evidence)
                if isinstance(evidence, Mapping)
                else {}
            )
            record = AgentFinancialConclusion(
                id=conclusion_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                run_id=run_id,
                conversation_id=conversation_id,
                task_id=task_id,
                conclusion_type=conclusion_type,
                symbol=symbol,
                name=(
                    str(raw.get("name") or "").strip() or None
                ),
                verdict=verdict,
                contract_version=(
                    str(raw.get("contract_version") or "").strip()
                    or None
                ),
                as_of_at=as_of_at,
                baseline_trade_date=(
                    baseline.date if baseline else None
                ),
                baseline_price=(
                    float(baseline.close)
                    if baseline and baseline.close is not None
                    else None
                ),
                baseline_source=(
                    str(baseline.data_source or "stock_daily")
                    if baseline
                    else None
                ),
                thesis_json=_json(raw.get("thesis") or {}),
                evidence_json=_json(evidence),
                evidence_fingerprint=str(
                    raw.get("evidence_fingerprint") or ""
                ),
                lifecycle_status="pending",
            )
            session.add(record)
            for horizon in DEFAULT_OUTCOME_HORIZONS:
                session.add(
                    AgentFinancialOutcome(
                        id=hashlib.sha256(
                            f"{conclusion_id}:{horizon}".encode("utf-8")
                        ).hexdigest(),
                        conclusion_id=conclusion_id,
                        horizon_trading_days=horizon,
                        status="pending",
                        engine_version=OUTCOME_ENGINE_VERSION,
                    )
                )
            created += 1
        return created

    def list_financial_conclusions(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        symbol: str | None = None,
        lifecycle_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            statement = select(AgentFinancialConclusion).where(
                AgentFinancialConclusion.tenant_id == tenant_id,
                AgentFinancialConclusion.owner_id == owner_id,
            )
            if symbol:
                statement = statement.where(
                    AgentFinancialConclusion.symbol == symbol
                )
            if lifecycle_status:
                statement = statement.where(
                    AgentFinancialConclusion.lifecycle_status
                    == lifecycle_status
                )
            records = (
                session.execute(
                    statement.order_by(
                        AgentFinancialConclusion.as_of_at.desc()
                    ).limit(max(1, min(limit, 1000)))
                )
                .scalars()
                .all()
            )
            ids = [record.id for record in records]
            outcomes = (
                session.execute(
                    select(AgentFinancialOutcome).where(
                        AgentFinancialOutcome.conclusion_id.in_(ids)
                    )
                )
                .scalars()
                .all()
                if ids
                else []
            )
            by_conclusion: dict[
                str,
                list[AgentFinancialOutcome],
            ] = defaultdict(list)
            for outcome in outcomes:
                by_conclusion[outcome.conclusion_id].append(outcome)
            return [
                _conclusion_dict(
                    record,
                    by_conclusion.get(record.id, []),
                )
                for record in records
            ]

    def refresh_financial_conclusion_outcomes(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        limit: int = 1000,
    ) -> dict[str, Any]:
        now = datetime.now()
        counters = {
            "conclusions_scanned": 0,
            "outcomes_completed": 0,
            "outcomes_pending": 0,
            "baselines_missing": 0,
        }
        with self.session_scope() as session:
            conclusions = (
                session.execute(
                    select(AgentFinancialConclusion)
                    .where(
                        AgentFinancialConclusion.tenant_id
                        == tenant_id,
                        AgentFinancialConclusion.owner_id
                        == owner_id,
                        AgentFinancialConclusion.lifecycle_status
                        != "completed",
                    )
                    .order_by(
                        AgentFinancialConclusion.updated_at.asc(),
                        AgentFinancialConclusion.as_of_at.asc(),
                    )
                    .limit(max(1, min(limit, 5000)))
                )
                .scalars()
                .all()
            )
            for conclusion in conclusions:
                counters["conclusions_scanned"] += 1
                conclusion.updated_at = now
                if (
                    conclusion.baseline_trade_date is None
                    or conclusion.baseline_price is None
                ):
                    baseline = (
                        session.execute(
                            select(StockDaily)
                            .where(
                                StockDaily.code == conclusion.symbol,
                                StockDaily.date
                                < conclusion.as_of_at.date(),
                                StockDaily.close.is_not(None),
                                StockDaily.close > 0,
                            )
                            .order_by(StockDaily.date.desc())
                            .limit(1)
                        )
                        .scalars()
                        .first()
                    )
                    if baseline is None:
                        counters["baselines_missing"] += 1
                        continue
                    conclusion.baseline_trade_date = baseline.date
                    conclusion.baseline_price = float(baseline.close)
                    conclusion.baseline_source = str(
                        baseline.data_source or "stock_daily"
                    )
                rows = (
                    session.execute(
                        select(StockDaily)
                        .where(
                            StockDaily.code == conclusion.symbol,
                            StockDaily.date
                            > conclusion.baseline_trade_date,
                            StockDaily.close.is_not(None),
                            StockDaily.close > 0,
                            StockDaily.date < now.date(),
                        )
                        .order_by(StockDaily.date.asc())
                        .limit(max(DEFAULT_OUTCOME_HORIZONS))
                    )
                    .scalars()
                    .all()
                )
                outcomes = (
                    session.execute(
                        select(AgentFinancialOutcome).where(
                            AgentFinancialOutcome.conclusion_id
                            == conclusion.id
                        )
                    )
                    .scalars()
                    .all()
                )
                for outcome in outcomes:
                    horizon = int(outcome.horizon_trading_days)
                    if outcome.status == "completed":
                        continue
                    if len(rows) < horizon:
                        counters["outcomes_pending"] += 1
                        outcome.diagnostics_json = _json(
                            {
                                "available_trading_days": len(rows),
                                "required_trading_days": horizon,
                            }
                        )
                        continue
                    window = rows[:horizon]
                    baseline_price = float(
                        conclusion.baseline_price
                    )
                    end_price = float(window[-1].close)
                    highs = [
                        float(row.high or row.close)
                        for row in window
                    ]
                    lows = [
                        float(row.low or row.close)
                        for row in window
                    ]
                    return_pct = (
                        (end_price / baseline_price) - 1.0
                    ) * 100
                    favorable = (
                        (max(highs) / baseline_price) - 1.0
                    ) * 100
                    adverse = (
                        (min(lows) / baseline_price) - 1.0
                    ) * 100
                    if conclusion.verdict == "buy":
                        label = (
                            "favorable"
                            if return_pct > 0
                            else "adverse"
                            if return_pct < 0
                            else "flat"
                        )
                        directional_success = return_pct > 0
                    elif conclusion.verdict == "not_buy":
                        label = (
                            "avoided_drawdown"
                            if return_pct < 0
                            else "missed_upside"
                            if return_pct > 0
                            else "flat"
                        )
                        # "不符合买入条件"并非看空预测，不把它伪装成
                        # 可用涨跌方向衡量的预测准确率。
                        directional_success = None
                    else:
                        label = "observed_return"
                        directional_success = None
                    outcome.status = "completed"
                    outcome.evaluated_through_date = window[-1].date
                    outcome.end_price = end_price
                    outcome.max_high = max(highs)
                    outcome.min_low = min(lows)
                    outcome.return_pct = round(return_pct, 6)
                    outcome.max_favorable_excursion_pct = round(
                        favorable,
                        6,
                    )
                    outcome.max_adverse_excursion_pct = round(
                        adverse,
                        6,
                    )
                    outcome.outcome_label = label
                    outcome.directional_success = directional_success
                    outcome.engine_version = OUTCOME_ENGINE_VERSION
                    outcome.diagnostics_json = _json(
                        {
                            "baseline_trade_date": _iso(
                                conclusion.baseline_trade_date
                            ),
                            "observations": horizon,
                            "semantics": (
                                "directional_validation"
                                if conclusion.verdict == "buy"
                                else "opportunity_cost_only" if conclusion.verdict == "not_buy" else "observation_only"
                            ),
                        }
                    )
                    outcome.evaluated_at = now
                    outcome.updated_at = now
                    counters["outcomes_completed"] += 1
                conclusion.lifecycle_status = (
                    "completed"
                    if outcomes
                    and all(
                        outcome.status == "completed"
                        for outcome in outcomes
                    )
                    else "pending"
                )
                conclusion.updated_at = now
        return counters



__all__ = ["_FinancialLifecycleMethods1"]
