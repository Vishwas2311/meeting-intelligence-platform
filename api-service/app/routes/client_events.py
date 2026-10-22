"""Allowlisted browser failure signals without transcript, title, audio or exception text."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from .. import response_models as contracts
from ..database import db_session
from ..models import AuditEvent
from ..security import require_actor

router = APIRouter()


class ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["local_storage_failed", "recovery_failed", "capture_interrupted"]
    meeting_id: UUID | None = None


@router.post(
    "/api/client-events",
    status_code=202,
    response_model=contracts.AcceptedAck,
    response_model_exclude_unset=True,
)
def client_event(
    body: ClientEvent, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    from ..limits import consume

    consume(actor, "client_events", 30)
    if body.meeting_id:
        from ..api_common import get_meeting

        get_meeting(db, str(body.meeting_id), actor)
    db.add(
        AuditEvent(
            actor=actor,
            action="browser." + body.kind,
            meeting_id=str(body.meeting_id) if body.meeting_id else None,
            detail={},
        )
    )
    db.commit()
    return {"accepted": True}
