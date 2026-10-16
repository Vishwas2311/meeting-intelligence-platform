from typing import Literal,Any
from pydantic import BaseModel,Field,model_validator

class MeetingCreate(BaseModel):
    title:str=Field(min_length=1,max_length=255)
    language:str='en-IN'
    source:Literal['microphone','browser-tab','desktop-agent','teams-transcript']='microphone'
    consent_confirmed:bool=False

class CaptureUpdate(BaseModel):
    capture_status:Literal['active','paused','stopped','lost','unknown']
    stt_status:Literal['connected','reconnecting','disconnected','failed']
    sync_status:Literal['synced','pending','offline','failed']='pending'

class SegmentCreate(BaseModel):
    client_event_id:str=Field(min_length=1,max_length=128)
    text:str=Field(min_length=1,max_length=20000)
    start_ms:int=Field(ge=0)
    end_ms:int=Field(ge=0)
    speaker_id:str|None=None
    source:Literal['live','recovered','imported']='live'
    @model_validator(mode='after')
    def check_time(self):
        if self.end_ms<self.start_ms:raise ValueError('end_ms must be >= start_ms')
        return self

class SegmentBatch(BaseModel):
    segments:list[SegmentCreate]=Field(min_length=1,max_length=300)

class MomData(BaseModel):
    title:str
    summary:str
    discussion_points:list[str]=Field(default_factory=list)
    decisions:list[dict[str,Any]]=Field(default_factory=list)
    action_items:list[dict[str,Any]]=Field(default_factory=list)
    risks:list[str]=Field(default_factory=list)
    unanswered_questions:list[str]=Field(default_factory=list)
    transcript_gaps:list[dict[str,int]]=Field(default_factory=list)
    requires_review:bool=True

class Approval(BaseModel):
    approved:Literal[True]

class RecoveryRequest(BaseModel):
    # Only existing uploaded audio is accepted. Never claim a gap has been recovered without STT evidence.
    start_ms:int=Field(ge=0)
    end_ms:int=Field(gt=0)
