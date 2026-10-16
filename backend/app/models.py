from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import String, DateTime, ForeignKey, Integer, Boolean, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

def now():return datetime.now(timezone.utc)
def uid():return str(uuid4())

class Meeting(Base):
    __tablename__='meetings'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    title:Mapped[str]=mapped_column(String(255))
    language:Mapped[str]=mapped_column(String(20),default='en-IN')
    source:Mapped[str]=mapped_column(String(50),default='microphone')
    created_by:Mapped[str]=mapped_column(String(150),default='local-user')
    consent_confirmed:Mapped[bool]=mapped_column(Boolean,default=False)
    status:Mapped[str]=mapped_column(String(50),default='created')
    capture_status:Mapped[str]=mapped_column(String(50),default='unknown')
    stt_status:Mapped[str]=mapped_column(String(50),default='disconnected')
    sync_status:Mapped[str]=mapped_column(String(50),default='pending')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    started_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    ended_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    revision:Mapped[int]=mapped_column(Integer,default=0)

class Segment(Base):
    __tablename__='segments'
    __table_args__=(UniqueConstraint('meeting_id','client_event_id',name='uq_segment_client'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    meeting_id:Mapped[str]=mapped_column(ForeignKey('meetings.id'),index=True)
    client_event_id:Mapped[str]=mapped_column(String(128))
    text:Mapped[str]=mapped_column(Text)
    start_ms:Mapped[int]=mapped_column(Integer)
    end_ms:Mapped[int]=mapped_column(Integer)
    speaker_id:Mapped[str|None]=mapped_column(String(100))
    source:Mapped[str]=mapped_column(String(30),default='live')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class AudioChunk(Base):
    __tablename__='audio_chunks'
    __table_args__=(UniqueConstraint('meeting_id','sequence',name='uq_audio_seq'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    meeting_id:Mapped[str]=mapped_column(ForeignKey('meetings.id'),index=True)
    sequence:Mapped[int]=mapped_column(Integer)
    start_ms:Mapped[int]=mapped_column(Integer)
    end_ms:Mapped[int]=mapped_column(Integer)
    sha256:Mapped[str]=mapped_column(String(64))
    media_type:Mapped[str]=mapped_column(String(100))
    path:Mapped[str]=mapped_column(Text)
    size_bytes:Mapped[int]=mapped_column(Integer)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Mom(Base):
    __tablename__='moms'
    meeting_id:Mapped[str]=mapped_column(ForeignKey('meetings.id'),primary_key=True)
    data:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String(50),default='draft')
    approved_by:Mapped[str|None]=mapped_column(String(150))
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Job(Base):
    __tablename__='jobs'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    meeting_id:Mapped[str]=mapped_column(ForeignKey('meetings.id'),index=True)
    kind:Mapped[str]=mapped_column(String(50))
    status:Mapped[str]=mapped_column(String(50),default='pending')
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    error:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Submission(Base):
    __tablename__='submissions'
    meeting_id:Mapped[str]=mapped_column(ForeignKey('meetings.id'),primary_key=True)
    status:Mapped[str]=mapped_column(String(50),default='pending')
    idempotency_key:Mapped[str]=mapped_column(String(100),unique=True)
    receipt:Mapped[dict|None]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class AuditEvent(Base):
    __tablename__='audit_events'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    meeting_id:Mapped[str|None]=mapped_column(String(36),index=True)
    actor:Mapped[str]=mapped_column(String(150))
    action:Mapped[str]=mapped_column(String(100))
    detail:Mapped[dict]=mapped_column(JSON,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
