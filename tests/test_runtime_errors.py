from src.agent.runtime_errors import build_runtime_error_receipt, emit_runtime_error


class _Events:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def stage(self, stage, status, summary, **kwargs):
        self.items.append(
            {
                "stage": stage,
                "status": status,
                "summary": summary,
                **kwargs,
            }
        )


def test_runtime_error_receipt_is_redacted_and_has_stable_identity() -> None:
    error = RuntimeError(
        "provider failed: token=secret-value-123456 "
        "Authorization: Bearer abcdefgh123456 "
        "owner=person@example.com phone=13812345678"
    )

    receipt = build_runtime_error_receipt(
        error,
        run_id="run-1",
        conversation_id="conversation-1",
        scope="expert",
        agent_id="market",
        task_id="market-task",
        node="atomic_tool_executor",
        tool_name="read_realtime_quote",
        tool_call_id="call-1",
        error_code="provider_unavailable",
        failure_kind="provider",
        retryable=True,
        fallback_eligible=True,
        fallback_status="pending",
        details={"arguments": {"token": "secret-value-123456"}},
    )

    assert receipt["id"] == receipt["error_id"]
    assert receipt["scope"] == "expert"
    assert receipt["fallback_eligible"] is True
    assert receipt["retryable"] is True
    rendered = str(receipt)
    assert "secret-value-123456" not in rendered
    assert "person@example.com" not in rendered
    assert "13812345678" not in rendered
    assert "Bearer abcdefgh123456" not in rendered


def test_runtime_error_event_contains_the_same_safe_receipt() -> None:
    events = _Events()
    receipt = emit_runtime_error(
        events,
        TimeoutError("upstream timeout"),
        summary="工具调用超时",
        error_code="timeout",
        failure_kind="timeout",
        retryable=True,
        fallback_eligible=True,
        fallback_status="pending",
        run_id="run-2",
        tool_name="read_recent_kline",
        tool_call_id="call-2",
        details={"arguments": {"query": "visible-safe-query"}},
    )

    assert len(events.items) == 1
    event = events.items[0]
    assert event["stage"] == "runtime_error"
    assert event["status"] == "failed"
    assert event["details"]["runtime_error"]["error_id"] == receipt["error_id"]
    assert event["details"]["details"]["arguments"]["query"] == "visible-safe-query"
