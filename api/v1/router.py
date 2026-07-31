# -*- coding: utf-8 -*-
"""
===================================
API v1 路由聚合
===================================

职责：
1. 聚合 v1 版本的所有 endpoint 路由
2. 统一添加 /api/v1 前缀
"""

from fastapi import APIRouter

from api.v1.endpoints import (
    analysis,
    auth,
    history,
    system_config,
    prompts,
    batches,
    stocks,
    watchlist,
    quotes,
    kline,
    market_status,
    sectors,
    stock_info,
    financials,
    macro,
    agent,
    rss,
    market_themes,
    industry_cycle,
    buy_decision,
)

# 创建 v1 版本主路由
router = APIRouter(prefix="/api/v1")

router.include_router(auth.router, prefix="/auth", tags=["Auth"])

router.include_router(analysis.router, prefix="/analysis", tags=["Analysis"])

router.include_router(history.router, prefix="/history", tags=["History"])

router.include_router(system_config.router, prefix="/system", tags=["SystemConfig"])

router.include_router(prompts.router, prefix="/prompts", tags=["Prompts"])

router.include_router(batches.router, prefix="/batch", tags=["Batch"])

router.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist"])

router.include_router(stocks.router, prefix="/stocks", tags=["Stocks"])

router.include_router(quotes.router, prefix="/quotes", tags=["Quotes"])

router.include_router(kline.router, prefix="/kline", tags=["KLine"])

router.include_router(market_status.router, prefix="/market", tags=["Market"])

router.include_router(sectors.router, prefix="/market", tags=["Market"])

router.include_router(market_themes.router, prefix="/market", tags=["Market"])

router.include_router(stock_info.router, prefix="/stocks", tags=["Stocks"])

router.include_router(financials.router, prefix="/stocks", tags=["Stocks"])

router.include_router(industry_cycle.router, prefix="/stocks", tags=["Stocks"])

router.include_router(buy_decision.router, prefix="/stocks", tags=["Stocks"])

router.include_router(macro.router, prefix="/macro", tags=["Macro"])

router.include_router(agent.router, prefix="", tags=["Agent"])

router.include_router(rss.router, prefix="/rss", tags=["RSS"])
