from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.manage_analysis_schedule import update_analysis_schedule
from src.tools.manage_analysis_templates import (
    create_analysis_template,
    delete_analysis_template,
    set_default_analysis_template,
)
from src.tools.send_notification import send_custom_notification


def test_create_template_does_not_also_change_the_default_template() -> None:
    store = MagicMock()
    store.create.return_value = {"id": "template-1", "name": "研究"}

    with patch(
        "src.tools.manage_analysis_templates.get_prompt_template_store",
        return_value=store,
    ):
        result = create_analysis_template("研究", "只回答当前问题")

    store.create.assert_called_once_with("研究", "只回答当前问题", is_default=False)
    store.load_all.assert_not_called()
    store.update.assert_not_called()
    assert result["template"]["id"] == "template-1"


def test_setting_default_uses_the_store_transaction_not_a_tool_level_loop() -> None:
    store = MagicMock()
    store.set_default.return_value = {"id": "template-2", "is_default": True}

    with patch(
        "src.tools.manage_analysis_templates.get_prompt_template_store",
        return_value=store,
    ):
        result = set_default_analysis_template("template-2")

    store.set_default.assert_called_once_with("template-2")
    store.load_all.assert_not_called()
    store.update.assert_not_called()
    assert result["template"]["is_default"] is True


def test_deleting_template_does_not_preload_or_rewrite_other_templates() -> None:
    store = MagicMock()
    store.delete_non_default.return_value = True

    with patch(
        "src.tools.manage_analysis_templates.get_prompt_template_store",
        return_value=store,
    ):
        result = delete_analysis_template("template-3")

    store.delete_non_default.assert_called_once_with("template-3")
    store.get.assert_not_called()
    store.load_all.assert_not_called()
    assert result["deleted"] is True


def test_custom_notification_delivers_only_supplied_text() -> None:
    service = MagicMock()
    service.send.return_value = True

    with (
        patch(
            "src.tools.send_notification.get_notification_service",
            return_value=service,
        ),
        patch(
            "src.tools.send_notification.HistoryService",
            side_effect=AssertionError("notification must not read an analysis report"),
        ),
    ):
        result = send_custom_notification("请注意风险", title="提示")

    service.send.assert_called_once_with("# 提示\n\n请注意风险")
    assert result["sent"] is True


def test_schedule_update_refuses_hidden_current_template_lookup() -> None:
    with pytest.raises(ValueError, match="prompt_template_id"):
        update_analysis_schedule(enabled=True, times="09:00", prompt_template_id="")
