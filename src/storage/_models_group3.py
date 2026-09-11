"""SQLAlchemy model group 3."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['ConversationMessage', 'LLMUsage', 'AlertRuleRecord', 'AlertTriggerRecord', 'AlertNotificationRecord', 'WatchlistGroup', 'AgentPromptTemplate', 'WatchlistGroupNameConflict']

class ConversationMessage(Base):
    """Agent 对话历史记录表"""

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)

class LLMUsage(Base):
    """Token-usage audit log."""

    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_type = Column(String(32), nullable=False, index=True)
    model = Column(String(128), nullable=False)
    stock_code = Column(String(16), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    called_at = Column(DateTime, default=datetime.now, index=True)

class AlertRuleRecord(Base):
    """Persisted alert rule."""

    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    state_json = Column(Text, nullable=False, default="{}")
    next_check_at = Column(DateTime)
    name = Column(String(64), nullable=False)
    target_scope = Column(String(32), nullable=False, default="single_symbol", index=True)
    target = Column(String(64), nullable=False, index=True)
    alert_type = Column(String(32), nullable=False, index=True)
    parameters = Column(Text, nullable=False, default="{}")
    severity = Column(String(16), nullable=False, default="warning", index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    source = Column(String(16), nullable=False, default="api", index=True)
    cooldown_policy = Column(Text)
    notification_policy = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        Index("ix_alert_rule_type_target", "alert_type", "target"),
        Index("ix_alert_rules_next_check", "next_check_at"),
        Index("ix_alert_rules_owner_source", "tenant_id", "owner_id", "source"),
    )

class AlertTriggerRecord(Base):
    """Alert trigger history."""

    __tablename__ = "alert_triggers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fingerprint = Column(String(64))
    rule_id = Column(Integer, index=True)
    target = Column(String(64), nullable=False, index=True)
    observed_value = Column(Float)
    threshold = Column(Float)
    reason = Column(Text)
    data_source = Column(String(64))
    data_timestamp = Column(DateTime, index=True)
    triggered_at = Column(DateTime, default=datetime.now, index=True)
    status = Column(String(16), nullable=False, default="triggered", index=True)
    diagnostics = Column(Text)

    __table_args__ = (
        Index("ix_alert_trigger_rule_time", "rule_id", "triggered_at"),
        Index("ix_alert_trigger_fingerprint", "fingerprint", unique=True),
    )

class AlertNotificationRecord(Base):
    """Notification attempt for alert triggers."""

    __tablename__ = "alert_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trigger_id = Column(Integer, index=True)
    channel = Column(String(32), nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=1)
    success = Column(Boolean, nullable=False, default=False, index=True)
    error_code = Column(String(64))
    retryable = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Integer)
    diagnostics = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (Index("ix_alert_notification_trigger_channel", "trigger_id", "channel"),)

class WatchlistGroup(Base):
    """自选股自定义分组"""

    __tablename__ = "watchlist_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    codes_json = Column(Text, nullable=False, default="[]")
    source = Column(String(16), nullable=False, default="manual")
    sort_order = Column(Integer, nullable=False, default=0, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        try:
            codes = json.loads(self.codes_json or "[]")
        except Exception:
            codes = []
        if not isinstance(codes, list):
            codes = []
        return {
            "id": str(self.id),
            "name": self.name,
            "codes": [str(c) for c in codes],
            "source": self.source,
            "sortOrder": self.sort_order,
        }

class AgentPromptTemplate(Base):
    """AI 助手 system prompt 模板（可在设置页配置）。"""

    __tablename__ = "agent_prompt_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    content = Column(Text, nullable=False, default="")
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class WatchlistGroupNameConflict(Exception):
    """分组名称与已有分组冲突。"""
