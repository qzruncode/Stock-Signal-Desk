import asyncio
from copy import deepcopy
from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, RemoveMessage

from src.agent.langgraph_runtime.memory import ConversationMemoryMiddleware


def history():
    messages = [HumanMessage(content=f"historical request {i}", id=f"human-{i}") for i in range(30)]
    messages.extend([
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "call"}], id="tool-call"),
        ToolMessage(content="evidence", tool_call_id="call", id="tool-result"),
        HumanMessage(content="latest question", id="latest"),
    ])
    return {"messages": messages, "evidence": [{"id": "retained"}], "claim_evidence": [{"id": "claim"}]}


def test_native_summary_preserves_tool_pair_and_independent_ledgers(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    state = history()
    original = deepcopy(state)
    runtime = SimpleNamespace(context=SimpleNamespace(model=GenericFakeChatModel(messages=iter([AIMessage(content="History summary")]))))
    update = asyncio.run(ConversationMemoryMiddleware().abefore_model(state, runtime))
    assert list(update) == ["messages"]
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][-3:] == original["messages"][-3:]
    assert len(update["messages"]) < len(original["messages"])
    assert state == original


def test_failed_summary_does_not_replace_history(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    state = history()
    original = deepcopy(state)
    model = GenericFakeChatModel(messages=iter([]))
    assert asyncio.run(ConversationMemoryMiddleware().abefore_model(state, SimpleNamespace(context=SimpleNamespace(model=model)))) is None
    assert state == original


def test_disabled_or_invalid_summary_leaves_messages_intact(monkeypatch):
    for value in ("0", "bad-config"):
        monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", value)
        assert asyncio.run(ConversationMemoryMiddleware().abefore_model(history(), None)) is None


def test_empty_summary_does_not_discard_history(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    model = GenericFakeChatModel(messages=iter([AIMessage(content="")]))
    assert asyncio.run(ConversationMemoryMiddleware().abefore_model(history(), SimpleNamespace(context=SimpleNamespace(model=model)))) is None
