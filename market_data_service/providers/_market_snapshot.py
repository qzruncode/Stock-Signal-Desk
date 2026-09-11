# -*- coding: utf-8 -*-
"""Source readers for atomic market observations.

The snapshot deliberately keeps each metric tied to a source with the same
business meaning:

* Shanghai/Shenzhen A-share breadth: Legu's market-activity table.  AKShare's
  wrapper for this page is currently broken, so the public page is parsed
  directly and validated.
* Major indices and Shanghai/Shenzhen turnover: Sina real-time index quotes.
* Limit-up, limit-down and failed-limit pools: the matching AKShare Eastmoney
  pool APIs for the expected trading day.
The Agent consumes these readers independently. They do not compose a market
status, breadth conclusion, or other derived judgement.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Any

import httpx

_LEGU_URL = "https://legulegu.com/stockdata/market-activity"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
_INDEX_CODES = {
    "shanghai_composite": "sh000001",
    "shenzhen_component": "sz399001",
    "chinext": "sz399006",
    "star50": "sh000688",
}


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _iso_local(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _parse_legu_activity(html: str) -> dict[str, Any]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("市场活跃度页面没有统计表")

    values: dict[str, int] = {}
    for row in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        for index in range(0, len(cells) - 1, 2):
            number = re.search(r"-?\d+", cells[index + 1].replace(",", ""))
            if number:
                values[cells[index].strip().lower()] = int(number.group())

    required = {"上涨", "下跌", "平盘"}
    if not required.issubset(values):
        raise RuntimeError("市场活跃度页面缺少上涨、下跌或平盘家数")
    total = values["上涨"] + values["下跌"] + values["平盘"] + values.get("停牌", 0)
    if total < 3000 or total > 7000:
        raise RuntimeError(f"市场活跃度股票总数异常: {total}")

    description = soup.find("meta", attrs={"name": "description"})
    description_text = str(description.get("content") or "") if description else ""
    activity_text = " ".join(
        node.get_text(" ", strip=True) for node in soup.select(".market-activity")
    )
    combined = f"{activity_text} {description_text}"
    time_match = re.search(
        r"(20\d{2}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?", combined
    )
    data_time = None
    if time_match:
        clock = time_match.group(2) or "15:00:00"
        data_time = _iso_local(datetime.fromisoformat(f"{time_match.group(1)}T{clock}"))
    activity_match = re.search(r"活跃度\s*([\d.]+)%", activity_text)

    return {
        "up_count": values["上涨"],
        "down_count": values["下跌"],
        "flat_count": values["平盘"],
        "halt_count": values.get("停牌"),
        "legu_limit_up_count": values.get("涨停"),
        "legu_limit_down_count": values.get("跌停"),
        "real_limit_up_count": values.get("真实涨停"),
        "real_limit_down_count": values.get("真实跌停"),
        "market_activity_pct": _safe_float(activity_match.group(1))
        if activity_match
        else None,
        "data_time": data_time,
        "source": "乐咕乐股市场活跃度",
        "breadth_scope": "沪深A股",
    }


def _fetch_legu_activity() -> dict[str, Any]:
    response = httpx.get(
        _LEGU_URL,
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=httpx.Timeout(10.0, connect=3.0),
        follow_redirects=True,
    )
    response.raise_for_status()
    return _parse_legu_activity(response.text)


def _fetch_sina_a_breadth() -> dict[str, Any]:
    """Slow but semantically correct breadth fallback for Legu failure."""

    import akshare as ak

    frame = ak.stock_zh_a_spot()
    if frame is None or frame.empty or "涨跌幅" not in frame.columns:
        raise RuntimeError("新浪全A实时行情为空")
    change = frame["涨跌幅"]
    active = change.notna()
    up = int((change[active] > 0).sum())
    down = int((change[active] < 0).sum())
    flat = int((change[active] == 0).sum())
    if up + down + flat < 3000:
        raise RuntimeError("新浪全A实时行情覆盖股票过少")
    timestamps = [str(value) for value in frame.get("时间戳", []) if value]
    now = datetime.now().astimezone()
    data_time = None
    # 新浪的全市场表只给出时分秒，缺少来源日期。绝不能把本服务的抓取时刻当
    # 成行情时间；若拼接当前日期，仅能作为明确标注的推定时间。
    data_time_inferred = False
    if timestamps and re.fullmatch(r"\d{2}:\d{2}:\d{2}", max(timestamps)):
        data_time = _iso_local(
            datetime.combine(now.date(), time.fromisoformat(max(timestamps)))
        )
        data_time_inferred = True
    return {
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "halt_count": None,
        "data_time": data_time,
        "data_time_inferred": data_time_inferred,
        "source": "新浪全A实时行情",
        "breadth_scope": "A股（含北交所）",
    }


def _fetch_index_spot() -> dict[str, Any]:
    import akshare as ak

    frame = ak.stock_zh_index_spot_sina()
    if frame is None or frame.empty or "代码" not in frame.columns:
        raise RuntimeError("新浪指数实时行情为空")
    rows = {str(row.get("代码")): row for _, row in frame.iterrows()}
    indices: dict[str, dict[str, Any]] = {}
    for key, code in _INDEX_CODES.items():
        row = rows.get(code)
        if row is None:
            continue
        indices[key] = {
            "code": code,
            "name": str(row.get("名称") or ""),
            "price": _safe_float(row.get("最新价")),
            "change": _safe_float(row.get("涨跌额")),
            "change_pct": _safe_float(row.get("涨跌幅")),
            "previous_close": _safe_float(row.get("昨收")),
            "open": _safe_float(row.get("今开")),
            "high": _safe_float(row.get("最高")),
            "low": _safe_float(row.get("最低")),
            "volume": _safe_float(row.get("成交量")),
            "amount": _safe_float(row.get("成交额")),
        }
    if "shanghai_composite" not in indices:
        raise RuntimeError("新浪指数实时行情缺少上证指数")

    sh_amount = (indices.get("shanghai_composite") or {}).get("amount")
    sz_amount = (indices.get("shenzhen_component") or {}).get("amount")
    total_amount = None
    if sh_amount is not None and sz_amount is not None:
        total_amount = round((sh_amount + sz_amount) / 1e8, 2)
    return {
        "indices": indices,
        "total_amount": total_amount,
        "total_amount_unit": "亿元",
        "turnover_scope": "沪深市场",
        "source": "新浪实时指数行情",
    }


def _fetch_pool(function_name: str, trade_day: str) -> dict[str, Any]:
    import akshare as ak

    function = getattr(ak, function_name)
    frame = function(date=trade_day)
    return {"count": 0 if frame is None else len(frame), "source": function_name}
