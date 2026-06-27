# -*- coding: utf-8 -*-
"""Stock business analysis endpoint — LLM-powered business, environment, and quality analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse

from api.v1.endpoints.stock_info import router
from api.v1.endpoints.stock_info.profile import _safe_float, _normalize_symbol

logger = logging.getLogger(__name__)

BUSINESS_CACHE_KEY = "stock_business:v2"


def _business_cache_key(symbol: str) -> str:
    return f"{BUSINESS_CACHE_KEY}:{_normalize_symbol(symbol)}:{datetime.now().strftime('%Y%m%d')}"


def _business_cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_business_cache_key(symbol))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.warning("[StockBusiness] _business_cache_get failed for symbol=%s", symbol, exc_info=True)
    return None


def _business_cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _business_cache_key(symbol), json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.warning("[StockBusiness] _business_cache_put failed for symbol=%s", symbol, exc_info=True)


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    elif isinstance(obj, float) and (obj != obj or obj == float('inf') or obj == float('-inf')):
        return None
    return obj


def _fetch_business_intro(symbol: str) -> dict:
    import akshare as ak
    try:
        profile = ak.stock_profile_cninfo(symbol=symbol)
        if profile is not None and not profile.empty:
            row = profile.iloc[0].to_dict() if len(profile) == 1 else profile.iloc[-1].to_dict()
            return _sanitize(row)
    except Exception as exc:
        logger.warning("[StockBusiness] intro fetch failed for %s: %s", symbol, exc)
    return {}


def _fetch_business_composition(symbol: str) -> list[dict]:
    import akshare as ak
    try:
        df = ak.stock_business_analysis(symbol=symbol)
        if df is not None and not df.empty:
            return _sanitize(df.to_dict('records'))
    except Exception as exc:
        logger.warning("[StockBusiness] composition fetch failed for %s: %s", symbol, exc)
    return []


def _fetch_profit_forecast(symbol: str) -> list[dict]:
    import akshare as ak
    try:
        df = ak.stock_profit_forecast(symbol=symbol)
        if df is not None and not df.empty:
            return _sanitize(df.to_dict('records'))
    except Exception as exc:
        logger.warning("[StockBusiness] profit forecast failed for %s: %s", symbol, exc)
    return []


def _fetch_financial_summary(symbol: str) -> dict:
    import akshare as ak
    try:
        df = ak.stock_financial_abstract(symbol=symbol)
        if df is not None and not df.empty:
            return _sanitize(df.to_dict('records'))
    except Exception as exc:
        logger.warning("[StockBusiness] financial summary failed for %s: %s", symbol, exc)
    return {}


def _fetch_recent_events(symbol: str) -> dict:
    news = []
    announcements = []
    try:
        import akshare as ak
        # Get news
        try:
            df_news = ak.stock_news(symbol=symbol)
            if df_news is not None and not df_news.empty:
                news = _sanitize(df_news.to_dict('records'))
        except Exception:
            logger.warning("[StockBusiness] stock_news failed for symbol=%s", symbol, exc_info=True)
        # Get announcements
        try:
            df_ann = ak.stock_notice_report(symbol=symbol)
            if df_ann is not None and not df_ann.empty:
                announcements = _sanitize(df_ann.to_dict('records'))
        except Exception:
            logger.warning("[StockBusiness] stock_notice_report failed for symbol=%s", symbol, exc_info=True)
    except Exception as exc:
        logger.warning("[StockBusiness] news/announcements failed for %s: %s", symbol, exc)
    return {"news": news, "announcements": announcements}


def _build_growth_text(financial_summary: dict) -> str:
    if isinstance(financial_summary, list):
        financial_summary = financial_summary[-1] if financial_summary else {}
    if not financial_summary or not isinstance(financial_summary, dict):
        return "暂无财务数据"
    growth = financial_summary.get("growth", "")
    return str(growth)[:200] if growth else "暂无增长数据"


def format_llm_input(
    system_prompt: str,
    user_prompt: str,
) -> str:
    return f"[系统提示词]\n{system_prompt}\n\n[用户输入]\n{user_prompt}"


def _fetch_macro_data() -> dict:
    result = {}
    try:
        from api.v1.endpoints.macro import INDICATOR_FETCHERS
        for key, indicator_name in [('pmi', 'PMI'), ('cpi', 'CPI'), ('ppi', 'PPI')]:
            try:
                fetcher = INDICATOR_FETCHERS.get(indicator_name)
                if not fetcher:
                    continue
                records = fetcher()
                if records:
                    result[key] = records[-3:]
            except Exception:
                logger.warning("[StockBusiness] _fetch_macro_data fetcher failed for indicator=%s", indicator_name, exc_info=True)
    except ImportError:
        pass
    return result


def _build_environment_prompt(
    symbol: str,
    industry: str,
    main_business: str,
    peer_data: list[dict],
    macro_data: dict,
) -> tuple[str, str]:
    peer_lines = []
    for p in peer_data[:5]:
        peer_lines.append(f"  - {p.get('name', '-')} ({p.get('code', '-')})")
    peer_text = '\n'.join(peer_lines) if peer_lines else "暂无同业数据"

    def _fmt_macro(records: list[dict]) -> str:
        if not records:
            return "暂无数据"
        lines = []
        for r in records[-3:]:
            date = r.get("date", "") or r.get("end_date", "")
            value = r.get("value", "")
            lines.append(f"  - {date}: {value}")
        return "\n".join(lines)

    pmi_text = _fmt_macro(macro_data.get('pmi', []))
    cpi_text = _fmt_macro(macro_data.get('cpi', []))
    ppi_text = _fmt_macro(macro_data.get('ppi', []))

    system = (
        "你是一个股票基本面分析专家，擅长以下分析：\n"
        "1. 宏观环境分析：解读宏观经济指标对行业和个股的影响\n"
        "2. 竞争格局分析：分析公司在行业中的竞争地位\n"
        "3. 业务质量分析：评估公司护城河和经营质量\n"
        "请基于提供的数据进行分析，不要编造数据。"
    )

    user = (
        f"请分析以下股票的环境和竞争格局：\n\n"
        f"**{symbol} - {industry}**\n"
        f"主营业务：{main_business}\n\n"
        f"**宏观指标**\n"
        f"PMI:\n{pmi_text}\n\n"
        f"CPI:\n{cpi_text}\n\n"
        f"PPI:\n{ppi_text}\n\n"
        f"**同业公司**\n{peer_text}\n\n"
        f"请从以下方面分析：\n"
        f"1. 当前宏观环境对该行业的影响\n"
        f"2. 公司在行业中的竞争地位\n"
        f"3. 行业发展趋势和机会\n"
        f"4. 主要风险因素"
    )

    return system, user


def _parse_environment_analysis(response_text: str, model_used: str, llm_input: str) -> dict:
    return {
        "analysis_type": "environment",
        "model_used": model_used,
        "llm_input": llm_input,
        "response": response_text,
        "generated_at": datetime.now().isoformat(),
    }


def _fetch_peer_data(industry: str, target_symbol: str, max_peers: int = 3) -> list[dict]:
    peers = []
    try:
        from src.repositories.stock_repository import get_stocks_by_industry
        all_stocks = get_stocks_by_industry(industry) or []
        for s in all_stocks:
            if str(s.get('code', '')) != target_symbol and s.get('status') == 'active':
                peers.append({
                    'code': s.get('code'),
                    'name': s.get('name'),
                    'market': s.get('market'),
                })
                if len(peers) >= max_peers:
                    break
    except Exception as exc:
        logger.warning("[StockBusiness] peer data fetch failed: %s", exc)
    return peers


def _get_stock_industry(symbol: str) -> str:
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            from src.storage import StockMeta
            from sqlalchemy import select
            meta = session.execute(
                select(StockMeta).where(StockMeta.code == _normalize_symbol(symbol))
            ).scalars().first()
            if meta and meta.industry:
                return meta.industry
    except Exception:
        logger.warning("[StockBusiness] DB industry lookup failed for symbol=%s", symbol, exc_info=True)
    from src.data.stock_mapping import STOCK_SECTOR_MAP, STOCK_NAME_MAP
    name = STOCK_NAME_MAP.get(_normalize_symbol(symbol), "")
    sector = STOCK_SECTOR_MAP.get(_normalize_symbol(symbol), "")
    default_industry = sector or ""
    if not default_industry and name:
        if any(word in name for word in ['银行', '证券', '保险', '信托']):
            return "金融"
        if any(word in name for word in ['医药', '生物', '医疗']):
            return "医药生物"
        if any(word in name for word in ['科技', '信息', '软件', '电子']):
            return "信息技术"
        if any(word in name for word in ['地产', '房产']):
            return "房地产"
    return default_industry or "综合"


def _build_track_quality_prompt(
    symbol: str,
    industry: str,
    main_business: str,
    composition: list[dict],
    financial_summary: dict,
    peer_data: list[dict],
) -> tuple[str, str]:
    comp_text = '\n'.join(
        f"  - {c['business_name']}: 收入占比{c['revenue_pct']:.1%}"
        for c in composition[:5]
        if c.get('revenue_pct') is not None
    ) if composition else "暂无业务构成数据"

    fin_text = _build_growth_text(financial_summary)

    peer_text = '\n'.join(
        f"  - {p.get('name', '-')}: 市值{p.get('market_cap', 'N/A')}亿"
        for p in peer_data[:3]
    ) if peer_data else "暂无同业数据"

    system = (
        "你是一个深度价值投资者，擅长评估企业护城河和经营质量。\n"
        "请基于数据给出客观评估。"
    )

    user = (
        f"请评估 {symbol} 的经营质量和护城河：\n\n"
        f"行业：{industry}\n"
        f"主营：{main_business}\n\n"
        f"**业务构成**\n{comp_text}\n\n"
        f"**增长数据**\n{fin_text}\n\n"
        f"**同业对比**\n{peer_text}\n\n"
        f"请评估：\n"
        f"1. 护城河评分 (0-10)，并说明理由\n"
        f"2. 经营质量评分 (0-10)，并说明理由\n"
        f"3. 核心竞争优势\n"
        f"4. 主要经营风险\n"
        f"5. 是否值得长期跟踪（是/否）"
    )

    return system, user


def _parse_track_quality_analysis(response_text: str, model_used: str, llm_input: str, peer_data: list[dict]) -> dict:
    return {
        "analysis_type": "track_quality",
        "model_used": model_used,
        "llm_input": llm_input,
        "response": response_text,
        "peers": peer_data[:5] if peer_data else [],
        "generated_at": datetime.now().isoformat(),
    }


def _build_catalyst_prompt(
    symbol: str,
    industry: str,
    events: dict,
    financial_summary: dict,
) -> tuple[str, str]:
    news_text = '\n'.join(
        f"  - [{n.get('time', '')}] [{n.get('source', '')}] {n.get('title', '')}"
        for n in (events.get('news') or [])[:8]
    )

    ann_text = '\n'.join(
        f"  - [{a.get('date', '')}] {a.get('title', '')}"
        for a in (events.get('announcements') or [])[:8]
    )

    fin_text = _build_growth_text(financial_summary)

    system = (
        "你是一个事件驱动分析专家，擅长识别和评估催化剂事件。"
    )

    user = (
        f"请分析 {symbol}({industry}) 近期催化因素：\n\n"
        f"**近期新闻**\n{news_text or '暂无'}\n\n"
        f"**近期公告**\n{ann_text or '暂无'}\n\n"
        f"**财务摘要**\n{fin_text}\n\n"
        f"请分析：\n"
        f"1. 短期催化剂（未来1-3个月）\n"
        f"2. 中长期催化剂（3-12个月）\n"
        f"3. 潜在风险事件"
    )

    return system, user


def _parse_catalyst_analysis(response_text: str, model_used: str, llm_input: str) -> dict:
    return {
        "analysis_type": "catalyst",
        "model_used": model_used,
        "llm_input": llm_input,
        "response": response_text,
        "generated_at": datetime.now().isoformat(),
    }


def _build_business_prompt(
    symbol: str,
    intro: dict,
    composition: list[dict],
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
) -> tuple[str, str, str]:
    announcements_text = '\n'.join(
        f"  - [{a['date']}] [{a['type']}] {a['title']}"
        for a in events.get('announcements', [])[:15]
    )
    news_text = '\n'.join(
        f"  - [{n['time']}] [{n['source']}] {n['title']}" + (f"\n    {n['content'][:120]}" if n.get('content') else "")
        for n in events.get('news', [])[:10]
    )

    composition_text = '\n'.join(
        f"  - {c['business_name']}: 收入占比{(c['revenue_pct']*100):.1f}%" + (f", 毛利率{(c['gross_margin']*100):.1f}%" if c.get('gross_margin') is not None else "")
        for c in composition
        if c.get('category_type') == '按产品分类' and c.get('revenue_pct') is not None
    )[:500]

    forecast_text = '\n'.join(
        f"  - {f['analyst']}({f['researcher']}): 2026E EPS={f['eps_2026']}, 2027E={f['eps_2027']}, 2028E={f['eps_2028']}"
        for f in profit_forecast[:8]
    ) if profit_forecast else '暂无预测数据'

    summary_text = ''
    if isinstance(financial_summary, list) and financial_summary:
        r = financial_summary[-1]
        summary_text = (
            f"营收: {r.get('revenue', 'N/A')} | 净利润: {r.get('net_profit', 'N/A')} | "
            f"每股收益: {r.get('eps', 'N/A')} | 净资产: {r.get('equity', 'N/A')}"
        )

    system = (
        "你是一个专业股票分析师，擅长通过多维度数据分析公司价值。"
        "请基于提供的信息给出客观、深入的分析。"
    )

    user = (
        f"请对 {symbol} 进行全面的业务分析：\n\n"
        f"**公司简介**\n{intro.get('main_business', '暂无')[:300] if isinstance(intro, dict) else '暂无'}\n\n"
        f"**业务构成**\n{composition_text or '暂无业务构成数据'}\n\n"
        f"**财务摘要**\n{summary_text or '暂无'}\n\n"
        f"**盈利预测**\n{forecast_text}\n\n"
        f"**近期公告**\n{announcements_text or '暂无'}\n\n"
        f"**新闻舆情**\n{news_text or '暂无'}\n\n"
        f"请从以下方面分析：\n"
        f"1. 业务概览：公司主要做什么，收入结构如何\n"
        f"2. 竞争优势：核心壁垒和护城河\n"
        f"3. 财务健康：关键财务指标分析\n"
        f"4. 成长驱动：未来增长的驱动力\n"
        f"5. 风险提示：需要关注的主要风险\n"
        f"6. 综合评分 (0-10)"
    )

    return system, user, format_llm_input(system, user)


def _generate_llm_business_analysis(
    symbol: str,
    intro: dict,
    composition: list[dict],
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
    on_text=None,
    on_env_text=None,
    on_track_text=None,
    on_catalyst_text=None,
    model: str = None,
    api_key: str = None,
):
    """Generate LLM business analysis synchronously."""
    from src.config import get_config, extra_litellm_params, get_api_keys_for_model

    config = get_config()
    resolved_model = model or config.litellm_model or "gpt-4o"
    resolved_api_key = api_key
    if not resolved_api_key:
        keys = get_api_keys_for_model(resolved_model, config)
        if keys:
            resolved_api_key = keys[0]
    extra = extra_litellm_params(resolved_model, config)

    import litellm

    industry = _get_stock_industry(symbol)
    main_business = (intro.get('main_business') or intro.get('主营业务', ''))[:200] if isinstance(intro, dict) else ''
    macro_data = _fetch_macro_data()
    peer_data = _fetch_peer_data(industry, symbol)

    # Environment analysis
    env_system, env_user = _build_environment_prompt(symbol, industry, main_business, peer_data, macro_data)
    kwargs = {"model": resolved_model, "messages": [
        {"role": "system", "content": env_system},
        {"role": "user", "content": env_user},
    ], "stream": False}
    if resolved_api_key:
        kwargs["api_key"] = resolved_api_key
    if extra.get("api_base"):
        kwargs["api_base"] = extra["api_base"]
    if extra.get("extra_headers"):
        kwargs["extra_headers"] = extra["extra_headers"]

    try:
        env_response = litellm.completion(**kwargs)
        env_text = env_response.choices[0].message.content or ''
    except Exception as e:
        logger.warning("[StockBusiness] Environment analysis LLM call failed: %s", e)
        env_text = "环境分析暂不可用"
    env_input = format_llm_input(env_system, env_user)
    env_result = _parse_environment_analysis(env_text, resolved_model, env_input)
    if on_env_text:
        on_env_text(env_text)

    # Track quality analysis
    tr_system, tr_user = _build_track_quality_prompt(symbol, industry, main_business, composition, financial_summary, peer_data)
    kwargs["messages"] = [
        {"role": "system", "content": tr_system},
        {"role": "user", "content": tr_user},
    ]
    try:
        tr_response = litellm.completion(**kwargs)
        tr_text = tr_response.choices[0].message.content or ''
    except Exception as e:
        logger.warning("[StockBusiness] Track quality LLM call failed: %s", e)
        tr_text = "经营质量分析暂不可用"
    tr_input = format_llm_input(tr_system, tr_user)
    tr_result = _parse_track_quality_analysis(tr_text, resolved_model, tr_input, peer_data)
    if on_track_text:
        on_track_text(tr_text)

    # Catalyst analysis
    cat_system, cat_user = _build_catalyst_prompt(symbol, industry, events, financial_summary)
    kwargs["messages"] = [
        {"role": "system", "content": cat_system},
        {"role": "user", "content": cat_user},
    ]
    try:
        cat_response = litellm.completion(**kwargs)
        cat_text = cat_response.choices[0].message.content or ''
    except Exception as e:
        logger.warning("[StockBusiness] Catalyst LLM call failed: %s", e)
        cat_text = "催化剂分析暂不可用"
    cat_input = format_llm_input(cat_system, cat_user)
    cat_result = _parse_catalyst_analysis(cat_text, resolved_model, cat_input)
    if on_catalyst_text:
        on_catalyst_text(cat_text)

    # Full business analysis
    biz_system, biz_user, llm_input = _build_business_prompt(symbol, intro, composition, profit_forecast, financial_summary, events)
    kwargs["messages"] = [
        {"role": "system", "content": biz_system},
        {"role": "user", "content": biz_user},
    ]
    try:
        biz_response = litellm.completion(**kwargs)
        biz_full_text = biz_response.choices[0].message.content or ''
    except Exception as e:
        logger.warning("[StockBusiness] Business analysis LLM call failed: %s", e)
        biz_full_text = "业务分析暂不可用"
    if on_text:
        on_text(biz_full_text)

    return {
        "symbol": symbol,
        "intro": intro,
        "composition": composition[:20] if composition else [],
        "profit_forecast": profit_forecast[:10] if profit_forecast else [],
        "financial_summary": financial_summary[-20:] if isinstance(financial_summary, list) else financial_summary,
        "events": events,
        "environment_analysis": env_result,
        "track_quality_analysis": tr_result,
        "catalyst_analysis": cat_result,
        "business_analysis": {
            "analysis_type": "business",
            "model_used": resolved_model,
            "llm_input": llm_input,
            "response": biz_full_text if biz_full_text else "业务分析暂不可用",
            "generated_at": datetime.now().isoformat(),
        },
        "generated_at": datetime.now().isoformat(),
    }


def _format_business_sse_event(event_type: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


@router.get("/business", summary="获取个股业务分析数据")
def get_stock_business(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
    model: Optional[str] = Query(None, description="指定 LLM 模型"),
    api_key: Optional[str] = Query(None, description="指定 API Key"),
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
        model=model,
        api_key=api_key,
    )

    _business_cache_put(normalized, result)
    return result


@router.get("/business/stream", summary="获取个股业务分析数据 (SSE 流)")
async def get_stock_business_stream(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    model: Optional[str] = Query(None, description="指定 LLM 模型"),
    api_key: Optional[str] = Query(None, description="指定 API Key"),
):
    """Stream stock business analysis via SSE with real-time progress."""
    normalized = _normalize_symbol(symbol)

    def _enqueue(event_type: str, data):
        return _format_business_sse_event(event_type, data)

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

                def _on_text(delta: str, full_text: str):
                    queue.put_nowait(("business_text", {"delta": delta}))

                def _on_env_text(delta: str, full_text: str):
                    queue.put_nowait(("environment_text", {"delta": delta}))

                def _on_track_text(delta: str, full_text: str):
                    queue.put_nowait(("track_quality_text", {"delta": delta}))

                def _on_catalyst_text(delta: str, full_text: str):
                    queue.put_nowait(("catalyst_text", {"delta": delta}))

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
                    model=model,
                    api_key=api_key,
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
                yield _enqueue(event_type, data)
        except asyncio.CancelledError:
            logger.debug("[StockBusiness] Client disconnected, cancelling worker")
            raise
        finally:
            cancel_event.set()

    return StreamingResponse(event_generator(), media_type="text/event-stream")