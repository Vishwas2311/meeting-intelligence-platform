from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class MeetingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=255)
    language: str = Field(default="en-IN", min_length=1, max_length=20)
    source: Literal["microphone", "browser-tab", "desktop-agent", "teams-transcript"] = "microphone"
    consent_confirmed: bool = False


class CaptureUpdate(BaseModel):
    capture_status: Literal["active", "paused", "stopped", "lost", "unknown"]
    stt_status: Literal["connected", "reconnecting", "disconnected", "failed"]
    sync_status: Literal["synced", "pending", "offline", "failed"] = "pending"
    transcript_incomplete: bool = False


class SegmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    client_event_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20000)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_id: str | None = Field(default=None, max_length=100)
    source: Literal["live", "recovered", "imported"] = "live"

    @model_validator(mode="after")
    def check_time(self):
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self


class SegmentBatch(BaseModel):
    segments: list[SegmentCreate] = Field(min_length=1, max_length=300)


Nonblank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Nonblank
    source_segment_ids: list[Nonblank] = Field(default_factory=list, max_length=300)


class ActionItem(Decision):
    owner: Nonblank | None = None
    due_date: Nonblank | None = None


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self


class MomData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    summary: Nonblank
    discussion_points: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)
    transcript_gaps: list[TimeRange] = Field(default_factory=list)
    requires_review: bool = True


class Approval(BaseModel):
    approved: Literal[True]


def stored_minutes(data: dict) -> dict:
    """Adapt trusted legacy records for display; never relax validation of new input."""
    result = dict(data)
    for key in ("decisions", "action_items"):
        if key not in data:
            continue
        items = []
        for original in data.get(key, []):
            sources = original.get(
                "source_segment_ids",
                original.get("segment_ids", original.get("source_segment_id", [])),
            )
            item = {
                "text": next(
                    (
                        original[name]
                        for name in ("text", "decision", "action", "description", "task")
                        if original.get(name)
                    ),
                    "",
                ),
                "source_segment_ids": [sources] if isinstance(sources, str) else sources or [],
            }
            if key == "action_items":
                item.update(
                    owner=original.get("owner") or None, due_date=original.get("due_date") or None
                )
            items.append(item)
        result[key] = items
    return result


class RecoveryRequest(BaseModel):
    # Only existing uploaded audio is accepted. Never claim a gap has been recovered without STT evidence.
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class LossEvent(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    at_ms: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=100)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_chunks: int = Field(ge=0, le=100000)
    recorded_through_ms: int = Field(ge=0, le=86400000)
    finalized: bool = False
    pauses: list[TimeRange] = Field(default_factory=list, max_length=1000)
