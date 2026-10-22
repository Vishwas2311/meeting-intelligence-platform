"""Frozen schema for revision 0002; never import the live application models here."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db_types import Document, NotesText, UtcTimestamp


class Base(DeclarativeBase):
    pass


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid4())


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (Index("ix_meetings_owner_created", "created_by", "created_at", "id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(20), default="auto")
    source: Mapped[str] = mapped_column(String(50), default="microphone")
    created_by: Mapped[str] = mapped_column(String(150), default="local-user")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="created")
    capture_status: Mapped[str] = mapped_column(String(50), default="unknown")
    stt_status: Mapped[str] = mapped_column(String(50), default="disconnected")
    sync_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)
    started_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    ended_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    revision: Mapped[int] = mapped_column(Integer, default=0)
    evidence_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("meeting_id", "client_event_id", name="uq_segment_client"),
        Index("ix_segments_timeline", "meeting_id", "start_ms", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    client_event_id: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    speaker_id: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(30), default="live")
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)


class PersonalRecap(Base):
    __tablename__ = "personal_recaps"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    meeting_date: Mapped[str | None] = mapped_column(String(10))
    participants: Mapped[list] = mapped_column(Document, default=list)
    notes: Mapped[str] = mapped_column(NotesText, default="", nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes_segment_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class NotesFinalization(Base):
    __tablename__ = "notes_finalizations"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36))
    live_notes: Mapped[str] = mapped_column(NotesText, default="", nullable=True)
    final_notes: Mapped[str | None] = mapped_column(Text)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)


class SpeakerName(Base):
    __tablename__ = "speaker_names"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    speaker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))


class AudioChunk(Base):
    __tablename__ = "audio_chunks"
    __table_args__ = (UniqueConstraint("meeting_id", "sequence", name="uq_audio_seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)
    storage_state: Mapped[str] = mapped_column(
        String(20), default="committed", server_default="committed"
    )
    retired_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())


class Mom(Base):
    __tablename__ = "moms"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(Document)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    approved_by: Mapped[str | None] = mapped_column(String(150))
    updated_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    approved_revision_id: Mapped[str | None] = mapped_column(String(36))
    approved_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_runnable", "status", "run_after", "created_at", "id"),
        Index("ix_jobs_lease", "status", "lease_until", "created_at", "id"),
        Index("ix_jobs_meeting_active", "meeting_id", "status", "kind"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    run_after: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    lease_until: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    claim_token: Mapped[str | None] = mapped_column(String(36))
    checkpoint: Mapped[dict | None] = mapped_column(Document)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)
    owner_actor: Mapped[str | None] = mapped_column(String(150), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    input_evidence_revision: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    deadline_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    effect_committed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
    completed_sections: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_action_time", "meeting_id", "action", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(Document, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)


class CaptureManifest(Base):
    __tablename__ = "capture_manifests"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(Document)


class MomRevision(Base):
    __tablename__ = "mom_revisions"
    __table_args__ = (Index("ix_mom_revision_time", "meeting_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    data: Mapped[dict] = mapped_column(Document)
    status: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(150))
    provenance: Mapped[dict] = mapped_column(Document, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)
    revision_number: Mapped[int | None] = mapped_column(Integer)
    source_job_id: Mapped[str | None] = mapped_column(String(36), unique=True)


class WorkerState(Base):
    __tablename__ = "worker_state"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)
    state: Mapped[str] = mapped_column(String(20), default="running", server_default="running")
    active_job: Mapped[str | None] = mapped_column(String(36))


class ActorUsage(Base):
    __tablename__ = "actor_usage"
    actor: Mapped[str] = mapped_column(String(150), primary_key=True)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_dispatched: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    actor: Mapped[str] = mapped_column(String(150), primary_key=True)
    operation: Mapped[str] = mapped_column(String(50), primary_key=True)
    window: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp(), index=True)


class ProviderState(Base):
    __tablename__ = "provider_state"
    resource: Mapped[str] = mapped_column(String(64), primary_key=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(UtcTimestamp())


class ProviderLease(Base):
    __tablename__ = "provider_leases"
    token: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    resource: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp(), index=True)


class LocalCacheKey(Base):
    __tablename__ = "local_cache_keys"
    actor: Mapped[str] = mapped_column(String(150), primary_key=True)
    generation: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    wrapped_key: Mapped[str] = mapped_column(Text)
    wrapping_key_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), default=now)


class UploadReservation(Base):
    __tablename__ = "upload_reservations"
    __table_args__ = (UniqueConstraint("meeting_id", "slot", name="uq_upload_slot"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(150), index=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    slot: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp(), index=True)


class MaintenanceLease(Base):
    __tablename__ = "maintenance_leases"
    task: Mapped[str] = mapped_column(String(64), primary_key=True)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    expires_at: Mapped[datetime | None] = mapped_column(UtcTimestamp())
