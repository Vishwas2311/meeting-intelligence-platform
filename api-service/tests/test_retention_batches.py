"""An old draft backlog must not starve eligible cleanup or cause per-draft queries."""

import hashlib
from datetime import timedelta

from sqlalchemy import event, func, select


def test_retention_prefilters_approvals_before_its_batch_limit(client, monkeypatch):
    from app.config import get_settings
    from app.database import SessionLocal, engine
    from app.models import AudioChunk, CaptureManifest, Meeting, Mom, now
    from app.storage import expire_audio

    root = get_settings().storage_dir
    with SessionLocal() as db:
        db.add_all(
            Meeting(
                title=f"Old unapproved {i}",
                status="stopped",
                created_at=now() - timedelta(days=120),
                ended_at=now() - timedelta(days=90),
            )
            for i in range(100)
        )
        approved = Meeting(
            title="Eligible behind old drafts",
            status="stopped",
            created_at=now() - timedelta(days=90),
            ended_at=now() - timedelta(days=60),
        )
        db.add(approved)
        db.flush()
        path = root / "eligible.bin"
        path.write_bytes(b"synthetic")
        db.add(
            AudioChunk(
                meeting_id=approved.id,
                sequence=0,
                start_ms=0,
                end_ms=1000,
                path=str(path),
                sha256=hashlib.sha256(b"synthetic").hexdigest(),
                media_type="audio/wav",
                size_bytes=9,
            )
        )
        db.add(
            CaptureManifest(
                meeting_id=approved.id,
                data={
                    "expected_chunks": 1,
                    "recorded_through_ms": 1000,
                    "finalized": True,
                    "pauses": [],
                },
            )
        )
        db.add(
            Mom(
                meeting_id=approved.id,
                status="approved",
                data={"title": "Synthetic", "summary": "Kept"},
            )
        )
        db.add_all(Meeting(title=f"Protected recent {i}") for i in range(10))
        db.commit()
    monkeypatch.setenv("AUDIO_RETENTION_ENABLED", "true")
    get_settings.cache_clear()
    reads = []

    def count_reads(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().lower().startswith("select"):
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", count_reads)
    try:
        assert expire_audio() == 1
        assert len(reads) < 30, "Unapproved histories should be filtered in one query"
    finally:
        event.remove(engine, "before_cursor_execute", count_reads)
    assert not path.exists()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Meeting)) == 111
