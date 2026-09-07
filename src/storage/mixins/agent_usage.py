"""Atomic, replay-safe provider usage, separate from budget estimates."""

import json

from langchain_core.callbacks.usage import add_usage
from sqlalchemy import select

from src.storage.models import AgentRun, LLMUsage


class AgentUsageMixin:
    def record_agent_usage(self, run_id: str, *, call_id: str, model: str, usage: dict) -> bool:
        def record(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            run = session.execute(statement).scalars().first()
            if run is None:
                return False
            current = json.loads(run.usage_json or "{}")
            call_ids = current.setdefault("call_ids", [])
            if call_id in call_ids:
                return False
            call_ids.append(call_id)
            models = current.setdefault("by_model", {})
            models[model] = add_usage(models.get(model), usage)
            current["total"] = add_usage(current.get("total"), usage)
            current["reported_calls"] = len(call_ids)
            current["source"] = "provider"
            run.usage_json = json.dumps(current)
            session.add(LLMUsage(
                call_type="agent", model=model,
                prompt_tokens=usage["input_tokens"],
                completion_tokens=usage["output_tokens"], total_tokens=usage["total_tokens"],
            ))
            return True
        return self._run_write_transaction("record_agent_usage", record)
