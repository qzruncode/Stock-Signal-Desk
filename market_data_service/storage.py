"""Data-provider storage contract, isolated from all business tables."""

from market_data_service.database import (
    Database as Database,
    DatabaseManager as DatabaseManager,
    get_database as get_database,
)
from market_data_service.models import *  # noqa: F403
