"""Method group extracted from agent_governance."""

from __future__ import annotations

import src.storage.mixins.agent_governance as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


class _AgentGovernanceMethods2:
    def list_agent_user_memories(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        conversation_id: str | None = None,
        enabled_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            statement = select(AgentUserMemory).where(
                AgentUserMemory.tenant_id == tenant_id,
                AgentUserMemory.owner_id == owner_id,
            )
            if conversation_id is not None:
                statement = statement.where(
                    or_(
                        AgentUserMemory.scope == "global",
                        (
                            AgentUserMemory.scope == "conversation"
                        )
                        & (
                            AgentUserMemory.conversation_id
                            == conversation_id
                        ),
                    )
                )
            if enabled_only:
                statement = statement.where(
                    AgentUserMemory.enabled.is_(True)
                )
            records = (
                session.execute(
                    statement.order_by(
                        AgentUserMemory.scope.asc(),
                        AgentUserMemory.updated_at.desc(),
                    ).limit(max(1, min(limit, 500)))
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": record.id,
                    "scope": record.scope,
                    "conversation_id": (
                        record.conversation_id or None
                    ),
                    "kind": record.kind,
                    "memory_key": record.memory_key,
                    "content": record.content,
                    "enabled": bool(record.enabled),
                    "source": record.source,
                    "created_at": _iso(record.created_at),
                    "updated_at": _iso(record.updated_at),
                }
                for record in records
            ]

    def upsert_agent_user_memory(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        memory_id: str | None,
        scope: str,
        conversation_id: str | None,
        kind: str,
        memory_key: str,
        content: str,
        enabled: bool,
    ) -> dict[str, Any]:
        if scope not in {"global", "conversation"}:
            raise ValueError("invalid memory scope")
        normalized_conversation_id = (
            ""
            if scope == "global"
            else str(conversation_id or "").strip()
        )
        if scope == "conversation" and not normalized_conversation_id:
            raise ValueError(
                "conversation_id is required for conversation memory"
            )
        now = datetime.now()
        with self.session_scope() as session:
            record = (
                session.get(AgentUserMemory, memory_id)
                if memory_id
                else None
            )
            if record is not None and (
                record.tenant_id != tenant_id
                or record.owner_id != owner_id
            ):
                raise KeyError("user_memory_not_found")
            if record is None:
                record = (
                    session.execute(
                        select(AgentUserMemory).where(
                            AgentUserMemory.tenant_id == tenant_id,
                            AgentUserMemory.owner_id == owner_id,
                            AgentUserMemory.scope == scope,
                            AgentUserMemory.conversation_id
                            == normalized_conversation_id,
                            AgentUserMemory.memory_key == memory_key,
                        )
                    )
                    .scalars()
                    .first()
                )
            if record is None:
                record = AgentUserMemory(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    source="explicit",
                    created_at=now,
                )
                session.add(record)
            record.scope = scope
            record.conversation_id = normalized_conversation_id
            record.kind = kind
            record.memory_key = memory_key
            record.content = content
            record.enabled = enabled
            record.updated_at = now
            self._append_agent_audit_event_in_session(
                session,
                tenant_id=tenant_id,
                owner_id=owner_id,
                event_type="user_memory_saved",
                resource_type="user_memory",
                resource_id=record.id,
                outcome="enabled" if enabled else "disabled",
                metadata={
                    "scope": scope,
                    "conversation_id": (
                        normalized_conversation_id or None
                    ),
                    "kind": kind,
                    "memory_key": memory_key,
                },
            )
            session.flush()
            return self.list_agent_user_memories_record(record)

    @staticmethod
    def list_agent_user_memories_record(
        record: AgentUserMemory,
    ) -> dict[str, Any]:
        return {
            "id": record.id,
            "scope": record.scope,
            "conversation_id": record.conversation_id or None,
            "kind": record.kind,
            "memory_key": record.memory_key,
            "content": record.content,
            "enabled": bool(record.enabled),
            "source": record.source,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    def delete_agent_user_memory(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        memory_id: str,
    ) -> bool:
        with self.session_scope() as session:
            record = session.get(AgentUserMemory, memory_id)
            if (
                record is None
                or record.tenant_id != tenant_id
                or record.owner_id != owner_id
            ):
                return False
            self._append_agent_audit_event_in_session(
                session,
                tenant_id=tenant_id,
                owner_id=owner_id,
                event_type="user_memory_deleted",
                resource_type="user_memory",
                resource_id=record.id,
                outcome="deleted",
                metadata={
                    "scope": record.scope,
                    "memory_key": record.memory_key,
                },
            )
            session.delete(record)
            return True



__all__ = ["_AgentGovernanceMethods2"]
