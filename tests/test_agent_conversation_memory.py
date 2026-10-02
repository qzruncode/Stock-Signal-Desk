import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage, RemoveMessage
from langchain_core.messages.utils import count_tokens_approximately
from pydantic import Field

from src.agent.langgraph_runtime.memory import ConversationMemoryMiddleware


class RecordingSummaryModel(GenericFakeChatModel):
    prompts: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)

    def _generate(self, messages, *args, **kwargs):
        self.prompts.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


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


def test_planning_skips_redundant_full_checkpoint_summary(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    state = {**history(), "planning_enabled": True, "planning_status": "finalizing"}
    original = deepcopy(state)
    model = RecordingSummaryModel(messages=iter([]), prompts=[])

    update = asyncio.run(
        ConversationMemoryMiddleware().abefore_model(
            state,
            SimpleNamespace(context=SimpleNamespace(model=model)),
        )
    )

    assert update is None
    assert not model.prompts
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


def test_summary_input_keeps_early_constraints_when_trim_limit_is_unset(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    monkeypatch.delenv("AGENT_SUMMARY_TRIM_TOKENS", raising=False)
    state = history()
    state["messages"][0] = HumanMessage(
        content="EARLY_USER_CONSTRAINT_MUST_SURVIVE " + state["messages"][0].content,
        id="human-0",
    )
    model = RecordingSummaryModel(
        messages=iter([AIMessage(content="Summary keeps the constraint")]),
        prompts=[],
    )

    update = asyncio.run(
        ConversationMemoryMiddleware().abefore_model(
            state,
            SimpleNamespace(context=SimpleNamespace(model=model)),
        )
    )

    assert update is not None
    assert model.prompts
    assert "EARLY_USER_CONSTRAINT_MUST_SURVIVE" in str(model.prompts[0][0].content)


def test_summary_input_is_bounded_by_model_window_and_keeps_both_ends(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "8_000")
    monkeypatch.setenv("AGENT_SUMMARY_TRIM_TOKENS", "0")
    monkeypatch.setenv("AGENT_SUMMARY_KEEP_MESSAGES", "2")
    messages = [
        HumanMessage(
            content="EARLY_CONSTRAINT_MUST_SURVIVE " + ("early detail " * 600),
            id="early",
        )
    ]
    messages.extend(
        HumanMessage(
            content=(
                f"middle-{index} "
                + ("RECENT_EVICTED " if index == 16 else "")
                + ("historical detail " * 180)
            ),
            id=f"middle-{index}",
        )
        for index in range(18)
    )
    messages.append(HumanMessage(content="LATEST_CONTEXT", id="latest"))
    model = RecordingSummaryModel(
        messages=iter([AIMessage(content="bounded summary")]),
        prompts=[],
        profile={"max_input_tokens": 16_000},
    )

    update = asyncio.run(
        ConversationMemoryMiddleware().abefore_model(
            {"messages": messages},
            SimpleNamespace(context=SimpleNamespace(model=model)),
        )
    )

    assert update is not None
    assert model.prompts
    prompt = model.prompts[0]
    assert "EARLY_CONSTRAINT_MUST_SURVIVE" in str(prompt[0].content)
    assert "RECENT_EVICTED" in str(prompt[0].content)
    assert update["messages"][-1].id == "latest"
    assert count_tokens_approximately(prompt) < 6_000


def test_summary_skips_when_model_window_has_no_safe_summary_budget(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    state = history()
    model = RecordingSummaryModel(
        messages=iter([AIMessage(content="should not be called")]),
        prompts=[],
        profile={"max_input_tokens": 8_192},
    )

    assert (
        asyncio.run(
            ConversationMemoryMiddleware().abefore_model(
                state,
                SimpleNamespace(context=SimpleNamespace(model=model)),
            )
        )
        is None
    )
    assert not model.prompts


def test_summary_does_not_replace_history_with_native_empty_placeholder(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    monkeypatch.delenv("AGENT_SUMMARY_TRIM_TOKENS", raising=False)
    model = RecordingSummaryModel(
        messages=iter([AIMessage(content="should not run")]),
        prompts=[],
        profile={"max_input_tokens": 14_145},
    )

    assert (
        asyncio.run(
            ConversationMemoryMiddleware().abefore_model(
                history(),
                SimpleNamespace(context=SimpleNamespace(model=model)),
            )
        )
        is None
    )
    assert not model.prompts


@pytest.mark.parametrize("budget", [256, 512, 1024])
@pytest.mark.parametrize("content_kind", ["english", "chinese", "blocks", "head_block", "tail_block"])
def test_summary_keeps_both_edges_of_one_long_message(monkeypatch, budget, content_kind):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    monkeypatch.setenv("AGENT_SUMMARY_TRIM_TOKENS", str(budget))
    monkeypatch.setenv("AGENT_SUMMARY_KEEP_MESSAGES", "2")
    text = "HEAD_CONSTRAINT " + ("历史约束不得覆盖 " if content_kind == "chinese" else "historical detail ") * 4_000 + " TAIL_CONSTRAINT"
    content = text
    if content_kind == "blocks":
        content = [{"type": "text", "text": text}]
    elif content_kind == "head_block":
        content = [
            {"type": "text", "text": "HEAD_CONSTRAINT"},
            {"type": "text", "text": text.removeprefix("HEAD_CONSTRAINT ")},
        ]
    elif content_kind == "tail_block":
        content = [
            {"type": "text", "text": text.removesuffix(" TAIL_CONSTRAINT")},
            {"type": "text", "text": "TAIL_CONSTRAINT"},
        ]
    original_content = deepcopy(content)
    long_message = HumanMessage(
        content=content,
        id="long-message",
    )
    model = RecordingSummaryModel(
        messages=iter([AIMessage(content="bounded summary")]),
        prompts=[],
    )

    update = asyncio.run(
        ConversationMemoryMiddleware().abefore_model(
            {
                "messages": [
                    long_message,
                    HumanMessage(content="middle", id="middle"),
                    HumanMessage(content="latest", id="latest"),
                ]
            },
            SimpleNamespace(context=SimpleNamespace(model=model)),
        )
    )

    assert update is not None
    prompt = str(model.prompts[0][0].content)
    assert "HEAD_CONSTRAINT" in prompt
    assert "TAIL_CONSTRAINT" in prompt
    assert count_tokens_approximately(model.prompts[0]) <= budget + 512
    assert long_message.content == original_content


def test_small_summary_keeps_recent_tool_evidence_and_early_constraint(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    monkeypatch.setenv("AGENT_SUMMARY_TRIM_TOKENS", "256")
    monkeypatch.setenv("AGENT_SUMMARY_KEEP_MESSAGES", "2")
    messages = [
        HumanMessage(content="HEAD_CONSTRAINT " + "details " * 4_000, id="old"),
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "call"}], id="call-message"),
        ToolMessage(content="evidence " * 4_000 + " TAIL_EVIDENCE", tool_call_id="call", id="tool-message"),
        HumanMessage(content="recent", id="recent"),
        HumanMessage(content="latest", id="latest"),
    ]
    model = RecordingSummaryModel(messages=iter([AIMessage(content="summary")]))
    update = asyncio.run(ConversationMemoryMiddleware().abefore_model(
        {"messages": messages}, SimpleNamespace(context=SimpleNamespace(model=model)),
    ))
    assert update is not None
    assert "HEAD_CONSTRAINT" in model.prompts[0][0].content
    assert "TAIL_EVIDENCE" in model.prompts[0][0].content
    assert update["messages"][-2:] == messages[-2:]


def test_summary_skips_when_one_edge_cannot_fit(monkeypatch):
    from src.agent.langgraph_runtime.memory import ContextAwareSummarizationMiddleware

    monkeypatch.setenv("AGENT_SUMMARY_TRIGGER_TOKENS", "1")
    monkeypatch.setenv("AGENT_SUMMARY_KEEP_MESSAGES", "2")
    # Force the native trimmer below the cost of even one message envelope.
    monkeypatch.setattr("src.agent.langgraph_runtime.memory._summary_trim_limit", lambda _model: 8)
    model = RecordingSummaryModel(messages=iter([AIMessage(content="must not run")]))
    state = history()
    original = deepcopy(state)
    update = asyncio.run(ConversationMemoryMiddleware().abefore_model(
        state, SimpleNamespace(context=SimpleNamespace(model=model)),
    ))
    assert update is None
    assert not model.prompts
    assert state == original
    assert "_acreate_summary" not in ContextAwareSummarizationMiddleware.__dict__
