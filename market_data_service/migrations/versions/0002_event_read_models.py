"""Transactional outbox, indexed read models, and commit-time notifications.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Only fields visible to readers produce events. Lease renewal and queue claims
# must not cause a page update. Observation payloads never enter the event log.
WATCHED = (
    ("md_data_state", "state", "dataset", "symbol", None),
    ("md_observation", "observation", "dataset", "request_key", ()),
    (
        "md_source_subscription",
        "subscription",
        "dataset",
        "request_key",
        ("last_requested_at", "error"),
    ),
    (
        "md_dataset_policy",
        "policy",
        "dataset",
        "dataset",
        ("enabled", "interval_seconds", "max_age_seconds"),
    ),
    (
        "md_sync_job",
        "job",
        "dataset",
        "id",
        (
            "status",
            "total",
            "progress",
            "succeeded",
            "failed",
            "message",
            "error",
            "cancel_requested",
        ),
    ),
    ("stock_meta", "master", "=securities", "code", ("name", "status")),
    ("md_trading_day", "calendar", "=calendar", "day", ()),
    ("md_service_heartbeat", "health", "=health", "component", None),
)


def upgrade():
    op.create_table(
        "md_change_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("dataset", sa.String(40), nullable=False),
        sa.Column("entity", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime()),
    )
    op.create_index(
        "ix_md_change_outbox_dispatched_at", "md_change_outbox", ["dispatched_at"]
    )
    op.create_table(
        "md_coverage_projection",
        sa.Column("dataset", sa.String(40), primary_key=True),
        sa.Column("entity", sa.String(128), primary_key=True),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("data_time", sa.String(64)),
        sa.Column("checked_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("source", sa.String(128)),
        sa.Column("version", sa.String(64)),
        sa.Column("error", sa.Text()),
        sa.Column("next_check_at", sa.DateTime()),
    )
    op.create_index(
        "ix_md_coverage_dataset_status_symbol",
        "md_coverage_projection",
        ["dataset", "status", "symbol"],
    )
    op.create_index("ix_md_coverage_due", "md_coverage_projection", ["next_check_at"])
    op.create_table(
        "md_dataset_projection",
        sa.Column("dataset", sa.String(40), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_md_observation_request_fetched",
        "md_observation",
        ["request_key", "fetched_at"],
    )
    op.create_index(
        "ix_md_subscription_dataset_requested",
        "md_source_subscription",
        ["dataset", "last_requested_at"],
    )
    postgres = op.get_bind().dialect.name == "postgresql"
    for table, kind, dataset, entity, columns in WATCHED:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            if operation == "UPDATE" and columns == ():
                continue
            row = "OLD" if operation == "DELETE" else "NEW"
            dataset_value = (
                f"'{dataset[1:]}'" if dataset.startswith("=") else f"{row}.{dataset}"
            )
            name = f"md_event_{kind}_{operation.lower()}"
            changed = ""
            if operation == "UPDATE" and columns:
                compare = "IS DISTINCT FROM" if postgres else "IS NOT"
                changed = " OR ".join(
                    f"NEW.{column} {compare} OLD.{column}" for column in columns
                )
            insert = (
                "INSERT INTO md_change_outbox(kind,dataset,entity,created_at) "
                f"VALUES ('{kind}',{dataset_value},{row}.{entity},"
                + (
                    "timezone('UTC', clock_timestamp())"
                    if postgres
                    else "CURRENT_TIMESTAMP"
                )
                + ");"
            )
            if postgres:
                op.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN {insert} PERFORM pg_notify('md_changes', ''); RETURN NULL; END $$""")
                condition = f" WHEN ({changed})" if changed else ""
                op.execute(
                    f"CREATE TRIGGER {name} AFTER {operation} ON {table} FOR EACH ROW{condition} EXECUTE FUNCTION {name}()"
                )
            else:
                condition = f" WHEN {changed}" if changed else ""
                op.execute(
                    f"CREATE TRIGGER {name} AFTER {operation} ON {table}{condition} BEGIN {insert} END"
                )


def downgrade():
    postgres = op.get_bind().dialect.name == "postgresql"
    for table, kind, _, _, columns in WATCHED:
        for operation in ("insert", "update", "delete"):
            if operation == "update" and columns == ():
                continue
            name = f"md_event_{kind}_{operation}"
            op.execute(
                f"DROP TRIGGER IF EXISTS {name}" + (f" ON {table}" if postgres else "")
            )
            if postgres:
                op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    op.drop_index("ix_md_subscription_dataset_requested", "md_source_subscription")
    op.drop_index("ix_md_observation_request_fetched", "md_observation")
    op.drop_table("md_dataset_projection")
    op.drop_table("md_coverage_projection")
    op.drop_table("md_change_outbox")
