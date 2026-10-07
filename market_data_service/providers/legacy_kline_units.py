from typing import Any

KLINE_SOURCE_EM, KLINE_SOURCE_SINA, KLINE_SOURCE_TENCENT = (
    "eastmoney",
    "sina",
    "tencent",
)


def _normalize_record_units(
    record: dict[str, Any], source: str | None = None
) -> dict[str, Any]:
    """Normalize K-line volume to shares and turnover to percent.

    Eastmoney/Tencent expose volume in lots while Sina exposes shares.  Older
    StockDaily rows may contain either unit, so amount/volume/price is used as
    a source-independent detector before falling back to the source label.
    """

    normalized = dict(record)
    volume = normalized.get("volume")
    amount = normalized.get("amount")
    close = normalized.get("close")
    try:
        volume_value = float(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume_value = None
    detected = str(normalized.get("volume_unit") or "") == "股"
    if volume_value and amount is not None and close not in (None, 0):
        try:
            ratio = float(amount) / volume_value / float(close)
            if 30 <= ratio <= 300:
                volume_value *= 100
                detected = True
            elif 0.3 <= ratio <= 3:
                detected = True
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    source_name = str(
        source or normalized.get("data_source") or normalized.get("_source") or ""
    ).lower()
    if (
        volume_value is not None
        and not detected
        and source_name
        in {
            KLINE_SOURCE_EM,
            KLINE_SOURCE_TENCENT,
            "akshare",
            "东方财富",
            "腾讯财经",
        }
    ):
        volume_value *= 100
    if volume_value is not None:
        normalized["volume"] = volume_value

    turnover = normalized.get("turnover_rate")
    if turnover is not None:
        try:
            turnover_value = float(turnover)
            if (
                source_name in {KLINE_SOURCE_SINA, "新浪财经"}
                and 0 <= turnover_value <= 1
            ):
                turnover_value *= 100
            normalized["turnover_rate"] = turnover_value
        except (TypeError, ValueError):
            pass
    normalized["volume_unit"] = "股"
    normalized["amount_unit"] = "元"
    return normalized


def normalize_legacy_bar(row):
    source = str(row.get("data_source") or "")
    values = _normalize_record_units(
        {**row, "volume_unit": "股"} if source.endswith("_shares") else row, source
    )
    return {key: values.get(key) for key in row}
