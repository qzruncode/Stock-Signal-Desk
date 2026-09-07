"""Change-only research alerts over existing synchronized data and notification delivery.

Rules are owner scoped and opt-in. The first observation establishes a baseline.
All external delivery is claimed durably before sending; ambiguous attempts are
not automatically resent. This is not a realtime market feed or a trading engine.
"""

from datetime import datetime, timedelta
import hashlib
import json
import logging

from sqlalchemy import func, or_, select, update

from src.storage.models import (
    AgentFinancialConclusion,
    AlertNotificationRecord,
    AlertRuleRecord,
    AlertTriggerRecord,
    WatchlistGroup,
)

SOURCE = "research"
FINANCIAL_FIELDS = (
    "report_date",
    "revenue_ttm",
    "parent_net_profit_ttm",
    "deducted_net_profit_ttm",
    "debt_ratio",
)


def rule_dict(rule):
    return {
        "id": rule.id,
        "name": rule.name,
        "target_scope": rule.target_scope,
        "target": rule.target,
        "enabled": rule.enabled,
        "parameters": json.loads(rule.parameters),
        "notification_enabled": bool(
            json.loads(rule.notification_policy or "{}").get("enabled")
        ),
        "state": {
            k: v
            for k, v in json.loads(rule.state_json or "{}").items()
            if k != "snapshot"
        },
        "next_check_at": rule.next_check_at.isoformat() if rule.next_check_at else None,
    }


def scoped_rules(tenant_id, owner_id):
    return select(AlertRuleRecord).where(
        AlertRuleRecord.source == SOURCE,
        AlertRuleRecord.tenant_id == tenant_id,
        AlertRuleRecord.owner_id == owner_id,
    )


def target_codes(session, scope, target):
    if scope == "single_symbol":
        return [target]
    group = session.get(WatchlistGroup, int(target))
    if group is None:
        raise ValueError("自选分组不存在")
    codes = list(dict.fromkeys(json.loads(group.codes_json or "[]")))
    if not codes:
        raise ValueError("自选分组为空，请先添加股票")
    if len(codes) > 200:
        raise ValueError("每条提醒最多跟踪 200 只股票，请拆分自选分组")
    return codes


def save_rule(database, tenant_id, owner_id, payload, rule_id=None):
    def write(session):
        target_codes(session, payload["target_scope"], payload["target"])
        rule = (
            session.execute(
                scoped_rules(tenant_id, owner_id).where(AlertRuleRecord.id == rule_id)
            )
            .scalars()
            .first()
            if rule_id
            else None
        )
        if rule_id and rule is None:
            raise KeyError("提醒不存在")
        if rule is None:
            rule = AlertRuleRecord(
                tenant_id=tenant_id,
                owner_id=owner_id,
                source=SOURCE,
                alert_type="research_change",
            )
            session.add(rule)
        changed_scope = (
            rule.target != payload["target"]
            or rule.target_scope != payload["target_scope"]
        )
        rule.name = payload["name"]
        rule.target, rule.target_scope = payload["target"], payload["target_scope"]
        parameters = json.dumps(payload["parameters"], sort_keys=True)
        reset_baseline = changed_scope or rule.parameters != parameters
        if reset_baseline:
            rule.state_json = "{}"
        if rule.id and (
            reset_baseline
            or not payload["enabled"]
            or not payload["notification_enabled"]
        ):
            # Revocation is durable even if the rule is re-enabled before the
            # worker next sweeps pending notifications.
            session.execute(
                update(AlertTriggerRecord)
                .where(
                    AlertTriggerRecord.rule_id == rule.id,
                    AlertTriggerRecord.status == "pending",
                )
                .values(status="recorded")
            )
        rule.parameters = parameters
        rule.enabled = payload["enabled"]
        rule.notification_policy = json.dumps(
            {"enabled": payload["notification_enabled"]}
        )
        rule.next_check_at = datetime.now()
        session.flush()
        return rule_dict(rule)

    return database._run_write_transaction("save_research_alert", write)


