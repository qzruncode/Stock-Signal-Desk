"""Alembic owns service schema upgrades; no business schema is imported."""

from alembic import context
from sqlalchemy import engine_from_config, pool
from market_data_service.models import Base
from market_data_service import control_models  # noqa: F401 -- register service control tables
from market_data_service.settings import get_settings

config = context.config
target_metadata = Base.metadata


def run(connection):
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = get_settings().database_url
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        run(connection)
