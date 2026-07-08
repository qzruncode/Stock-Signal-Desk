# -*- coding: utf-8 -*-
"""Stock business analysis endpoint — routing and orchestration only."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import AsyncGenerator

from fastapi import Query
from fastapi.responses import StreamingResponse

from api.v1.endpoints.stock_info import router
from api.v1.endpoints.stock_info.profile import _normalize_symbol
from api.v1.endpoints.stock_info._cache import _business_cache_get, _business_cache_put
from api.v1.endpoints.stock_info._data import (
    _fetch_business_intro,
    _fetch_business_composition,
    _fetch_profit_forecast,
    _fetch_financial_summary,
    _fetch_recent_events,
)
from api.v1.endpoints.stock_info._llm_parse import (
    _generate_llm_business_analysis,
)
from api.v1.endpoints.stock_info._sse import _format_business_sse_event

logger = logging.getLogger(__name__)


@router.get("/business", summary="获取个股业务分析数据")
def get_stock_business(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """Get stock business analysis with LLM insights. Returns cached data if available."""
    normalized = _normalize_symbol(symbol)

    if not force:
        cached = _business_cache_get(normalized)
        if cached:
            return cached

    intro = _fetch_business_intro(normalized)
    composition = _fetch_business_composition(normalized)
    profit_forecast = _fetch_profit_forecast(normalized)
    financial_summary = _fetch_financial_summary(normalized)
    events = _fetch_recent_events(normalized)

    result = _generate_llm_business_analysis(
        symbol=normalized,
        intro=intro,
        composition=composition,
        profit_forecast=profit_forecast,
        financial_summary=financial_summary,
        events=events,
    )

    _business_cache_put(normalized, result)
    return result


@router.get("/business/stream", summary="获取个股业务分析数据 (SSE 流)")
async def get_stock_business_stream(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
):
    """Stream stock business analysis via SSE with real-time progress."""
    normalized = _normalize_symbol(symbol)

    async def event_generator() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        cancel_event = threading.Event()

        def _worker():
            """Run analysis in a thread, enqueueing events."""
            try:
                queue.put_nowait(("progress", {"stage": "fetching", "message": "正在获取公司业务数据..."}))

                intro = _fetch_business_intro(normalized)
                composition = _fetch_business_composition(normalized)
                profit_forecast = _fetch_profit_forecast(normalized)
                financial_summary = _fetch_financial_summary(normalized)
                events = _fetch_recent_events(normalized)

                queue.put_nowait(("progress", {"stage": "data_ready", "message": "数据获取完成，开始 LLM 分析..."}))

                def _on_text(text: str):
                    queue.put_nowait(("business_text", {"delta": text}))

                def _on_env_text(text: str):
                    queue.put_nowait(("environment_text", {"delta": text}))

                def _on_track_text(text: str):
                    queue.put_nowait(("track_quality_text", {"delta": text}))

                def _on_catalyst_text(text: str):
                    queue.put_nowait(("catalyst_text", {"delta": text}))

                result = _generate_llm_business_analysis(
                    symbol=normalized,
                    intro=intro,
                    composition=composition,
                    profit_forecast=profit_forecast,
                    financial_summary=financial_summary,
                    events=events,
                    on_text=_on_text,
                    on_env_text=_on_env_text,
                    on_track_text=_on_track_text,
                    on_catalyst_text=_on_catalyst_text,
                )

                if cancel_event.is_set():
                    return

                _business_cache_put(normalized, result)

                queue.put_nowait(("complete", {
                    "symbol": normalized,
                    "intro": intro,
                    "environment_analysis": result.get("environment_analysis"),
                    "track_quality_analysis": result.get("track_quality_analysis"),
                    "catalyst_analysis": result.get("catalyst_analysis"),
                    "business_analysis": result.get("business_analysis"),
                    "generated_at": result.get("generated_at"),
                }))
            except Exception as e:
                logger.exception("[StockBusiness] Worker failed")
                queue.put_nowait(("error", {"message": str(e)}))
            finally:
                queue.put_nowait((None, None))

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        try:
            while True:
                event_type, data = await queue.get()
                if event_type is None:
                    break
                yield _format_business_sse_event(event_type, data)
        except asyncio.CancelledError:
            logger.debug("[StockBusiness] Client disconnected, cancelling worker")
            cancel_event.set()
            logger.info("[StockBusiness] Worker cancel_event set after disconnect")
            raise
        except Exception:
            logger.exception("[StockBusiness] Unexpected error in event generator, cancelling worker")
            cancel_event.set()
            logger.info("[StockBusiness] Worker cancel_event set after generator error")
            raise
        finally:
            if not cancel_event.is_set():
                cancel_event.set()
                logger.info("[StockBusiness] Worker cancel_event set in finally")

    return StreamingResponse(event_generator(), media_type="text/event-stream")