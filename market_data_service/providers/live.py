"""Canonical datasets over existing AKShare APIs and extracted source adapters."""

from datetime import timedelta
from typing import Annotated, Literal
from pydantic import Field

import pandas as pd

from market_data_service.calendar import latest_completed_trade_day, trade_dates
from market_data_service.providers.common import bare_symbol, frame_records, json_value
from market_data_service.settings import get_settings


class LiveProvider:
    def calendar(self):
        import akshare as ak

        values = ak.tool_trade_date_hist_sina()
        if values is None or values.empty:
            raise RuntimeError("上游交易日历为空")
        return sorted(
            {pd.Timestamp(value).date().isoformat() for value in values.trade_date}
        )

    def securities(self):
        from market_data_service.data_provider.fetchers.market import (
            get_all_a_stocks,
            reset_a_stock_list_fetch_state,
        )

        reset_a_stock_list_fetch_state()
        values = get_all_a_stocks()
        if not values or len(values) < 4000:
            raise RuntimeError("证券主数据覆盖不足，拒绝发布不完整股票池")
        return json_value(values)

    def kline(
        self,
        symbol: str,
        count: Annotated[int, Field(ge=1, le=5000)] = 500,
        start_date: str | None = None,
        end_date: str | None = None,
        source_id: Literal["auto", "eastmoney", "sina", "tencent"] = "auto",
        allow_fallback: bool = True,
    ):
        import akshare as ak

        code = bare_symbol(symbol)
        count = max(1, min(int(count), 5000))
        end = (
            pd.Timestamp(end_date).date() if end_date else latest_completed_trade_day()
        )
        trading = [
            day
            for day in trade_dates()
            if day <= min(end, latest_completed_trade_day())
        ]
        if not trading:
            raise ValueError("请求区间不在可验证交易日历范围内")
        end = trading[-1]
        start = (
            pd.Timestamp(start_date).date()
            if start_date
            else end - timedelta(days=int(count * 1.8) + 40)
        )
        source_id = {
            "eastmoney": "eastmoney",
            "sina": "sina",
            "tencent": "tencent",
            "auto": "eastmoney",
        }.get(source_id, source_id)
        sources = [source_id]
        if allow_fallback:
            sources += [
                value
                for value in ("eastmoney", "sina", "tencent")
                if value not in sources
            ]
        if code.startswith(("4", "8", "92")) and allow_fallback:
            sources = [value for value in sources if value != "eastmoney"]
        preferred_source = sources[0]
        prefix = (
            "bj"
            if code.startswith(("4", "8", "92"))
            else "sh"
            if code.startswith("6")
            else "sz"
        )
        failures = []
        stale_candidates = []
        for source in sources:
            try:
                from market_data_service.data_provider.rate_limiter import (
                    akshare_rate_limiter,
                )

                akshare_rate_limiter.wait()
                if source == "eastmoney":
                    frame = ak.stock_zh_a_hist(
                        symbol=code,
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                        period="daily",
                        adjust="qfq",
                        timeout=get_settings().request_timeout,
                    )
                    frame = frame.rename(
                        columns={
                            "日期": "date",
                            "开盘": "open",
                            "收盘": "close",
                            "最高": "high",
                            "最低": "low",
                            "成交量": "volume",
                            "成交额": "amount",
                            "涨跌幅": "pct_chg",
                            "换手率": "turnover_rate",
                        }
                    )
                    if "volume" in frame:
                        frame["volume"] = (
                            pd.to_numeric(frame.volume, errors="coerce") * 100
                        )
                elif source == "sina":
                    frame = ak.stock_zh_a_daily(
                        symbol=prefix + code,
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                        adjust="qfq",
                    )
                    frame = frame.rename(columns={"turnover": "turnover_rate"})
                elif source == "tencent":
                    frame = ak.stock_zh_a_hist_tx(
                        symbol=prefix + code,
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                        adjust="qfq",
                        timeout=get_settings().request_timeout,
                    )
                    frame = frame.rename(columns={"amount": "volume"})
                    if "volume" in frame:
                        frame["volume"] = (
                            pd.to_numeric(frame.volume, errors="coerce") * 100
                        )
                    frame["amount"] = None
                else:
                    raise ValueError("未知 K 线来源")
                if frame is None or frame.empty:
                    raise RuntimeError("来源没有返回日线")
                frame["date"] = pd.to_datetime(frame.date, errors="coerce").dt.date
                frame = (
                    frame[(frame.date >= start) & (frame.date <= end)]
                    .sort_values("date")
                    .drop_duplicates("date")
                )
                for key in ("open", "high", "low", "close"):
                    frame[key] = pd.to_numeric(frame[key], errors="coerce")
                frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
                frame = frame[(frame.close > 0) & (frame.high >= frame.low)]
                if "pct_chg" not in frame:
                    frame["pct_chg"] = frame.close.pct_change() * 100
                if not start_date:
                    frame = frame.tail(count)
                rows = frame_records(frame)
                if not rows:
                    raise RuntimeError("没有有效日线")
                for row in rows:
                    row.update(_source=source, volume_unit="股")
                latest = str(rows[-1]["date"])[:10]
                result = {
                    "success": True,
                    "symbol": code,
                    "data": rows,
                    "count": len(rows),
                    "source": source,
                    "requested_count": count,
                    "requested_start": start.isoformat(),
                    "requested_end": end.isoformat(),
                    "source_origin": source,
                    "data_time": latest,
                    "is_stale": latest < end.isoformat(),
                    "partial": len(rows) < count if not start_date else False,
                    "fallback_used": source != preferred_source,
                    "source_attempts": list(failures),
                    "adjust": "qfq",
                    "period": "daily",
                    "bar_complete": True,
                    "volume_unit": "股",
                    "amount_unit": "元",
                    "errors": [],
                    "warnings": [],
                }
                if result["is_stale"] and allow_fallback:
                    stale_candidates.append(result)
                    failures.append(
                        {"source": source, "error": "返回日线早于请求的完整交易日"}
                    )
                    continue
                return result
            except Exception as exc:
                failures.append({"source": source, "error": str(exc)[:250]})
        if stale_candidates:
            result = max(stale_candidates, key=lambda item: item["data_time"])
            return {
                **result,
                "source_attempts": failures,
                "warnings": [
                    "各来源均未提供最近完整交易日的数据，当前快照不作为最新数据使用"
                ],
            }
        raise RuntimeError("日线来源均不可用: " + str(failures))

    def financials(self, symbol):
        from market_data_service.providers.financial_sync import (
            _fallback_update_from_financials,
        )

        values = _fallback_update_from_financials(symbol)
        if not values or not values.get("report_date"):
            raise RuntimeError("未获取到可验证报告期的财务数据")
        return {
            "success": True,
            "symbol": symbol,
            "data": json_value(values),
            "source": "THS/Eastmoney",
            "data_time": values["report_date"],
            "errors": [],
        }

    def financials_bulk(self, codes):
        from market_data_service.providers.financial_sync import (
            _report_periods,
            _collect_period_rows,
            _row_to_update,
        )

        periods = _report_periods()
        selected, rows_by_period, _, _ = _collect_period_rows(set(codes), periods)
        return {
            code: {
                "success": True,
                "symbol": code,
                "data": json_value(_row_to_update(code, period, row, rows_by_period)),
                "source": "Eastmoney",
                "data_time": period,
                "errors": [],
            }
            for code, (period, row) in selected.items()
        }

    def quotes(
        self,
        symbol: str,
        source_id: Literal[
            "auto", "tencent", "sina", "eastmoney_push", "xueqiu"
        ] = "auto",
    ):
        from market_data_service.data_provider.fetchers import realtime

        functions = {
            "tencent": realtime._get_stock_realtime_quote_tencent,
            "sina": realtime._get_stock_realtime_quote_sina,
            "eastmoney_push": realtime._get_stock_realtime_quote_em_push,
            "xueqiu": realtime._get_stock_realtime_quote_xueqiu,
        }
        requested = (
            [source_id]
            if source_id != "auto"
            else ["tencent", "sina", "eastmoney_push"]
        )
        failures = []
        for source in requested:
            try:
                quote = functions[source](bare_symbol(symbol))
                if quote is None or not quote.has_basic_data():
                    raise ValueError("无有效报价")
                item = json_value(quote.to_dict())
                from types import SimpleNamespace
                from market_data_service.control_models import utcnow
                from market_data_service.freshness import state_status

                state = SimpleNamespace(
                    dataset="quotes",
                    status="ready",
                    last_success_at=utcnow(),
                    data_time=item.get("trade_time"),
                    error=None,
                )
                if state_status(state, SimpleNamespace(max_age_seconds=120)) != "fresh":
                    raise ValueError("报价时间未达到当前交易时段要求")
                return {
                    "success": True,
                    "symbol": symbol,
                    "items": [item],
                    "total": 1,
                    "source": source,
                    "data_time": item.get("trade_time"),
                    "errors": [],
                    "warnings": failures,
                    "fallback_used": source_id == "auto" and source != requested[0],
                }
            except Exception as exc:
                failures.append(f"{source}: {exc}")
        raise RuntimeError("行情暂不可用: " + "; ".join(failures))

    def news(self, symbol):
        from market_data_service.providers.news import read_company_news_akshare

        return read_company_news_akshare(symbol, use_cache=False)

    def announcements(self, symbol):
        from market_data_service.providers.announcements import (
            read_company_announcements_akshare,
        )

        return read_company_announcements_akshare(symbol, use_cache=False)


class FixtureProvider:
    """Explicit development/test upstream; never a fallback for failed live requests."""

    def operation(self, operation, arguments):
        import json
        from pathlib import Path

        fixture = json.loads(Path(get_settings().fixture_path).read_text())
        value = (fixture.get("operations") or {}).get(operation)
        if value is None:
            raise ValueError(
                f"Fixture has no operation {operation}; live sources are disabled"
            )
        if value.get("error"):
            raise RuntimeError(value["error"])
        return value

    def __getattr__(self, dataset):
        def read(symbol=None, **arguments):
            import json
            import time
            from pathlib import Path

            fixture = json.loads(Path(get_settings().fixture_path).read_text())
            value = fixture.get(dataset)
            if dataset not in {"calendar", "securities"}:
                value = (value or {}).get(symbol)
            if isinstance(value, dict) and value.get("delay_seconds"):
                time.sleep(min(10, value["delay_seconds"]))
            if isinstance(value, dict) and value.get("error"):
                raise RuntimeError(value["error"])
            if value is None:
                raise RuntimeError(f"Fixture has no {dataset}/{symbol}")
            return value

        return read


def get_provider():
    return FixtureProvider() if get_settings().provider == "fixture" else LiveProvider()
