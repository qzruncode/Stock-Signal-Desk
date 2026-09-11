# -*- coding: utf-8 -*-
"""Enterprise WeChat notification service."""

from __future__ import annotations

import logging
from typing import Optional

from src.config import Config, get_config
from src.notification_sender import WechatSender

logger = logging.getLogger(__name__)


class NotificationService(WechatSender):
    """Application wrapper around the only supported notification sender."""

    def __init__(self, config: Optional[Config] = None):
        super().__init__(config or get_config())

    def is_available(self) -> bool:
        """Return whether an enterprise WeChat webhook is configured."""
        return bool(self._wechat_url)

    def send(self, content: str) -> bool:
        """Send Markdown content through enterprise WeChat."""
        try:
            result = self.send_to_wechat(content)
            if result:
                logger.info("企业微信通知发送成功")
            else:
                logger.error("企业微信通知发送失败")
            return result
        except Exception as exc:
            logger.error("企业微信发送异常: %s", exc)
            return False


def get_notification_service() -> NotificationService:
    """Return a configured enterprise WeChat notification service."""
    return NotificationService()


__all__ = ["NotificationService", "get_notification_service"]
