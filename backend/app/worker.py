import asyncio,logging,random
from datetime import timedelta
from sqlalchemy import update
from sqlalchemy import select
from .config import get_settings
from .database import SessionLocal,Base,engine
from .models import Meeting,Segment,Mom,Job,Submission,now,AuditEvent
from .providers import llm_mom,submit_internal_form

logging.basicConfig(level=logging.INFO)
log=logging.getLogger('meeting-worker')

def acquire_job():
    with SessionLocal() as db:
        # Single worker on SQLite. Multiple workers require PostgreSQL SELECT FOR UPDATE SKIP LOCKED.
        q=select(Job).where(Job.status=='pending').order_by(Job.created_at).limit(1)
        if not db.bind.dialect.name=='sqlite':q=q.with_for_update(skip_locked=True)
        job=db.scalar(q)
        if not job:return None
        job.status='running';job.attempts+=1;db.commit();return (job.id,job.kind,job.meeting_id)

async def execute(job_id,kind,mid):
    with SessionLocal() as db:
        m=db.get(Meeting,mid)
        if kind=='generate_mom':
            segs=db.scalars(select(Segment).where(Segment.meeting_id==mid).order_by(Segment.start_ms)).all()
            if not segs:raise RuntimeError('No finalized transcript segments. Cannot create MoM.')
            text='\n'.join(f'[{s.id} {s.start_ms}-{s.end_ms}ms {s.speaker_id or "unknown"}] {s.text}' for s in segs)
            # Completeness cannot be inferred from segments alone. Production needs capture/gap reconciliation.
            if m.capture_status=='lost' or m.sync_status in ('offline','failed'):
                raise RuntimeError('Capture/transcript incomplete; reconcile audio and gaps before generating MoM')
            title=m.title
        elif kind=='submit_form':
            mom=db.get(Mom,mid)
            if not mom or mom.status!='approved':raise RuntimeError('MoM not approved')
            title=None
        else:raise RuntimeError('Unknown job type')
    if kind=='generate_mom':
        # Hierarchical extraction when a meeting exceeds the configured LLM request budget.
        limit=get_settings().llm_max_input_chars
        if len(text)<=limit:
            result=await llm_mom(get_settings(),title,text,[s.id for s in segs],[])
        else:
            sections=[]; batch=[]; length=0
            for segment in segs:
                line=f'[{segment.id} {segment.start_ms}-{segment.end_ms}ms {segment.speaker_id or "unknown"}] {segment.text}'
                if len(line)>limit//2:raise RuntimeError('Individual transcript segment too large for summarization')
                if batch and length+len(line)>limit//2:
                    sections.append(batch);batch=[];length=0
                batch.append(segment);length+=len(line)+1
            if batch:sections.append(batch)
            summaries=[]
            for i,part in enumerate(sections):
                chunk='\n'.join(f'[{s.id} {s.start_ms}-{s.end_ms}ms] {s.text}' for s in part)
                extracted=await llm_mom(get_settings(),f'{title} / section {i+1}',chunk,[s.id for s in part],[])
                summaries.append(extracted)
            # Structured intermediate summaries, not invented replacement transcript.
            import json
            summary_text='\n'.join(json.dumps({'section':i+1,'extractions':v}) for i,v in enumerate(summaries))
            if len(summary_text)>limit:
                # Conservative failure: avoid dropping evidence silently.
                raise RuntimeError('Too many long-meeting summaries for final merge; adjust LLM context or add another summarization tier')
            result=await llm_mom(get_settings(),title,summary_text,[s.id for s in segs],[])
            result['requires_review']=True
        with SessionLocal() as db:
            old=db.get(Mom,mid)
            if old:old.data=result;old.status='draft';old.updated_at=now()
            else:db.add(Mom(meeting_id=mid,data=result,status='draft'))
            db.get(Meeting,mid).status='ready_review'
            db.add(AuditEvent(meeting_id=mid,actor='system',action='mom.generated',detail={'segments':len(segs)}))
            db.commit()
    else:
        result=await submit_internal_form(get_settings(),mom.data,mid)
        with SessionLocal() as db:
            db.get(Submission,mid).receipt=result;db.get(Submission,mid).status='submitted'
            db.get(Meeting,mid).status='submitted'
            db.add(AuditEvent(meeting_id=mid,actor='system',action='form.submitted',detail={'receipt_recorded':True}))
            db.commit()

async def main():
    Base.metadata.create_all(bind=engine)
    log.info('Worker started')
    while True:
        job=acquire_job()
        if not job:
            await asyncio.sleep(2);continue
        jid,kind,mid=job
        try:
            await execute(jid,kind,mid)
            with SessionLocal() as db:
                db.get(Job,jid).status='completed';db.commit()
        except Exception as exc:
            log.exception('Job failed: %s',jid)
            with SessionLocal() as db:
                item=db.get(Job,jid);item.error=str(exc)[:1000]
                # Non-retryable configuration / schema errors are not retried automatically.
                retriable=any(k in str(exc).lower() for k in ('timeout','temporar','502','503','504','429','connection','connecterror'))
                item.status='pending' if retriable and item.attempts<3 else 'failed'
                if kind=='generate_mom':db.get(Meeting,mid).status='processing' if item.status=='pending' else 'stopped'
                if kind=='submit_form':db.get(Submission,mid).status='failed'
                db.commit()

if __name__=='__main__':asyncio.run(main())