def _latest_rows(session, statement, symbol_column, order_column, limit=1):
    """Bound each symbol using SQLAlchemy's native SQL window function."""
    ranked = statement.add_columns(
        func.row_number()
        .over(partition_by=symbol_column, order_by=order_column.desc())
        .label("_rank"),
    ).subquery()
    return (
        session.execute(select(ranked).where(ranked.c._rank <= limit)).mappings().all()
    )


def snapshot(session, codes, tenant_id, owner_id, now, market_data=None):
    from src.services.market_data_client import get_market_data_client

    current = market_data or get_market_data_client().snapshot(
        codes, ["financials", "kline", "news"], count=1, wait=0
    )
    conclusions = {
        item["symbol"]: item
        for item in _latest_rows(
            session,
            select(
                AgentFinancialConclusion.symbol,
                AgentFinancialConclusion.verdict,
                AgentFinancialConclusion.thesis_json,
            ).where(
                AgentFinancialConclusion.symbol.in_(codes),
                AgentFinancialConclusion.tenant_id == tenant_id,
                AgentFinancialConclusion.owner_id == owner_id,
            ),
            AgentFinancialConclusion.symbol,
            AgentFinancialConclusion.as_of_at,
        )
    }
    result = {}
    for code in codes:
        item = current["items"][code]
        meta = item.get("financials")
        bars = item.get("kline") or []
        bar = bars[-1] if bars else None
        conclusion = conclusions.get(code)
        news = {
            row.get("url") or row.get("link"): {
                "title": row.get("title"),
                "source": row.get("source"),
                "published_at": row.get("published_date")
                or row.get("published")
                or "未知",
            }
            for row in item.get("news", [])
            if row.get("url") or row.get("link")
        }
        result[code] = {
            "news": news,
            "financial": {field: meta.get(field) for field in FINANCIAL_FIELDS}
            if meta and meta.get("report_date")
            else None,
            "research": {
                "verdict": conclusion["verdict"],
                "blocks": [
                    {"content": block.get("content"), "kind": block.get("kind")}
                    for block in json.loads(conclusion["thesis_json"]).get("blocks", [])
                ],
            }
            if conclusion
            else None,
            "price": {
                "close": bar["close"],
                "date": str(bar["date"]),
                "source": bar.get("data_source"),
            }
            if bar
            else None,
            "data_versions": item.get("versions", {}),
        }
    return result


def changes_between(before, after, parameters):
    changes = []
    for code, current in after.items():
        previous = before.get(code)
        if previous is None:
            continue  # A newly added group member also starts with a baseline.
        for kind in parameters["kinds"]:
            old, new = previous.get(kind), current.get(kind)
            if kind == "news":
                added = {
                    url: item
                    for url, item in (new or {}).items()
                    if url not in (old or {})
                }
                if added:
                    changes.append(
                        {"symbol": code, "kind": kind, "before": None, "after": added}
                    )
            elif new is not None and new != old:
                changes.append(
                    {"symbol": code, "kind": kind, "before": old, "after": new}
                )
        threshold = parameters.get("below_price")
        old_price, new_price = previous.get("price"), current.get("price")
        if (
            threshold
            and old_price
            and new_price
            and old_price["close"] >= threshold > new_price["close"]
        ):
            changes.append(
                {
                    "symbol": code,
                    "kind": "below_price",
                    "before": old_price,
                    "after": new_price,
                    "threshold": threshold,
                }
            )
    return changes


