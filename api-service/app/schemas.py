from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class MeetingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    language: str = Field(default="auto", min_length=1, max_length=20)
    source: Literal[
        "microphone", "browser-tab", "meeting-audio", "desktop-agent", "teams-transcript"
    ] = "microphone"
    consent_confirmed: bool = False


class CaptureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_status: Literal["active", "paused", "stopped", "lost", "unknown"]
    stt_status: Literal["connected", "reconnecting", "disconnected", "failed"]
    sync_status: Literal["synced", "pending", "offline", "failed"] = "pending"
    transcript_incomplete: bool = False


class SegmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
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
    model_config = ConfigDict(extra="forbid")
    segments: list[SegmentCreate] = Field(min_length=1, max_length=300)


class SpeakerRename(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    speaker_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)


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
    discussion_points: list[Nonblank] = Field(default_factory=list, max_length=300)
    decisions: list[Decision] = Field(default_factory=list, max_length=300)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=300)
    risks: list[Nonblank] = Field(default_factory=list, max_length=300)
    unanswered_questions: list[Nonblank] = Field(default_factory=list, max_length=300)
    transcript_gaps: list[TimeRange] = Field(default_factory=list, max_length=1000)
    requires_review: bool = True

    @model_validator(mode="after")
    def bounded_document(self):
        if len(self.model_dump_json().encode()) > 1024**2:
            raise ValueError("Minutes exceed the 1 MiB document limit")
        return self


class MomEditEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: MomData
    expected_revision: int | None = Field(
        default=None, ge=0, description="Required in production; omit only for legacy local clients"
    )


class MomEditFlat(MomData):
    """Supported flat request for existing clients; prefer the explicit envelope."""

    expected_revision: int | None = Field(
        default=None, ge=0, description="Required in production; omit only for legacy local clients"
    )


class MomEdit(MomEditFlat):
    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler):
        from pydantic_core import core_schema

        return handler(
            core_schema.union_schema(
                [MomEditEnvelope.__pydantic_core_schema__, MomEditFlat.__pydantic_core_schema__]
            )
        )

    @model_validator(mode="before")
    @classmethod
    def edit_envelope(cls, value):
        if isinstance(value, dict) and "data" in value:
            if set(value) - {"data", "expected_revision"}:
                raise ValueError("Unexpected edit fields")
            if not isinstance(value["data"], dict):
                raise ValueError("Minutes data must be a JSON object")
            return {**value["data"], "expected_revision": value.get("expected_revision")}
        return value

    def minutes(self):
        return self.model_dump(exclude={"expected_revision"})


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: Literal[True]
    expected_revision: int | None = Field(
        default=None, ge=0, description="Required in production; optional for legacy local clients"
    )


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int | None = Field(
        default=None, ge=0, description="Required in production; optional for legacy local clients"
    )


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


class LossEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    at_ms: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=100)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_chunks: int = Field(ge=0, le=100000)
    recorded_through_ms: int = Field(ge=0, le=86400000)
    finalized: bool = False
    pauses: list[TimeRange] = Field(default_factory=list, max_length=1000)
