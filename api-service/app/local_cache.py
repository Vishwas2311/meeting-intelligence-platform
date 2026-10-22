"""Return only the authenticated owner's browser recovery keys; never cache/log bodies."""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import response_models as contracts
from .config import get_settings
from .database import db_session
from .security import require_actor

router = APIRouter(prefix="/api/local-cache", tags=["Browser recovery"])


def wrapping_key(settings, key_id):
    raw = (
        settings.local_cache_kek
        if key_id == settings.local_cache_kek_id
        else settings.local_cache_old_keks.get(key_id, "")
    )
    try:
        key = base64.b64decode(raw, validate=True)
        if len(key) != 32:
            raise ValueError
        return key
    except ValueError:
        raise HTTPException(503, "Browser recovery key configuration unavailable") from None


def wrap(key, actor, generation, settings):
    nonce = os.urandom(12)
    encrypted = AESGCM(wrapping_key(settings, settings.local_cache_kek_id)).encrypt(
        nonce, key, (actor + "\0" + generation).encode()
    )
    return base64.b64encode(nonce + encrypted).decode()


@router.get("/key", response_model=contracts.RecoveryKeysView, response_model_exclude_unset=True)
def keys(
    response: Response, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    from .audio_store import actor_usage
    from .models import AuditEvent, LocalCacheKey, uid

    settings = get_settings()
    wrapping_key(settings, settings.local_cache_kek_id)
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    actor_usage(db, actor)  # Serialize first-key creation/rotation for this owner.
    rows = db.scalars(
        select(LocalCacheKey)
        .where(LocalCacheKey.actor == actor)
        .order_by(LocalCacheKey.created_at.desc(), LocalCacheKey.generation.desc())
    ).all()
    if not rows:
        generation = uid()
        row = LocalCacheKey(
            actor=actor,
            generation=generation,
            wrapping_key_id=settings.local_cache_kek_id,
            wrapped_key=wrap(os.urandom(32), actor, generation, settings),
        )
        db.add(row)
        rows = [row]
    values = []
    for row in rows:
        try:
            raw = base64.b64decode(row.wrapped_key, validate=True)
            key = AESGCM(wrapping_key(settings, row.wrapping_key_id)).decrypt(
                raw[:12], raw[12:], (actor + "\0" + row.generation).encode()
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Browser recovery key cannot be unlocked") from None
        if row.wrapping_key_id != settings.local_cache_kek_id:
            row.wrapped_key = wrap(key, actor, row.generation, settings)
            row.wrapping_key_id = settings.local_cache_kek_id
        values.append({"generation": row.generation, "key": base64.b64encode(key).decode()})
    db.add(
        AuditEvent(actor=actor, action="browser.key_unlocked", detail={"generations": len(values)})
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"actor": actor, "current_generation": values[0]["generation"], "keys": values}
