"""Public response contracts. Optional legacy fields stay absent when not supplied."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from .schemas import Manifest, SpeakerRename, TimeRange


class MeetingView(BaseModel):
    id: str
    title: str
    status: str
    language: str
    source: str
    consent_confirmed: bool
    capture_status: str
    stt_status: str
    sync_status: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    revision: int
    evidence_revision: int


class SegmentView(BaseModel):
    id: str
    client_event_id: str
    text: str
    start_ms: int
    end_ms: int
    speaker_id: str | None
    source: str


class TranscriptView(BaseModel):
    meeting_id: str
    next_cursor: str | None
    evidence_revision: int
    speaker_names: dict[str, str]
    segments: list[SegmentView]


class MinutesDecision(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str
    source_segment_ids: list[str]
    owner: str | None = None
    due_date: str | None = None


class MinutesDocument(BaseModel):
    # Trusted legacy documents can lack newer fields or carry provenance fields.
    model_config = ConfigDict(extra="allow")
    title: str | None = None
    summary: str | None = None
    discussion_points: list[str] = []
    decisions: list[MinutesDecision] = []
    action_items: list[MinutesDecision] = []
    risks: list[str] = []
    unanswered_questions: list[str] = []
    transcript_gaps: list[TimeRange] = []
    requires_review: bool = True


class MinutesView(BaseModel):
    status: str
    data: MinutesDocument
    revision: int
    approved_by: str | None = None
    updated_at: datetime | None = None
    approved_revision_id: str | None = None
    approved_at: datetime | None = None


class RevisionView(BaseModel):
    id: str
    data: MinutesDocument
    status: str
    at: datetime
    provenance: dict[str, JsonValue]


class JobView(BaseModel):
    id: str
    kind: str
    status: str
    attempts: int
    error: str | None
    run_after: datetime | None
    completed_sections: int
    lease_until: datetime | None = None
    retry_attempts: int | None = None
    max_attempts: int | None = None
    completed_at: datetime | None = None


class QueueAck(BaseModel):
    job_id: str
    status: str


class SavedStatus(BaseModel):
    status: Literal["saved"]


class StatusView(BaseModel):
    status: str


class AuditView(BaseModel):
    id: str
    action: str
    at: datetime
    actor: str
    detail: dict[str, JsonValue]


class MeetingSnapshot(BaseModel):
    id: str
    meeting: MeetingView
    segments: list[SegmentView]
    speakerNames: dict[str, str]
    transcript_next_cursor: str | None
    minutes: MinutesView | None
    jobs: list[JobView]


class ChunkAck(BaseModel):
    id: str
    duplicate: bool


class ChunkView(BaseModel):
    sequence: int
    start_ms: int
    end_ms: int
    sha256: str
    size_bytes: int


class ManifestAck(BaseModel):
    saved: bool
    finalized: bool


class LossAck(BaseModel):
    id: str
    saved: bool


class SegmentAck(BaseModel):
    accepted: int
    duplicates: int


class AcceptedAck(BaseModel):
    accepted: bool


class CoverageView(BaseModel):
    meeting_id: str
    manifest: Manifest | None
    audio_timeline_gaps: list[TimeRange]
    persisted_audio_chunks: int
    persisted_audio_through_ms: int
    audio_capture_claimed_complete: bool
    transcript_incomplete: bool
    final_transcript_segments: int
    warning: str | None


class ReadinessView(BaseModel):
    status: Literal["ready", "degraded"]
    database: bool
    worker: bool


class IdentityView(BaseModel):
    actor: str
    admin: bool


class AllowanceView(BaseModel):
    can_upload: bool
    low_disk: bool


class StorageView(BaseModel):
    meetings: int
    audio_bytes: int
    disk_free_bytes: int
    low_disk: bool
    database: str
    backup_supported: bool
    backup_guidance: str
    audio_retention_enabled: bool
    audio_retention_days: int
    history_policy: str


class LanguagesView(BaseModel):
    auto_detect_languages: list[str]


class SpeechTokenView(LanguagesView):
    token: str
    region: str
    expires_in_seconds: int


class FinalizationView(BaseModel):
    status: str
    live_notes: str
    final_notes: str | None


class RecapView(BaseModel):
    id: str
    title: str
    language: str
    meeting_date: str | None
    participants: list[str]
    notes: str
    reviewed: bool
    revision: int
    audio_available: bool
    memo_status: str | None
    source_label: str
    finalization: FinalizationView | None


class ImprovementView(BaseModel):
    status: str
    error: str | None
    candidate_id: str | None
    resolved: bool
    stale: bool
    suggestions: dict[str, str]
    original: list[SegmentView]
    candidate: list[SegmentView]
    speaker_names: dict[str, str]


class RecoveryKey(BaseModel):
    generation: str
    key: str


class RecoveryKeysView(BaseModel):
    actor: str
    current_generation: str
    keys: list[RecoveryKey]


class RevisionConflict(BaseModel):
    code: Literal["revision_conflict"]
    current_revision: int


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="allow")
    loc: list[str | int]
    msg: str
    type: str


class ApiErrorView(BaseModel):
    detail: str | RevisionConflict | list[ValidationIssue]


ERROR_RESPONSES = {
    status: {"model": ApiErrorView, "description": description}
    for status, description in {
        400: "Malformed request",
        401: "Bearer authentication required; see WWW-Authenticate",
        403: "Permission or ownership denied",
        404: "Resource not found",
        409: "Lifecycle, evidence or revision conflict; reconcile before retrying",
        413: "Body or supported media/document limit exceeded",
        415: "Unsupported media",
        422: "Invalid request; expected_revision is mandatory in production",
        429: "Admission allowance exceeded; honor Retry-After",
        503: "Dependency unavailable; retry with bounded backoff",
        507: "Insufficient persistent audio space",
    }.items()
}
for status in (413, 429):
    ERROR_RESPONSES[status]["content"] = {"text/html": {"schema": {"type": "string"}}}
ERROR_RESPONSES[500] = {
    "description": "Unexpected server failure; no private exception details",
    "content": {"text/plain": {"schema": {"type": "string"}}},
}
ERROR_RESPONSES[502] = {"model": ApiErrorView, "description": "Upstream provider unavailable"}

# Retained public schema name for the speaker rename response.
SpeakerView = SpeakerRename


def document_errors(schema):
    """Errors are emitted by middleware/handlers, not success-response validators."""
    from copy import deepcopy

    error_schema = ApiErrorView.model_json_schema(ref_template="#/components/schemas/{model}")
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(error_schema.pop("$defs", {}))
    components["ApiErrorView"] = error_schema
    for item in schema["paths"].values():
        for method, operation in item.items():
            if method not in {"get", "post", "patch", "put", "delete"}:
                continue
            for code, declaration in ERROR_RESPONSES.items():
                response = deepcopy({k: v for k, v in declaration.items() if k != "model"})
                if "model" in declaration:
                    response.setdefault("content", {})["application/json"] = {
                        "schema": {"$ref": "#/components/schemas/ApiErrorView"}
                    }
                if str(code) not in operation["responses"] or code == 422:
                    operation["responses"][str(code)] = response
    return schema
