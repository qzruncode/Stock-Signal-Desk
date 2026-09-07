"""Owned, opt-in change subscriptions and their durable history."""

import json
from typing import Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.financial_lifecycle import _owner_scope
from src.services.research_alerts import evaluate_rule, rule_dict, save_rule, scoped_rules
from src.storage.models import AlertRuleRecord, AlertTriggerRecord


class AlertParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kinds: list[Literal["news", "financial", "research"]] = Field(default_factory=lambda: ["news", "financial", "research"], max_length=3)
    below_price: float | None = Field(None, gt=0, allow_inf_nan=False)
    interval_seconds: int = Field(900, ge=60, le=86400)
    cooldown_seconds: int = Field(3600, ge=60, le=604800)

    @model_validator(mode="after")
    def require_condition(self):
        self.kinds = list(dict.fromkeys(self.kinds))
        if not self.kinds and self.below_price is None:
            raise ValueError("至少选择一种变化类型或设置价格条件")
        return self


class AlertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    target_scope: Literal["single_symbol", "watchlist_group"] = "single_symbol"
    target: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9.\-_]+$")
    enabled: bool = False
    notification_enabled: bool = False
    parameters: AlertParameters = Field(default_factory=AlertParameters)


@router.get("/agent/research-alerts")
def list_alerts(request: Request, db=Depends(get_database_manager)):
    with db.get_session() as session:
        return {"items": [rule_dict(rule) for rule in session.execute(scoped_rules(*_owner_scope(request))).scalars()]}


@router.post("/agent/research-alerts")
def create_alert(payload: AlertRequest, request: Request, db=Depends(get_database_manager)):
    try:
        return save_rule(db, *_owner_scope(request), payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/agent/research-alerts/{rule_id}")
def update_alert(rule_id: int, payload: AlertRequest, request: Request, db=Depends(get_database_manager)):
    try:
        return save_rule(db, *_owner_scope(request), payload.model_dump(), rule_id)
    except KeyError as exc:
        raise HTTPException(404, "提醒不存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/agent/research-alerts/{rule_id}/check")
def check_alert(rule_id: int, request: Request, db=Depends(get_database_manager)):
    """Record changes; opted-in external delivery is handled by the worker."""
    tenant, owner = _owner_scope(request)
    try:
        return evaluate_rule(db, rule_id, tenant_id=tenant, owner_id=owner)
    except KeyError as exc:
        raise HTTPException(404, "提醒不存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/agent/research-alerts/history")
def alert_history(request: Request, db=Depends(get_database_manager)):
    owned = scoped_rules(*_owner_scope(request)).with_only_columns(AlertRuleRecord.id)
    with db.get_session() as session:
        rows = session.execute(select(AlertTriggerRecord).where(AlertTriggerRecord.rule_id.in_(owned))
                               .order_by(AlertTriggerRecord.triggered_at.desc()).limit(100)).scalars()
        return {"items": [{
            "id": item.id, "rule_id": item.rule_id, "name": item.reason, "status": item.status,
            "created_at": item.triggered_at.isoformat(), "changes": json.loads(item.diagnostics or "{}").get("changes", []),
        } for item in rows]}
