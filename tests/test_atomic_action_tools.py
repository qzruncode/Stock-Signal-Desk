from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.tools.send_notification import send_custom_notification

def test_custom_notification_delivers_only_supplied_text() -> None:
    service = MagicMock()
    service.send.return_value = True

    with patch("src.tools.send_notification.get_notification_service", return_value=service):
        result = send_custom_notification("请注意风险", title="提示")

    service.send.assert_called_once_with("# 提示\n\n请注意风险")
    assert result["sent"] is True
