# -*- coding: utf-8 -*-
"""Durable Agent runtime reliability and isolation contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.chat_session_service import ChatSessionService
from src.agent.model_runtime import GuardedModelRuntime
from src.agent.terminal_publisher import AgentTerminalPublisher
from src.storage import DatabaseManager
from src.storage.models import AgentRun, AgentRunTrace



"""Shared fixtures for the focused test slices."""

@pytest.fixture
def database(tmp_path: Path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'agent-runtime.db'}")
    try:
        yield manager
    finally:
        DatabaseManager.reset_instance()

def _conversation(database: DatabaseManager, suffix: str = "1") -> str:
    conversation_id = f"conversation-{suffix}"
    database.create_chat_conversation(conversation_id)
    return conversation_id

def _claim(
    database: DatabaseManager,
    conversation_id: str,
    *,
    run_id: str,
    max_active_runs: int = 4,
):
    return database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={"messages": [{"role": "user", "content": "test"}]},
        worker_id="worker-a",
        lease_seconds=30,
        max_active_runs=max_active_runs,
    )
