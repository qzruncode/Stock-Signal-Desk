# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 通知层
===================================

职责：
1. 汇总分析结果生成日报
2. 支持 Markdown 格式输出
3. 企业微信 Webhook 推送
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING
from enum import Enum

from src.config import Config, get_config
from src.enums import ReportType
from src.report_language import (
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.utils.data_processing import normalize_model_used
from src.notification_sender import WechatSender, WECHAT_IMAGE_MAX_BYTES

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult


class NotificationChannel(Enum):
    """通知渠道类型"""

    WECHAT = "wechat"  # 企业微信


class ChannelDetector:
    """渠道检测器"""

    @staticmethod
    def get_channel_name(channel: NotificationChannel) -> str:
        names = {
            NotificationChannel.WECHAT: "企业微信",
        }
        return names.get(channel, "未知渠道")


from ._notification_methods1 import _NotificationServiceMethods1
from ._notification_methods2 import _NotificationServiceMethods2
from ._notification_methods3 import _NotificationServiceMethods3
from ._notification_methods4 import _NotificationServiceMethods4
class NotificationService(_NotificationServiceMethods1, _NotificationServiceMethods2, _NotificationServiceMethods3, _NotificationServiceMethods4):
        """
        通知服务

        职责：
        1. 生成 Markdown 格式的分析日报
        2. 向企业微信推送消息
        """
        _SOURCE_DISPLAY_NAMES = {
            "akshare_em": {"zh": "东方财富", "en": "Eastmoney"},
            "akshare_sina": {"zh": "新浪财经", "en": "Sina Finance"},
            "akshare_qq": {"zh": "腾讯财经", "en": "Tencent Finance"},
            "tencent": {"zh": "腾讯财经", "en": "Tencent Finance"},
            "sina": {"zh": "新浪财经", "en": "Sina Finance"},
            "fallback": {"zh": "降级兜底", "en": "Fallback"},
        }


def _bind_mixin_member(_member):
    import functools
    import types

    if isinstance(_member, staticmethod):
        return staticmethod(_bind_mixin_member(_member.__func__))
    if isinstance(_member, classmethod):
        return classmethod(_bind_mixin_member(_member.__func__))
    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _mixin in (_NotificationServiceMethods1, _NotificationServiceMethods2, _NotificationServiceMethods3, _NotificationServiceMethods4):
    for _name, _member in _mixin.__dict__.items():
        if _name not in {"__dict__", "__weakref__"}:
            setattr(NotificationService, _name, _bind_mixin_member(_member))


class NotificationBuilder:
    """
    通知消息构建器

    提供便捷的消息构建方法
    """

    @staticmethod
    def build_simple_alert(title: str, content: str, alert_type: str = "info") -> str:
        """
        构建简单的提醒消息

        Args:
            title: 标题
            content: 内容
            alert_type: 类型（info, warning, error, success）
        """
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅",
        }
        emoji = emoji_map.get(alert_type, "📢")

        return f"{emoji} **{title}**\n\n{content}"

    @staticmethod
    def build_stock_summary(results: List[AnalysisResult]) -> str:
        """
        构建股票摘要（简短版）

        适用于快速通知
        """
        report_language = normalize_report_language(
            next(
                (
                    getattr(result, "report_language", None)
                    for result in results
                    if getattr(result, "report_language", None)
                ),
                None,
            )
        )
        labels = get_report_labels(report_language)
        lines = [f"📊 **{labels['summary_heading']}**", ""]

        for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
            _, emoji, _ = get_signal_level(r.operation_advice, r.sentiment_score, report_language)
            name = get_localized_stock_name(r.name, r.code, report_language)
            lines.append(
                f"{emoji} {name}({r.code}): {localize_operation_advice(r.operation_advice, report_language)} | "
                f"{labels['score_label']} {r.sentiment_score}"
            )

        return "\n".join(lines)


# 便捷函数
def get_notification_service() -> NotificationService:
    """获取通知服务实例"""
    return NotificationService()


def send_daily_report(results: List[AnalysisResult]) -> bool:
    """
    发送每日报告的快捷方式

    自动识别渠道并推送
    """
    service = get_notification_service()

    # 生成报告
    report = service.generate_daily_report(results)

    # 保存到本地
    service.save_report_to_file(report)

    # 推送到配置的渠道（自动识别）
    return service.send(report)
