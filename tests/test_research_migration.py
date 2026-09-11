from sqlalchemy import create_engine, inspect, text

from src.storage.migrations import ensure_compatible_schema, get_schema_version, SCHEMA_VERSION


def test_old_rows_survive_replayable_additive_migration(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        # Minimal historical tables; other tables are created by the migration.
        connection.execute(text('CREATE TABLE agent_runs (id VARCHAR(64) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO agent_runs (id) VALUES ('old-run')"))
        connection.execute(text('CREATE TABLE alert_rules (id INTEGER PRIMARY KEY, name VARCHAR(64), source VARCHAR(16))'))
        connection.execute(text("INSERT INTO alert_rules (id, name) VALUES (1, 'old-rule')"))
        connection.execute(text('CREATE TABLE alert_triggers (id INTEGER PRIMARY KEY)'))
    ensure_compatible_schema(engine)
    ensure_compatible_schema(engine)
    assert get_schema_version(engine) == SCHEMA_VERSION
    assert 'usage_json' in {column['name'] for column in inspect(engine).get_columns('agent_runs')}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT usage_json FROM agent_runs WHERE id='old-run'")).scalar() == '{}'
        assert connection.execute(text('SELECT name, tenant_id, owner_id, state_json FROM alert_rules')).one() == ('old-rule', 'local', 'admin', '{}')
    engine.dispose()
