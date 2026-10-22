"""Non-billable health check for the explicitly configured production worker."""

import socket
from datetime import timedelta

from sqlalchemy import select


def main():
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import WorkerState, now

    settings = get_settings()
    with SessionLocal() as db:
        prefix = f"{settings.worker_id or 'worker'}-{socket.gethostname()[:32]}-"
        worker = db.scalar(
            select(WorkerState)
            .where(
                WorkerState.id.startswith(prefix, autoescape=True),
                WorkerState.state == "running",
                WorkerState.seen_at >= now() - timedelta(seconds=45),
            )
            .limit(1)
        )
        if not worker:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
