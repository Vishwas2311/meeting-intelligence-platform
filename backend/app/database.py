import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    result = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    if url.startswith("sqlite"):

        @event.listens_for(result, "connect")
        def sqlite_settings(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")

    return result


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database(target: Engine = engine) -> None:
    """Add only the known job-recovery columns; preserve existing local meeting history."""
    from .models import Job

    Job.metadata.create_all(bind=target)
    columns = {column["name"] for column in inspect(target).get_columns("jobs")}
    additions = {
        "run_after": "TIMESTAMP",
        "lease_until": "TIMESTAMP",
        "claim_token": "VARCHAR(36)",
        "checkpoint": "JSON",
    }
    missing = {name: kind for name, kind in additions.items() if name not in columns}
    if not missing:
        return
    if target.dialect.name == "sqlite" and target.url.database not in (None, "", ":memory:"):
        path = Path(target.url.database)
        backup = path.with_name(
            f"{path.name}.before-job-recovery-{datetime.now(UTC):%Y%m%d%H%M%S%f}.bak"
        )
        with (
            closing(sqlite3.connect(path)) as source,
            closing(sqlite3.connect(backup)) as destination,
        ):
            source.backup(destination)
    with target.begin() as connection:
        if target.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.exec_driver_sql("LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE")
        current = {column["name"] for column in inspect(connection).get_columns("jobs")}
        for name, kind in missing.items():
            if name in current:
                continue
            if kind == "TIMESTAMP" and target.dialect.name != "sqlite":
                kind = "TIMESTAMP WITH TIME ZONE"
            connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {kind}"))


def db_session() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db
