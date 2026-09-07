from unittest.mock import MagicMock
import uuid

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from src.agent.langgraph_runtime.model import _generation_chunk
from src.agent.usage import PersistedUsageCallback
from src.storage import DatabaseManager


def test_standard_usage_and_native_callback():
    chunk = _generation_chunk({"model": "test-model", "choices": [], "usage": {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "prompt_tokens_details": {"cached_tokens": 7},
    }})
    assert chunk.message.usage_metadata["input_token_details"] == {"cache_read": 7}
    database = MagicMock()
    handler = PersistedUsageCallback(database, "parent-run")
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="ok", usage_metadata=chunk.message.usage_metadata,
        response_metadata=chunk.message.response_metadata,
    ))]]), run_id=uuid.uuid4())
    assert handler.usage_metadata["test-model"]["total_tokens"] == 15
    assert database.record_agent_usage.call_args.kwargs["usage"]["total_tokens"] == 15


def test_missing_provider_usage_is_unknown():
    assert _generation_chunk({"choices": []}).message.usage_metadata is None
    assert _generation_chunk({"choices": [], "usage": {"prompt_tokens": "bad", "completion_tokens": 1}}).message.usage_metadata is None


def test_usage_is_atomic_and_idempotent(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'usage.db'}")
    try:
        db.create_chat_conversation("conversation")
        db.claim_agent_run(run_id="run", conversation_id="conversation", request_payload={}, worker_id="test")
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        assert db.record_agent_usage("run", call_id="call", model="test", usage=usage)
        assert not db.record_agent_usage("run", call_id="call", model="test", usage=usage)
        metrics = db.agent_runtime_metrics()["workload_24h"]
        assert metrics["actual_usage"]["total_tokens"] == 15
        assert metrics["actual_usage"]["reported_calls"] == 1
    finally:
        DatabaseManager.reset_instance()
