"""NotificationService method group 4."""

from __future__ import annotations

from src.notification import (
    logging,
    datetime,
    List,
    Dict,
    Any,
    Optional,
    Tuple,
    TYPE_CHECKING,
    Enum,
    Config,
    get_config,
    ReportType,
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
    normalize_model_used,
    WechatSender,
    WECHAT_IMAGE_MAX_BYTES,
    logger,
    NotificationChannel,
    ChannelDetector,
 )

class _NotificationServiceMethods4:
    def save_report_to_file(self, content: str, filename: Optional[str] = None) -> str:
        """
        保存日报到本地文件

        Args:
            content: 日报内容
            filename: 文件名（可选，默认按日期生成）

        Returns:
            保存的文件路径
        """
        from pathlib import Path

        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"report_{date_str}.md"

        # 确保 reports 目录存在（使用项目根目录下的 reports）
        reports_dir = Path(__file__).parent.parent / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        filepath = reports_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"日报已保存到: {filepath}")
        return str(filepath)