def evaluate_rule(database, rule_id, *, tenant_id=None, owner_id=None, now=None):
    now = now or datetime.now()
    from src.services.market_data_client import get_market_data_client, MarketDataError

    with database.get_session() as read_session:
        query = select(AlertRuleRecord).where(
            AlertRuleRecord.id == rule_id, AlertRuleRecord.source == SOURCE
        )
        if tenant_id is not None:
            query = query.where(
                AlertRuleRecord.tenant_id == tenant_id,
                AlertRuleRecord.owner_id == owner_id,
            )
        selected = read_session.scalar(query)
        if selected is None:
            raise KeyError("提醒不存在")
        if not selected.enabled:
            return {"status": "disabled", "changes": []}
        codes = target_codes(read_session, selected.target_scope, selected.target)
    try:
        market_data = get_market_data_client().snapshot(
            codes, ["financials", "kline", "news"], count=1, wait=0
        )
    except MarketDataError as exc:
        with database.session_scope() as write_session:
            rule = write_session.get(AlertRuleRecord, rule_id)
            if rule is not None:
                state = json.loads(rule.state_json or "{}")
                state.update(error=str(exc), checked_at=now.isoformat())
                rule.state_json = json.dumps(state, ensure_ascii=False)
                rule.next_check_at = now + timedelta(seconds=60)
        return {"status": "data_pending", "changes": [], "error": str(exc)}

    def evaluate(session):
        statement = select(AlertRuleRecord).where(
            AlertRuleRecord.id == rule_id, AlertRuleRecord.source == SOURCE
        )
        if tenant_id is not None:
            statement = statement.where(
                AlertRuleRecord.tenant_id == tenant_id,
                AlertRuleRecord.owner_id == owner_id,
            )
        if not database._is_sqlite_engine:
            statement = statement.with_for_update()
        rule = session.execute(statement).scalars().first()
        if rule is None:
            raise KeyError("提醒不存在")
        if not rule.enabled:
            return {"status": "disabled", "changes": []}
        state = json.loads(rule.state_json or "{}")
        parameters = json.loads(rule.parameters)
        if set(target_codes(session, rule.target_scope, rule.target)) != set(codes):
            return {"status": "scope_changed", "changes": []}
        current = snapshot(
            session, codes, rule.tenant_id, rule.owner_id, now, market_data=market_data
        )
        changes = changes_between(state.get("snapshot", {}), current, parameters)
        previous_sent = (
            datetime.fromisoformat(state["notified_at"])
            if state.get("notified_at")
            else None
        )
        cooldown = previous_sent and now < previous_sent + timedelta(
            seconds=parameters["cooldown_seconds"]
        )
        rule.next_check_at = now + timedelta(seconds=parameters["interval_seconds"])
        # During cooldown retain the last reported baseline so changes are not silently lost.
        if cooldown and changes:
            state["checked_at"] = now.isoformat()
            rule.state_json = json.dumps(state)
            return {"status": "cooldown", "changes": changes}
        baseline = state.get("snapshot", {})
        state.pop("error", None)
        state.update(snapshot=current, checked_at=now.isoformat())
        trigger_id = None
        if changes:
            fingerprint = hashlib.sha256(
                json.dumps(
                    [rule.id, baseline, current, state.get("notified_at")],
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            existing = (
                session.execute(
                    select(AlertTriggerRecord).where(
                        AlertTriggerRecord.fingerprint == fingerprint
                    )
                )
                .scalars()
                .first()
            )
            if existing is None:
                external = bool(
                    json.loads(rule.notification_policy or "{}").get("enabled")
                )
                trigger = AlertTriggerRecord(
                    rule_id=rule.id,
                    target=rule.target,
                    fingerprint=fingerprint,
                    reason=rule.name,
                    diagnostics=json.dumps(
                        {"changes": changes}, ensure_ascii=False, default=str
                    ),
                    data_source="synchronized_data",
                    data_timestamp=now,
                    status="pending" if external else "recorded",
                )
                session.add(trigger)
                session.flush()
                trigger_id = trigger.id
                state["notified_at"] = now.isoformat()
        rule.state_json = json.dumps(state, ensure_ascii=False, default=str)
        return {
            "status": "changed" if changes else "unchanged",
            "changes": changes,
            "trigger_id": trigger_id,
        }

    return database._run_write_transaction("evaluate_research_alert", evaluate)


def dispatch_notification(database, trigger_id, send=None):
    def claim(session):
        trigger = session.get(AlertTriggerRecord, trigger_id)
        if trigger is None or trigger.status != "pending":
            return None
        rule = session.get(AlertRuleRecord, trigger.rule_id)
        if (
            not rule
            or not rule.enabled
            or not json.loads(rule.notification_policy or "{}").get("enabled")
        ):
            trigger.status = "recorded"
            return None
        # Conditional UPDATE is the cross-worker once-only claim.
        claimed = session.execute(
            update(AlertTriggerRecord)
            .where(
                AlertTriggerRecord.id == trigger_id,
                AlertTriggerRecord.status == "pending",
            )
            .values(status="sending")
        ).rowcount
        return (rule.name, trigger.diagnostics) if claimed else None

    payload = database._run_write_transaction("claim_research_notification", claim)
    if payload is None:
        return False
    try:
        if send is None:
            from src.notification import get_notification_service

            send = get_notification_service().send
        sent = bool(
            send(f"# {payload[0]}\n\n同步数据变化（非实时行情）：\n\n{payload[1]}")
        )
    except Exception:
        sent = False
    with database.session_scope() as session:
        trigger = session.get(AlertTriggerRecord, trigger_id)
        trigger.status = "sent" if sent else "delivery_unknown"
        session.add(
            AlertNotificationRecord(
                trigger_id=trigger_id,
                channel="wechat",
                success=sent,
                retryable=False,
                error_code=None if sent else "delivery_unconfirmed",
            )
        )
    return sent


def sweep_research(database):
    now = datetime.now()
    with database.get_session() as session:
        rules = (
            session.execute(
                select(AlertRuleRecord.id)
                .where(
                    AlertRuleRecord.source == SOURCE,
                    AlertRuleRecord.enabled.is_(True),
                    or_(
                        AlertRuleRecord.next_check_at.is_(None),
                        AlertRuleRecord.next_check_at <= now,
                    ),
                )
                .order_by(AlertRuleRecord.next_check_at.asc())
                .limit(100)
            )
            .scalars()
            .all()
        )
        pending = (
            session.execute(
                select(AlertTriggerRecord.id)
                .join(
                    AlertRuleRecord,
                    AlertRuleRecord.id == AlertTriggerRecord.rule_id,
                )
                .where(
                    AlertRuleRecord.source == SOURCE,
                    AlertTriggerRecord.status == "pending",
                )
                .limit(100)
            )
            .scalars()
            .all()
        )
        owners = session.execute(
            select(
                AgentFinancialConclusion.tenant_id, AgentFinancialConclusion.owner_id
            )
            .where(
                AgentFinancialConclusion.lifecycle_status != "completed",
            )
            .distinct()
        ).all()
    logger = logging.getLogger(__name__)
    for tenant, owner in owners:
        try:
            database.refresh_financial_conclusion_outcomes(
                tenant_id=tenant, owner_id=owner
            )
        except Exception:
            logger.exception("Research outcomes refresh failed")
    for rule_id in rules:
        try:
            result = evaluate_rule(database, rule_id, now=now)
            if result.get("trigger_id"):
                pending.append(result["trigger_id"])
        except Exception as exc:
            logger.exception("Research rule %s check failed", rule_id)
            error_message = (
                str(exc) if isinstance(exc, ValueError) else "检查失败，请查看服务日志"
            )

            def record_error(session):
                rule = session.get(AlertRuleRecord, rule_id)
                if rule is not None:
                    state = json.loads(rule.state_json or "{}")
                    state.update(
                        checked_at=now.isoformat(),
                        error=error_message,
                    )
                    rule.state_json = json.dumps(state, ensure_ascii=False)
                    rule.next_check_at = now + timedelta(minutes=5)

            database._run_write_transaction("record_research_check_error", record_error)
    for trigger_id in dict.fromkeys(pending):
        try:
            dispatch_notification(database, trigger_id)
        except Exception:
            logger.exception("Research notification %s failed", trigger_id)


async def run_research_monitor(database):
    """Use the existing distributed resource lease and FastAPI worker lifecycle."""
    import asyncio
    import logging
    from src.agent.resource_scheduler import (
        agent_resource_lease,
        ResourceCapacityExceeded,
    )

    while True:
        try:
            async with agent_resource_lease(
                database,
                resource_name="maintenance:research",
                slots=1,
                lease_seconds=120.0,
                wait=False,
            ):
                await asyncio.to_thread(sweep_research, database)
        except ResourceCapacityExceeded:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception("Research maintenance failed")
        await asyncio.sleep(60)
