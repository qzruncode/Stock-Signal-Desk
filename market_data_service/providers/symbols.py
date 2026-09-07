from sqlalchemy import select
from market_data_service.database import get_database
from market_data_service.models import StockMeta
from market_data_service.providers.common import bare_symbol


resolve_symbol = bare_symbol
resolve_local_symbol = bare_symbol


def get_index_stock_name(code):
    with get_database().get_session() as session:
        return session.scalar(select(StockMeta.name).where(StockMeta.code == code))
