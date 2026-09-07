"""The source-side security-master readiness contract (no business imports)."""

from sqlalchemy import select, func
from market_data_service.control_models import DataState, DatasetPolicy
from market_data_service.database import get_database
from market_data_service.freshness import state_status
from market_data_service.models import StockMeta


def ensure_stock_universe(*, trigger=None):
    with get_database().get_session() as session:
        state = session.scalar(
            select(DataState).where(
                DataState.dataset == "securities", DataState.symbol == "all"
            )
        )
        if state_status(state, session.get(DatasetPolicy, "securities")) != "fresh":
            raise RuntimeError("证券主数据尚未达标，请等待自动维护完成")
        return {
            "total": session.scalar(
                select(func.count())
                .select_from(StockMeta)
                .where(StockMeta.status == "active")
            ),
            "is_stale": False,
            "maintenance_status": "ready",
        }
