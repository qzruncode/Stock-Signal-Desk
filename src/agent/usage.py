"""Provider-reported usage through LangChain's standard callback contract."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler

logger = logging.getLogger(__name__)


class PersistedUsageCallback(UsageMetadataCallbackHandler):
    """Retain native aggregation and persist each completed model call once.

    Missing usage remains unknown, not zero. Telemetry failures must never turn
    a successfully generated answer into a failed run.
    """

    def __init__(self, database: Any, agent_run_id: str) -> None:
        super().__init__()
        self.database = database
        self.agent_run_id = agent_run_id

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        super().on_llm_end(response, run_id=run_id, **kwargs)
        if not response.generations or not response.generations[0]:
            return
        message = getattr(response.generations[0][0], "message", None)
        usage = getattr(message, "usage_metadata", None)
        model = getattr(message, "response_metadata", {}).get("model_name")
        if not usage or not model or self.database is None:
            return
        try:
            self.database.record_agent_usage(
                self.agent_run_id, call_id=str(run_id), model=model, usage=dict(usage),
            )
        except Exception:
            logger.warning("Could not persist provider usage for run %s", self.agent_run_id, exc_info=True)
