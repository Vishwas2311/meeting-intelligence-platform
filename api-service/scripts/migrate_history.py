"""Explicit, offline SQLite history copy into an empty migrated database and audio root.

Default is read-only inventory. The source is always opened read-only. Set
MIGRATION_TARGET_URL only for --apply, after a disposable rehearsal and backup.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def migrate(source_path, source_audio, owner_mapping, destination_audio=None, target_url=None):
    from app import models
    from app.database import Base, check_schema_version, make_engine
    from migrations.baseline import Base as Legacy

    source_path, source_audio = Path(source_path).resolve(), Path(source_audio).resolve()
    if not source_path.is_file():
        raise RuntimeError("Source SQLite database is missing")
    source = create_engine(
        "sqlite://", creator=lambda: sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    )
    target = None
    try:
        with source.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if tables == set(Legacy.metadata.tables):
                from scripts.check_schema_baseline import verify

                verify(connection)
                metadata = Legacy.metadata
            elif tables == set(Base.metadata.tables) | {"alembic_version"}:
                check_schema_version(source)
                metadata = Base.metadata
            else:
                raise RuntimeError("Source schema is not a verified legacy or current release")
            owners = set(connection.scalars(select(metadata.tables["meetings"].c.created_by)))
            if not owners <= set(owner_mapping) or any(
                not owner_mapping[x] or len(owner_mapping[x]) > 150 for x in owners
            ):
                raise RuntimeError("Every source meeting owner requires an explicit valid mapping")
            counts = {
                table.name: connection.scalar(select(func.count()).select_from(table))
                for table in metadata.sorted_tables
            }
            manifest = []
            for row in connection.execute(select(metadata.tables["audio_chunks"])).mappings():
                if row.get("storage_state", "committed") != "committed":
                    continue
                path = Path(row["path"])
                path = path.resolve() if path.is_absolute() else (source_audio / path).resolve()
                if (
                    not path.is_relative_to(source_audio)
                    or not path.is_file()
                    or checksum(path) != row["sha256"]
                ):
                    raise RuntimeError("Source audio path/checksum validation failed")
                if path.stat().st_size != row["size_bytes"]:
                    raise RuntimeError("Source audio size validation failed")
                manifest.append(
                    {
                        "id": row["id"],
                        "key": f"{row['meeting_id']}/{row['sequence']:09d}.bin",
                        "sha256": row["sha256"],
                        "bytes": row["size_bytes"],
                    }
                )
            if not target_url:
                return {"mode": "inventory", "rows": counts, "audio": manifest}
            if not destination_audio:
                raise RuntimeError("An explicit empty destination audio root is required")
            destination_audio = Path(destination_audio).resolve()
            if destination_audio == source_audio or destination_audio.is_relative_to(source_audio):
                raise RuntimeError("Destination must be separate from the original audio")
            if destination_audio.exists() and any(
                p.name != ".meeting-volume" for p in destination_audio.iterdir()
            ):
                raise RuntimeError("Destination audio root must be empty except its mount marker")
            target = make_engine(target_url)
            check_schema_version(target)
            with target.begin() as output:
                if any(
                    output.scalar(select(func.count()).select_from(table))
                    for table in Base.metadata.sorted_tables
                ):
                    raise RuntimeError("Destination application tables must be empty")
                # Operational leases/counters are not historical evidence. In-flight work must be reviewed after cutover.
                historical = set(Legacy.metadata.tables) - {"worker_state"}
                copied = {}
                audio_by_id = {entry["id"]: entry for entry in manifest}
                for table in metadata.sorted_tables:
                    if table.name not in historical:
                        continue
                    target_table = Base.metadata.tables[table.name]
                    copied[table.name] = 0
                    for rows in connection.execute(select(table)).mappings().partitions(100):
                        batch = []
                        for original in rows:
                            row = {
                                key: value
                                for key, value in original.items()
                                if key in target_table.c
                            }
                            for column in ["created_by", "approved_by", "actor", "owner_actor"]:
                                if column in row and row[column] not in (None, "system"):
                                    if row[column] not in owner_mapping:
                                        raise RuntimeError(
                                            "A historical actor requires an explicit ownership mapping"
                                        )
                                    row[column] = owner_mapping[row[column]]
                            if table.name == "audio_chunks" and row["id"] in audio_by_id:
                                entry = audio_by_id[row["id"]]
                                old = Path(original["path"])
                                old = old if old.is_absolute() else source_audio / old
                                new = destination_audio / entry["key"]
                                new.parent.mkdir(parents=True, exist_ok=True)
                                with old.open("rb") as read, new.open("xb") as write:
                                    shutil.copyfileobj(read, write, 1024 * 1024)
                                    write.flush()
                                    os.fsync(write.fileno())
                                if checksum(new) != entry["sha256"]:
                                    raise RuntimeError("Copied audio checksum failed")
                                row["path"] = entry["key"]
                            if table.name == "jobs":
                                if row["status"] in ("pending", "running"):
                                    row["status"], row["error"] = (
                                        "failed",
                                        "Interrupted by migration; review before retrying.",
                                    )
                                row["lease_until"] = row["claim_token"] = row["run_after"] = None
                            batch.append(row)
                        output.execute(target_table.insert(), batch)
                        copied[table.name] += len(batch)
                # New approval metadata is derived from preserved immutable source content.
                with Session(bind=output) as db:
                    for mom in db.scalars(
                        select(models.Mom).where(models.Mom.status == "approved")
                    ):
                        snapshot = models.MomRevision(
                            meeting_id=mom.meeting_id,
                            data=mom.data,
                            status="approved",
                            actor=mom.approved_by or "system",
                            revision_number=mom.revision,
                            provenance={"history_cutover": True},
                        )
                        db.add(snapshot)
                        db.flush()
                        mom.approved_revision_id, mom.approved_at = snapshot.id, mom.updated_at
                    for job in db.scalars(select(models.Job)):
                        job.owner_actor = db.get(models.Meeting, job.meeting_id).created_by
                    db.flush()
                for table, count in copied.items():
                    if (
                        table != "mom_revisions"
                        and output.scalar(
                            select(func.count()).select_from(Base.metadata.tables[table])
                        )
                        != count
                    ):
                        raise RuntimeError("Destination row reconciliation failed")
            return {"mode": "copied", "rows": copied, "audio": manifest, "source_unchanged": True}
    finally:
        source.dispose()
        if target is not None:
            target.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-audio", type=Path, required=True)
    parser.add_argument(
        "--owners",
        type=Path,
        required=True,
        help="Restricted JSON source actor -> approved destination actor mapping",
    )
    parser.add_argument("--destination-audio", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = os.getenv("MIGRATION_TARGET_URL") if args.apply else None
    if args.apply and not target:
        parser.error("Set MIGRATION_TARGET_URL for the approved empty destination")
    try:
        result = migrate(
            args.source,
            args.source_audio,
            json.loads(args.owners.read_text()),
            args.destination_audio,
            target,
        )
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except Exception:
        raise SystemExit(
            "History migration stopped. Source retained. Inspect the disposable destination and use a clean destination for retry."
        ) from None
    print("History inventory/copy verified; see the restricted reconciliation report")


if __name__ == "__main__":
    main()
