"""Checkpointed minutes generation and its atomic evidence/version commit."""

import asyncio
import hashlib
import json

from sqlalchemy import select, update

from .config import get_settings
from .models import AuditEvent, Job, Meeting, Mom, MomRevision, PersonalRecap, now
from .recovery import has_transcript_loss, require_complete_audio, transcript_segments


async def generate_minutes(mid, jid, claim, *, sessions, provider):
    with sessions() as db:
        m = db.get(Meeting, mid)
        if not m:
            raise RuntimeError("Meeting not found")
        existing = db.get(Mom, mid)
        if existing and existing.status == "approved":
            raise RuntimeError("Approved MoM cannot be overwritten")
        segs = transcript_segments(db, mid)
        require_complete_audio(db, mid)
        if not segs:
            raise RuntimeError("No finalized transcript segments. Cannot create MoM.")
        text = "\n".join(
            f"[{s.id} {s.start_ms}-{s.end_ms}ms {s.speaker_id or 'unknown'}] {s.text}" for s in segs
        )
        # Completeness cannot be inferred from segments alone. Production needs capture/gap reconciliation.
        if (
            m.capture_status == "lost"
            or m.sync_status in ("offline", "failed")
            or has_transcript_loss(db, mid)
        ):
            raise RuntimeError(
                "Capture/transcript incomplete; reconcile audio and gaps before generating MoM"
            )
        source_revision = m.evidence_revision
        title = m.title
        recap = db.get(PersonalRecap, mid)
        source_kind = "personal_recollection" if recap else "meeting_transcript"
        recap_context = ""
        if recap:
            if not recap.reviewed:
                raise RuntimeError("Review recap notes before generating a meeting memo.")
            recap_context = (
                json.dumps(
                    {
                        "source_kind": source_kind,
                        "source_label": "Based on personal recollection; not a recording of the meeting",
                        "meeting_date": recap.meeting_date,
                        "participants_as_recalled": recap.participants,
                        "timing_note": "Any audio timing belongs to the personal recap, not the original meeting.",
                    }
                )
                + "\n"
            )
            text = recap_context + text
    settings = get_settings()
    limit = settings.llm_max_input_chars
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                title,
                text,
                settings.llm_endpoint,
                settings.llm_model,
                limit,
                settings.llm_response_format,
            ]
        ).encode()
    ).hexdigest()
    checkpoint = {"fingerprint": fingerprint, "sections": {}}
    if jid:
        with sessions() as db:
            job = db.get(Job, jid)
            if job.checkpoint and job.checkpoint.get("fingerprint") == fingerprint:
                checkpoint = job.checkpoint

    async def summarize(key, content, ids, section_title):
        if key in checkpoint["sections"]:
            return checkpoint["sections"][key]
        if recap_context and not content.startswith(recap_context):
            content = recap_context + content
        result = await provider(settings, section_title, content, ids, [])
        checkpoint["sections"][key] = result
        checkpoint["retry_count"] = 0
        if jid:
            with sessions() as db:
                changed = db.execute(
                    update(Job)
                    .where(Job.id == jid, Job.status == "running", Job.claim_token == claim)
                    .values(
                        checkpoint=json.loads(json.dumps(checkpoint)),
                        completed_sections=len(checkpoint["sections"]),
                        retry_attempts=0,
                    )
                )
                db.commit()
                if changed.rowcount != 1:
                    raise RuntimeError("Job lease changed; this worker stopped safely")
        await asyncio.sleep(settings.llm_request_interval_seconds)
        return result

    # Keep successful section results across retries and worker restarts.
    if len(text) <= limit:
        result = await summarize("final", text, [s.id for s in segs], title)
    else:
        sections = []
        batch = []
        length = 0
        for segment in segs:
            line = f"[{segment.id} {segment.start_ms}-{segment.end_ms}ms {segment.speaker_id or 'unknown'}] {segment.text}"
            if len(line) > limit:
                raise RuntimeError("Individual transcript segment too large for summarization")
            if batch and length + len(line) > limit // 2:
                sections.append(batch)
                batch = []
                length = 0
            batch.append(segment)
            length += len(line) + 1
        if batch:
            sections.append(batch)
        summaries = []
        for i, part in enumerate(sections):
            chunk = "\n".join(f"[{s.id} {s.start_ms}-{s.end_ms}ms] {s.text}" for s in part)
            extracted = await summarize(
                str(i), chunk, [s.id for s in part], f"{title} / section {i + 1}"
            )
            summaries.append(extracted)
        # Structured intermediate summaries, not invented replacement transcript.
        summary_text = "\n".join(
            json.dumps({"section": i + 1, "extractions": v}) for i, v in enumerate(summaries)
        )
        if len(summary_text) > limit:
            # Conservative failure: avoid dropping evidence silently.
            raise RuntimeError(
                "Too many long-meeting summaries for final merge; adjust LLM context or add another summarization tier"
            )
        result = await summarize("final", summary_text, [s.id for s in segs], title)
        result["requires_review"] = True
    with sessions() as db:
        from .mutations import finish_job, lock_meeting

        locked = lock_meeting(db, mid)
        if locked.evidence_revision != source_revision:
            raise RuntimeError(
                "Meeting evidence changed during generation; regenerate from the latest version"
            )
        if jid:
            job = db.scalar(select(Job).where(Job.id == jid).with_for_update())
            if job.claim_token != claim or job.status != "running":
                raise RuntimeError("Job lease changed; this worker stopped safely")
        if has_transcript_loss(db, mid):
            raise RuntimeError(
                "Transcription loss was reported during generation. Recover before retrying."
            )
        require_complete_audio(db, mid)
        old = db.get(Mom, mid)
        if old and old.status == "approved":
            raise RuntimeError("Approved MoM cannot be overwritten")
        if old:
            db.add(
                MomRevision(
                    meeting_id=mid,
                    data=old.data,
                    status=old.status,
                    actor="system",
                    provenance={"schema_version": 1},
                    revision_number=old.revision,
                )
            )
            old.data = result
            old.status = "draft"
            old.updated_at = now()
            old.revision += 1
        else:
            db.add(Mom(meeting_id=mid, data=result, status="draft"))
        db.add(
            MomRevision(
                meeting_id=mid,
                data=result,
                status="draft",
                actor="system",
                source_job_id=jid,
                revision_number=old.revision if old else 0,
                provenance={
                    "schema_version": 1,
                    "prompt_version": 2,
                    "model": settings.llm_model,
                    "source_kind": source_kind,
                    "transcript_fingerprint": fingerprint,
                },
            )
        )
        db.get(Meeting, mid).status = "ready_review"
        db.get(Meeting, mid).revision += 1
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor="system",
                action="mom.generated",
                detail={"segments": len(segs)},
            )
        )
        finish_job(db, jid, claim)
        db.commit()
