"""HistoryService method group 3."""

from __future__ import annotations

from src.services.history_service import (
    json,
    logging,
    date,
    datetime,
    timedelta,
    Optional,
    Dict,
    Any,
    List,
    Tuple,
    TYPE_CHECKING,
    get_config,
    resolve_news_window_days,
    get_bias_status_emoji,
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    localize_bias_status,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
    DatabaseManager,
    normalize_model_used,
    parse_json_field,
    logger,
    MarkdownReportGenerationError,
 )

class _HistoryServiceMethods3:
    @staticmethod
    def _safe_format_number(value: Any, fmt: str = ".2f") -> str:
        """
        Safely format a numeric value that may be a string.

        Args:
            value: The value to format (may be int, float, or string like "12.34" or "N/A")
            fmt: Format string (default: ".2f")

        Returns:
            Formatted string or original string if not a valid number
        """
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            return f"{value:{fmt}}"
        if isinstance(value, str):
            value = value.strip()
            if not value or value in ("N/A", "-", "—", "None"):
                return "N/A"
            try:
                return f"{float(value):{fmt}}"
            except (ValueError, TypeError):
                return value
        return str(value)
    @staticmethod
    def _append_market_snapshot_to_report(
        lines: List[str],
        result: AnalysisResult,
        labels: Dict[str, str],
    ) -> None:
        """Append market snapshot data to report lines."""
        snapshot = getattr(result, "market_snapshot", None)
        if not snapshot:
            return

        lines.extend(
            [
                f"### 📈 {labels['market_snapshot_heading']}",
                "",
                f"| {labels['price_metrics_label']} | {labels['current_price_label']} |",
                "|------|------|",
            ]
        )

        # Price info
        current_price = snapshot.get("price") or snapshot.get("current_price") or result.current_price
        change_pct = snapshot.get("change_pct") or snapshot.get("pct_chg") or result.change_pct
        if current_price is not None:
            current_str = HistoryService._safe_format_number(current_price, ".2f")
            if change_pct is not None:
                if isinstance(change_pct, str) and change_pct.strip().endswith("%"):
                    change_str = change_pct.strip()
                else:
                    change_str = f"{HistoryService._safe_format_number(change_pct, '+.2f')}%"
            else:
                change_str = "--"
            lines.append(f"| {labels['current_price_label']} | **{current_str}** ({change_str}) |")

        # Other metrics
        metrics = [
            (labels["open_label"], "open", ".2f"),
            (labels["high_label"], "high", ".2f"),
            (labels["low_label"], "low", ".2f"),
            (labels["volume_label"], "volume", ",.0f"),
            (labels["amount_label"], "amount", ",.0f"),
        ]
        for label, key, fmt in metrics:
            value = snapshot.get(key)
            if value is not None:
                formatted = HistoryService._safe_format_number(value, fmt)
                lines.append(f"| {label} | {formatted} |")

        lines.extend(["", "---", ""])
