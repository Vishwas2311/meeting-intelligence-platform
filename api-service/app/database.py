import sqlite3
import time
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
    settings = get_settings()
    from sqlalchemy.engine import make_url

    oracle = make_url(url).get_backend_name() == "oracle"
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    if oracle:
        connect_args["tcp_connect_timeout"] = settings.db_connect_timeout_seconds
        for name in ("config_dir", "wallet_location", "wallet_password"):
            value = getattr(settings, "oracle_" + name)
            if value:
                connect_args[name] = value
    pool = (
        {}
        if url.startswith("sqlite") and make_url(url).database in (None, "", ":memory:")
        else {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout_seconds,
        }
    )
    result = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        **pool,
    )

    @event.listens_for(result, "before_cursor_execute")
    def query_started(connection, cursor, statement, parameters, context, executemany):
        context._meeting_query_started = time.monotonic()

    @event.listens_for(result, "after_cursor_execute")
    def query_finished(connection, cursor, statement, parameters, context, executemany):
        from .observability import sql_latency

        operation = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "OTHER"
        if operation not in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
            operation = "OTHER"
        sql_latency.labels(operation).observe(time.monotonic() - context._meeting_query_started)

    if url.startswith("sqlite"):

        @event.listens_for(result, "connect")
        def sqlite_settings(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
    elif oracle:

        @event.listens_for(result, "connect")
        def oracle_timeout(connection, _):
            connection.call_timeout = settings.db_statement_timeout_seconds * 1000

    return result


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(SessionLocal, "before_commit")
def commit_started(session):
    session.info["meeting_commit_started"] = time.monotonic()


@event.listens_for(SessionLocal, "after_commit")
def commit_finished(session):
    from .observability import commit_latency

    started = session.info.pop("meeting_commit_started", None)
    if started is not None:
        commit_latency.observe(time.monotonic() - started)


def initialize_database(target: Engine = engine, *, runtime=False) -> None:
    """Add only the known job-recovery columns; preserve existing local meeting history."""
    from .models import Job

    if get_settings().app_env == "production":
        check_schema_version(target)
        return

    inventory = inspect(target)
    if runtime and inventory.has_table("meetings"):
        from . import models  # noqa: F401

        tables = set(inventory.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in tables or set(table.c.keys()) - {
                c["name"] for c in inventory.get_columns(table.name)
            }:
                raise RuntimeError(
                    "Database schema upgrade required; rehearse the backed-up local migration before starting this release"
                )
    if inventory.has_table("meetings") and "evidence_revision" not in {
        c["name"] for c in inventory.get_columns("meetings")
    }:
        raise RuntimeError(
            "Legacy database requires an explicit backed-up Alembic upgrade; follow the local migration guide before starting this release"
        )

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
    if target.dialect.name == "oracle":
        # Oracle DDL commits implicitly. Run schema initialization once before starting workers.
        with target.begin() as connection:
            for name, kind in missing.items():
                connection.execute(text(migration_column_sql("oracle", name, kind)))
        return
    with target.begin() as connection:
        if target.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.exec_driver_sql("LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE")
        current = {column["name"] for column in inspect(connection).get_columns("jobs")}
        for name, kind in missing.items():
            if name in current:
                continue
            connection.execute(text(migration_column_sql(target.dialect.name, name, kind)))

    # Non-production existing databases require the same explicit Alembic upgrade
    # for new application fields; never silently alter a user's meeting history.


SCHEMA_HEAD = "0002_production"


def check_schema_version(target=engine):
    with target.connect() as connection:
        if "alembic_version" not in inspect(connection).get_table_names():
            raise RuntimeError("Database migration required; run the documented Alembic upgrade")
        value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if value != SCHEMA_HEAD:
            raise RuntimeError("Database schema version is incompatible with this release")


def migration_column_sql(dialect, name, kind):
    if name not in {"run_after", "lease_until", "claim_token", "checkpoint"}:
        raise ValueError("Unknown migration column")
    if kind == "TIMESTAMP" and dialect != "sqlite":
        kind = "TIMESTAMP WITH TIME ZONE"
    if dialect == "oracle":
        kind = {"JSON": "CLOB", "VARCHAR(36)": "VARCHAR2(36 CHAR)"}.get(kind, kind)
        return f"ALTER TABLE jobs ADD ({name} {kind})"
    return f"ALTER TABLE jobs ADD COLUMN {name} {kind}"


def db_session() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db
