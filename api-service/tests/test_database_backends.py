"""SQLite behavior plus Oracle SQL/type checks; live Oracle tests are opt-in."""

import json
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import oracle, sqlite
from sqlalchemy.schema import CreateTable


def test_oracle_configuration_without_exposing_password():
    from sqlalchemy.engine import make_url

    from app.config import Settings

    s = Settings(
        _env_file=None,
        database_url="oracle",
        oracle_user="meeting",
        oracle_password="p@ss:/word",
        oracle_dsn="db.internal:1521/FREEPDB1",
    )
    url = make_url(s.database_url)
    assert url.drivername == "oracle+oracledb"
    assert url.username == "meeting" and url.password == "p@ss:/word"
    assert url.query["dsn"] == "db.internal:1521/FREEPDB1"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="oracle")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="oracle+cx_oracle://user:pass@host/db")


def test_all_model_tables_compile_for_oracle(client):
    from app.database import Base

    dialect = oracle.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert ddl and " JSON" not in ddl
    ddl = str(CreateTable(Base.metadata.tables["jobs"]).compile(dialect=dialect))
    assert "checkpoint CLOB" in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "notes CLOB" in str(
        CreateTable(Base.metadata.tables["personal_recaps"]).compile(dialect=dialect)
    )


def test_oracle_driver_can_build_an_engine_without_network():
    from app.config import Settings
    from app.database import make_engine

    settings = Settings(
        _env_file=None,
        database_url="oracle",
        oracle_user="test_user",
        oracle_password="not-a-real-password",
        oracle_dsn="localhost:1521/FREEPDB1",
    )
    target = make_engine(settings.database_url)
    try:
        assert target.dialect.name == "oracle"
        assert target.dialect.driver == "oracledb"
        _, options = target.dialect.create_connect_args(target.url)
        assert options["dsn"] == "localhost:1521/FREEPDB1"
    finally:
        target.dispose()


def test_documents_large_multilingual_and_blank_notes(client):
    from app.database import SessionLocal
    from app.db_types import Document, NotesText, UtcTimestamp
    from app.models import Meeting, PersonalRecap

    document = {"discussion": "हिन्दी मराठी 中文 " * 10000, "items": [1, None, True]}
    value = Document()
    encoded = value.process_bind_param(document, oracle.dialect())
    assert len(encoded) > 32767 and json.loads(encoded) == document
    assert value.process_result_value(encoded, oracle.dialect()) == document
    assert (
        value.process_result_value(SimpleNamespace(read=lambda: encoded), oracle.dialect())
        == document
    )
    assert value.process_bind_param(document, sqlite.dialect()) == document
    assert value.process_result_value(None, oracle.dialect()) is None
    assert NotesText().process_result_value(None, oracle.dialect()) == ""
    stamp = datetime(2026, 10, 21, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert UtcTimestamp().process_bind_param(stamp, oracle.dialect()).hour == 6
    with SessionLocal() as db:
        meeting = Meeting(title="Type roundtrip")
        db.add(meeting)
        db.flush()
        mid = meeting.id
        db.add(PersonalRecap(meeting_id=mid, participants=["Alex", "मराठी"], notes=""))
        db.commit()
    with SessionLocal() as db:
        assert db.get(PersonalRecap, mid).notes == ""
        assert db.get(PersonalRecap, mid).participants == ["Alex", "मराठी"]
        assert db.get(Meeting, mid).created_at.tzinfo == UTC


def test_oracle_job_selection_uses_atomic_claim_without_invalid_row_lock(client, monkeypatch):
    from app import worker

    sql = []

    class Session:
        bind = SimpleNamespace(dialect=oracle.dialect())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalar(self, statement):
            sql.append(str(statement.compile(dialect=self.bind.dialect)))
            return None

    monkeypatch.setattr(worker, "SessionLocal", Session)
    assert worker.acquire_job() is None
    assert "FETCH FIRST" in sql[0] and "FOR UPDATE" not in sql[0]


def test_oracle_migration_sql_and_backup_rejection(tmp_path):
    from app.database import migration_column_sql
    from app.storage import backup

    assert (
        migration_column_sql("oracle", "checkpoint", "JSON")
        == "ALTER TABLE jobs ADD (checkpoint CLOB)"
    )
    assert "TIMESTAMP WITH TIME ZONE" in migration_column_sql("oracle", "run_after", "TIMESTAMP")
    assert "VARCHAR2" in migration_column_sql("oracle", "claim_token", "VARCHAR(36)")
    assert "ADD COLUMN" in migration_column_sql("sqlite", "checkpoint", "JSON")
    with pytest.raises(RuntimeError, match="SQLite-only"):
        backup(tmp_path / "never.zip", "oracle+oracledb://user:password@host/?service_name=example")
    assert not (tmp_path / "never.zip").exists()


def test_storage_reports_database_backup_capability(client, auth):
    result = client.get("/api/storage", headers=auth)
    assert result.status_code == 200
    assert result.json()["database"] == "sqlite"
    assert result.json()["backup_supported"] is True
