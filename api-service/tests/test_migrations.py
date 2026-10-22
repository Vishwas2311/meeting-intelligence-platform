from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def upgrade(engine, revision="head"):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_fresh_migration_is_repeatable_and_matches_models(client, tmp_path):
    from app.database import Base, check_schema_version

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    upgrade(engine)
    upgrade(engine)
    check_schema_version(engine)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    for table in Base.metadata.tables.values():
        assert set(c["name"] for c in inspector.get_columns(table.name)) == set(table.c.keys())
    engine.dispose()


def test_verified_legacy_upgrade_preserves_minutes_and_owner(client, tmp_path):
    from migrations.baseline import Base
    from scripts.check_schema_baseline import verify

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        assert verify(connection)
        meetings = Base.metadata.tables["meetings"]
        connection.execute(
            meetings.insert().values(id="synthetic", title="Preserved", created_by="alice")
        )
        connection.execute(
            Base.metadata.tables["moms"]
            .insert()
            .values(
                meeting_id="synthetic",
                data={"title": "Preserved", "summary": "Synthetic"},
                status="approved",
            )
        )
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.stamp(config, "0001_baseline")
    upgrade(engine)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT title FROM meetings WHERE id='synthetic'")).scalar()
            == "Preserved"
        )
        assert (
            connection.execute(
                text("SELECT created_by FROM meetings WHERE id='synthetic'")
            ).scalar()
            == "alice"
        )
        assert (
            connection.execute(
                text("SELECT revision FROM moms WHERE meeting_id='synthetic'")
            ).scalar()
            == 0
        )
    engine.dispose()


def test_baseline_stamp_refuses_unknown_schema(client, tmp_path):
    from scripts.check_schema_baseline import verify

    engine = create_engine(f"sqlite:///{tmp_path / 'unknown.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE unknown (id INTEGER)"))
        with pytest.raises(RuntimeError, match="inventory mismatch"):
            verify(connection)
        assert "alembic_version" not in inspect(connection).get_table_names()
    engine.dispose()


def test_local_rehearsal_preserves_original_and_verifies_existing_rows(client, tmp_path):
    import hashlib

    from migrations.baseline import Base
    from scripts.migrate_local import rehearse

    source = tmp_path / "original.db"
    engine = create_engine(f"sqlite:///{source}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            Base.metadata.tables["meetings"]
            .insert()
            .values(id="synthetic-local", title="Preserve history", created_by="owner")
        )
    engine.dispose()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = rehearse(source, tmp_path / "backup")
    assert result["status"] == "PASS" and result["source_applied"] is False
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert Path(result["backup"]).is_file()
