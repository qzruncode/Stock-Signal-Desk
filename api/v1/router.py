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
    auth,
    system_config,
    stocks,
    watchlist,
    agent,
    rss,
    indicator_screening,
    data_service,
)

# 创建 v1 版本主路由
router = APIRouter(prefix="/api/v1")
router.include_router(data_service.router, prefix="/data-service", tags=["DataService"])

router.include_router(auth.router, prefix="/auth", tags=["Auth"])

router.include_router(system_config.router, prefix="/system", tags=["SystemConfig"])

router.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist"])

router.include_router(stocks.router, prefix="/stocks", tags=["Stocks"])

router.include_router(
    indicator_screening.router,
    prefix="/indicator-screening",
    tags=["IndicatorScreening"],
)

router.include_router(agent.router, prefix="", tags=["Agent"])

router.include_router(rss.router, prefix="/rss", tags=["RSS"])
