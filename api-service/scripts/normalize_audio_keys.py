"""Verify legacy absolute audio paths; --apply replaces only metadata with relative keys."""

import argparse

from sqlalchemy import select


def normalize(apply=False):
    from app.audio_store import check_volume, storage_key, storage_path
    from app.config import get_settings
    from app.database import SessionLocal
    from app.maintenance import lease
    from app.models import AudioChunk, AuditEvent
    from app.storage import digest

    check_volume(get_settings())
    count = 0
    with lease("data-maintenance", 3600) as held:
        if not held:
            raise RuntimeError("Storage maintenance is active")
        with SessionLocal() as db:
            for chunk in db.scalars(
                select(AudioChunk)
                .where(AudioChunk.storage_state == "committed")
                .execution_options(yield_per=100)
            ):
                path = storage_path(chunk.path)
                key = storage_key(path)
                if (
                    not path.is_file()
                    or path.stat().st_size != chunk.size_bytes
                    or digest(path) != chunk.sha256
                ):
                    raise RuntimeError("Legacy audio reconciliation failed; metadata retained")
                if chunk.path != key:
                    count += 1
                    if apply:
                        chunk.path = key
            if apply:
                db.add(
                    AuditEvent(
                        actor="system", action="audio.keys_normalized", detail={"count": count}
                    )
                )
                db.commit()
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(f"Verified legacy audio keys: {normalize(args.apply)}; apply={args.apply}")
    except Exception:
        raise SystemExit("Audio key reconciliation FAILED; no audio files changed") from None
