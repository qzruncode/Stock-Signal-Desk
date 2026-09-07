"""Existing authoritative sector catalogs and exact-name quote joins."""

from __future__ import annotations
import logging
from typing import Optional, Literal

logger = logging.getLogger(__name__)


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _normalize_name(name: str) -> str:
    """Normalize provider formatting for exact identifier joins."""
    return "".join(str(name).split()).casefold()


def _fetch_sector_sina(indicator: str) -> list[dict]:
    """Fetch industry/concept boards from Sina as an independent fallback."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []
    try:
        df = ak.stock_sector_spot(indicator=indicator)
        if df is None or df.empty:
            return result
        for _, row in df.iterrows():
            name = str(row.get("板块", "")).strip()
            if not name:
                continue
            result.append(
                {
                    "name": name,
                    "code": str(row.get("label", "")).strip(),
                    "change_pct": _safe_float(row.get("涨跌幅")),
                    "lead_stock": str(row.get("股票名称", "")).strip(),
                    "lead_stock_price": _safe_float(row.get("个股-当前价")),
                    "lead_stock_change_pct": _safe_float(row.get("个股-涨跌幅")),
                    "up_count": None,
                    "down_count": None,
                    "company_count": _safe_int(row.get("公司家数")),
                    "total_volume": _safe_float(row.get("总成交量")),
                    "total_amount": _safe_float(row.get("总成交额")),
                    "net_flow": None,
                    "data_source": "新浪",
                }
            )
        result.sort(
            key=lambda item: (
                item.get("change_pct") is not None,
                item.get("change_pct") or 0,
            ),
            reverse=True,
        )
        logger.info(
            "[Sectors] sina %s: %s 条, %.1fs", indicator, len(result), _time.time() - t0
        )
    except Exception as exc:
        logger.warning("[Sectors] sina %s 获取失败: %s", indicator, exc)
    return result


def _fetch_industry() -> list[dict]:
    """Fetch industry boards from Eastmoney, then Sina when EM is unavailable."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []

    try:
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                item = {
                    "name": str(row.get("板块名称", "")),
                    "code": str(row.get("板块代码", "")),
                    "change_pct": _safe_float(row.get("涨跌幅")),
                    "lead_stock": str(row.get("领涨股票", "")),
                    "lead_stock_price": None,
                    "lead_stock_change_pct": _safe_float(row.get("领涨股票-涨跌幅")),
                    "up_count": _safe_int(row.get("上涨家数")),
                    "down_count": _safe_int(row.get("下跌家数")),
                    "total_amount": None,
                    "net_flow": None,
                    "data_source": "东方财富",
                }
                result.append(item)
        logger.info(f"[Sectors] industry: {len(result)} 条, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.error(f"[Sectors] industry 获取失败: {e}")

    return result or _fetch_sector_sina("行业")


def _fetch_concept() -> list[dict]:
    """Fetch the provider's authoritative concept catalog and exact-join quotes."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []

    try:
        names_df = ak.stock_board_concept_name_em()
        if names_df is None or names_df.empty:
            logger.warning("[Sectors] concept catalog returned empty")
            return _fetch_sector_sina("概念")

        change_by_name: dict[str, dict] = {}
        try:
            change_df = ak.stock_board_change_em()
            if change_df is not None and not change_df.empty:
                for _, change_row in change_df.iterrows():
                    change_name = str(change_row.get("板块名称") or "").strip()
                    if change_name:
                        change_by_name[_normalize_name(change_name)] = (
                            change_row.to_dict()
                        )
        except Exception as exc:
            logger.warning(
                "[Sectors] concept quote enrichment failed: %s",
                exc,
            )

        for _, catalog_row in names_df.iterrows():
            name = str(catalog_row.get("板块名称") or "").strip()
            code = str(catalog_row.get("板块代码") or "").strip()
            if not name:
                continue
            quote_row = change_by_name.get(_normalize_name(name), {})
            change_pct = quote_row.get(
                "涨跌幅",
                catalog_row.get("涨跌幅"),
            )
            if change_pct == "-" or change_pct is None:
                change_pct = None
            else:
                change_pct = _safe_float(change_pct)
            result.append(
                {
                    "name": name,
                    "code": code,
                    "change_pct": change_pct,
                    "net_flow": _safe_float(
                        quote_row.get(
                            "主力净流入",
                            catalog_row.get("主力净流入"),
                        )
                    ),
                    "lead_stock": "",
                    "up_count": None,
                    "down_count": None,
                    "data_source": "东方财富",
                }
            )

        result.sort(
            key=lambda x: (x["change_pct"] is not None, x["change_pct"] or 0),
            reverse=True,
        )

        logger.info(f"[Sectors] concept: {len(result)} 条, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.error(f"[Sectors] concept 获取失败: {e}")

    return result or _fetch_sector_sina("概念")


def _sector_source(items: list[dict]) -> str:
    sources = {
        str(item.get("data_source") or "").strip()
        for item in items
        if isinstance(item, dict) and item.get("data_source")
    }
    return "+".join(sorted(sources)) if sources else "none"


def get_sector_list(
    type: Literal["industry", "concept"] = "industry", force: bool = False
):
    items = _fetch_industry() if type == "industry" else _fetch_concept()
    return {
        "success": bool(items),
        "type": type,
        "items": items,
        "source": _sector_source(items),
        "data_time": None,
        "data_time_applicable": False,
        "data_time_note": "源未返回统一行情时点，检查时间仅表示本次查询时间。",
        "fallback_used": _sector_source(items) == "新浪",
        "errors": [] if items else ["板块来源暂不可用"],
    }
