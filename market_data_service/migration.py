"""Transactional, versioned schema bootstrap with PostgreSQL's native DDL lock."""

from pathlib import Path
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text


def upgrade(engine):
    from market_data_service.models import Base

    config = Config(str(Path(__file__).with_name("alembic.ini")))
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(738190164)"))
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if tables and "alembic_version" not in tables:
            differences = compare_metadata(
                MigrationContext.configure(connection), Base.metadata
            )
            if differences:
                raise RuntimeError(
                    "Unversioned market database does not match the initial schema; back up and review migrations before proceeding"
                )
            # Adopt only an exactly matching pre-release service schema. Never
            # blindly stamp an unrelated/legacy business database.
            command.stamp(config, "0001")
        command.upgrade(config, "head")
