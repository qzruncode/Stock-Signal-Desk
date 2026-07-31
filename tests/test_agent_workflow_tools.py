from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.tools.delete_analysis_history import delete_analysis_history
from src.tools.get_notification_status import get_notification_status
from src.tools.manage_analysis_schedule import manage_analysis_schedule
from src.tools.process_runner import STATEFUL_TOOL_NAMES
from src.tools.registry import ToolRegistry
from src.tools.run_stock_analysis import run_stock_analysis
from src.tools.search_analysis_history import search_analysis_history
from src.tools.send_notification import send_notification


WORKFLOW_NAMES = {
    "run_stock_analysis",
    "get_analysis_status",
    "search_analysis_history",
    "read_analysis_report",
    "delete_analysis_history",
    "manage_analysis_templates",
    "run_batch_analysis",
    "manage_batch_run",
    "manage_analysis_schedule",
    "get_notification_status",
    "send_notification",
}


def test_registry_exposes_dashboard_workflows_as_same_named_tools() -> None:
    assert WORKFLOW_NAMES <= set(ToolRegistry().get_tool_names())
    assert {
        "run_stock_analysis",
        "get_analysis_status",
        "run_batch_analysis",
        "manage_batch_run",
        "manage_analysis_schedule",
    } <= STATEFUL_TOOL_NAMES


def test_run_stock_analysis_submits_persistent_queue_task_without_implicit_notification() -> None:
    task = SimpleNamespace(
        task_id="task-1",
        stock_code="002015",
        status=SimpleNamespace(value="pending"),
        progress=0,
        message="任务已加入队列",
    )
    queue = SimpleNamespace(submit_tasks_batch=lambda *args, **kwargs: ([task], []))
    with (
        patch("api.v1.endpoints.analysis.trigger._resolve_and_normalize_input", return_value="002015"),
        patch(
            "src.tools.run_stock_analysis.get_task_queue",
            return_value=queue,
        ),
    ):
        result = run_stock_analysis("新强联")

    assert result["accepted"] is True
    assert result["task_id"] == "task-1"
    assert result["notify_on_complete"] is False


def test_history_search_preserves_pagination_contract() -> None:
    payload = {"total": 3, "items": [{"id": 2, "stock_code": "002015"}]}
    with patch("src.tools.search_analysis_history.HistoryService.get_history_list", return_value=payload):
        result = search_analysis_history(symbol="002015", page=1, limit=1)
    assert result["total"] == 3
    assert result["returned_count"] == 1
    assert result["has_more"] is True


def test_destructive_and_external_actions_require_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="confirmed=true"):
        delete_analysis_history("1,2", confirmed=False)
    with pytest.raises(ValueError, match="confirmed=true"):
        send_notification("custom", message="测试", confirmed=False)
    with pytest.raises(ValueError, match="confirmed=true"):
        manage_analysis_schedule("update", enabled=True, times="15:10", prompt_template_id="tpl", confirmed=False)


def test_notification_status_is_redacted() -> None:
    config = {
        "items": [
            {
                "key": "WECHAT_WEBHOOK_URL",
                "value": "******",
                "raw_value_exists": True,
                "is_masked": True,
                "schema": {"category": "notification"},
            }
        ],
    }
    with patch("src.tools.get_notification_status.SystemConfigService.get_config", return_value=config):
        result = get_notification_status()
    assert result["configured_count"] == 1
    assert "webhook" not in str(result).lower()
