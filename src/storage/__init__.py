# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 存储层
===================================

职责：
1. 管理 SQLite 数据库连接（单例模式）
2. 定义 ORM 数据模型
3. 提供数据存取接口
4. 实现智能更新逻辑（断点续传）
"""

import logging
from typing import Any, Dict, Optional

from src.storage.models import (
    Base,
    StockDaily,
    StockMeta,
    DataMaintenanceJob,
    NewsIntel,
    FundamentalSnapshot,
    AnalysisHistory,
    BuyCriteriaRecord,
    MarketMainlineReport,
    ChatConversation,
    ChatMessage,
    BacktestResult,
    BacktestSummary,
    PortfolioAccount,
    PortfolioTrade,
    PortfolioCashLedger,
    PortfolioCorporateAction,
    PortfolioPosition,
    PortfolioPositionLot,
    PortfolioDailySnapshot,
    PortfolioFxRate,
    ConversationMessage,
    LLMUsage,
    AlertRuleRecord,
    AlertTriggerRecord,
    AlertNotificationRecord,
    BatchRun,
    BatchSchedule,
    QuoteSnapshot,
    KlineSnapshot,
    RssCache,
    MacroIndexDaily,
    BondYieldDaily,
    MacroIndicator,
    WatchlistGroup,
    WatchlistGroupNameConflict,
    AgentPromptTemplate,
)
from src.storage.manager import DatabaseManager

logger = logging.getLogger(__name__)


def get_db() -> DatabaseManager:
    """获取数据库管理器实例的快捷方式"""
    return DatabaseManager.get_instance()


def persist_llm_usage(
    usage: Dict[str, Any],
    model: str,
    call_type: str,
    stock_code: Optional[str] = None,
) -> None:
    """Fire-and-forget: write one LLM call record to llm_usage. Never raises."""
    try:
        db = DatabaseManager.get_instance()
        db.record_llm_usage(
            call_type=call_type,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            total_tokens=usage.get("total_tokens", 0) or 0,
            stock_code=stock_code,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "[LLM usage] failed to persist usage record: %s", exc
        )


__all__ = [
    "Base",
    "StockDaily",
    "StockMeta",
    "DataMaintenanceJob",
    "NewsIntel",
    "FundamentalSnapshot",
    "AnalysisHistory",
    "BuyCriteriaRecord",
    "MarketMainlineReport",
    "ChatConversation",
    "ChatMessage",
    "BacktestResult",
    "BacktestSummary",
    "PortfolioAccount",
    "PortfolioTrade",
    "PortfolioCashLedger",
    "PortfolioCorporateAction",
    "PortfolioPosition",
    "PortfolioPositionLot",
    "PortfolioDailySnapshot",
    "PortfolioFxRate",
    "ConversationMessage",
    "LLMUsage",
    "AlertRuleRecord",
    "AlertTriggerRecord",
    "AlertNotificationRecord",
    "BatchRun",
    "BatchSchedule",
    "QuoteSnapshot",
    "KlineSnapshot",
    "RssCache",
    "MacroIndexDaily",
    "BondYieldDaily",
    "MacroIndicator",
    "WatchlistGroup",
    "WatchlistGroupNameConflict",
    "AgentPromptTemplate",
    "DatabaseManager",
    "get_db",
    "persist_llm_usage",
]
