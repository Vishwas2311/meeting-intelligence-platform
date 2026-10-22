"""System API endpoints."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import response_models as contracts
from ..database import db_session
from ..models import Meeting, WorkerState, now
from ..security import Principal, require_actor, require_admin, require_principal
from ..storage import storage_status

router = APIRouter()


@router.get("/api/health", response_model=contracts.StatusView, response_model_exclude_unset=True)
async def health():
    return {"status": "alive"}


def readiness_status(db: Session):
    from ..audio_store import check_volume
    from ..config import get_settings

    try:
        db.execute(select(Meeting.id).limit(1)).all()
        check_volume(get_settings())
        states = db.scalars(
            select(WorkerState).where(WorkerState.seen_at >= now() - timedelta(seconds=45))
        ).all()
    except (SQLAlchemyError, OSError, RuntimeError):
        raise HTTPException(503, "Ingestion dependencies unavailable") from None
    worker_ready = any(s.state == "running" for s in states)
    return {
        "status": "ready" if worker_ready else "degraded",
        "database": True,
        "worker": worker_ready,
    }


@router.get("/api/ready", response_model=contracts.ReadinessView, response_model_exclude_unset=True)
def ready(db: Session = Depends(db_session)):
    return readiness_status(db)


@router.get(
    "/api/processing-ready", response_model=contracts.StatusView, response_model_exclude_unset=True
)
def processing_ready(db: Session = Depends(db_session)):
    status = readiness_status(db)
    if not status["worker"]:
        raise HTTPException(503, "Processing worker unavailable")
    return {"status": "ready"}


@router.get(
    "/api/metrics",
    dependencies=[Depends(require_admin)],
    response_class=Response,
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
)
def metrics():
    from ..observability import metrics as export

    return Response(export(), media_type="text/plain; version=0.0.4")


@router.get(
    "/api/auth/me",
    tags=["Authentication"],
    response_model=contracts.IdentityView,
    response_model_exclude_unset=True,
)
def identity(principal: Principal = Depends(require_principal)):
    return {"actor": principal.actor, "admin": principal.admin}


@router.get(
    "/api/storage",
    dependencies=[Depends(require_admin)],
    response_model=contracts.StorageView,
    response_model_exclude_unset=True,
)
def storage():
    return storage_status()


@router.get(
    "/api/storage/allowance",
    dependencies=[Depends(require_actor)],
    response_model=contracts.AllowanceView,
    response_model_exclude_unset=True,
)
def storage_allowance():
    import shutil

    from ..audio_store import check_volume
    from ..config import get_settings

    settings = get_settings()
    check_volume(settings)
    free = shutil.disk_usage(settings.storage_dir).free
    return {"can_upload": free >= 100 * 1024**2, "low_disk": free < 1024**3}


@router.post(
    "/api/storage/backup",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
def download_backup(actor: str = Depends(require_admin)):
    import tempfile
    from pathlib import Path
    from uuid import uuid4

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    from ..database import SessionLocal
    from ..models import AuditEvent
    from ..observability import current_request
    from ..storage import backup

    with SessionLocal() as db:
        db.add(
            AuditEvent(
                actor=actor, action="backup.requested", detail={"request_id": current_request.get()}
            )
        )
        db.commit()

    target = Path(tempfile.gettempdir()) / f"meeting-backup-{uuid4()}.zip"
    try:
        backup(target)
    except RuntimeError as error:
        target.unlink(missing_ok=True)
        raise HTTPException(409, str(error)) from None
    return FileResponse(
        target,
        filename="meeting-intelligence-backup.zip",
        media_type="application/zip",
        background=BackgroundTask(target.unlink, missing_ok=True),
    )
