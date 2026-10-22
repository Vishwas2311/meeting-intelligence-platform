"""Rehearse a known legacy SQLite upgrade on a backup; --apply is an explicit offline cutover."""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def upgrade(path):
    from scripts.check_schema_baseline import verify

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN EXCLUSIVE")
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.set_main_option(
                "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
            )
            config.attributes["connection"] = connection
            if "alembic_version" not in inspect(connection).get_table_names():
                verify(connection)
                command.stamp(config, "0001_baseline")
            command.upgrade(config, "head")
            if (
                connection.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok"
                or connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            ):
                raise RuntimeError("Upgraded database integrity failed")
            connection.commit()
    finally:
        engine.dispose()


def rehearse(source, backup_dir, apply=False):
    source, backup_dir = Path(source).resolve(), Path(backup_dir).resolve()
    if not source.is_file():
        raise RuntimeError("Existing source SQLite file is required")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"before-production-fields-{stamp}.db"
    rehearsal = backup_dir / f"rehearsal-{stamp}.db"
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original,
        sqlite3.connect(backup) as saved,
    ):
        original.backup(saved)
    with sqlite3.connect(backup) as saved, sqlite3.connect(rehearsal) as copy:
        saved.backup(copy)
    upgrade(rehearsal)
    # Compare every pre-existing row/column via its original PK; additive snapshots are allowed.
    from migrations.baseline import Base

    with sqlite3.connect(rehearsal) as checked:
        checked.execute("ATTACH DATABASE ? AS baseline", (str(backup),))

        def quoted(value):
            return '"' + value.replace('"', '""') + '"'

        for table in Base.metadata.tables.values():
            join = " AND ".join(
                f"a.{quoted(c.name)} IS b.{quoted(c.name)}" for c in table.primary_key.columns
            )
            different = " OR ".join(
                f"a.{quoted(c.name)} IS NOT b.{quoted(c.name)}" for c in table.c
            )
            count = checked.execute(
                f"SELECT COUNT(*) FROM baseline.{quoted(table.name)} b LEFT JOIN main.{quoted(table.name)} a ON {join} WHERE {different}"
            ).fetchone()[0]
            if count:
                raise RuntimeError("Legacy data comparison failed; source retained")
    if apply:
        upgrade(source)
    return {
        "status": "PASS",
        "source_applied": apply,
        "backup": str(backup),
        "rehearsal": str(rehearsal),
        "schema": "0002_production",
        "preexisting_rows_verified": True,
        "audio_files_changed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error("Stop API/worker writers, verify backup custody, then pass --writers-stopped")
    try:
        result = rehearse(args.database, args.backup_dir, args.apply)
    except Exception:
        raise SystemExit(
            "SQLite rehearsal/upgrade FAILED. Source/backup retained; inspect the dedicated copy before retrying."
        ) from None
    print(json.dumps(result))
