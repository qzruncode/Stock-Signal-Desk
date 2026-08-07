from __future__ import annotations

from api.v1.endpoints.agent.health import _assess_tool_data_health


def test_health_rejects_non_object_result_without_hidden_fallback() -> None:
    assert _assess_tool_data_health("any_tool", "invalid") == {
        "tool_name": "any_tool",
        "usable": False,
        "needs_replan": True,
        "reason": "invalid_result_contract",
    }


def test_health_marks_explicit_failure_for_replanning() -> None:
    health = _assess_tool_data_health(
        "source_a",
        {"success": False, "errors": ["upstream unavailable"]},
    )
    assert health["usable"] is False
    assert health["needs_replan"] is True
    assert health["reason"] == "tool_failed"


def test_health_marks_stale_success_for_replanning() -> None:
    health = _assess_tool_data_health(
        "source_a",
        {
            "success": True,
            "is_stale": True,
            "data_time": "2026-01-01T00:00:00+08:00",
        },
    )
    assert health == {
        "tool_name": "source_a",
        "usable": False,
        "needs_replan": True,
        "reason": "stale_data",
        "data_time": "2026-01-01T00:00:00+08:00",
    }


def test_health_accepts_success_independent_of_tool_name_or_payload_shape() -> None:
    health = _assess_tool_data_health(
        "long_tail_source",
        {
            "success": True,
            "items": [],
            "data_time": "2026-08-06T10:00:00+08:00",
        },
    )
    assert health["usable"] is True
    assert health["needs_replan"] is False
    assert health["reason"] is None
