import hashlib,os
from pathlib import Path
from datetime import datetime,timezone
from fastapi import FastAPI,Depends,HTTPException,UploadFile,File,Form,Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import get_settings,Settings
from .database import Base,engine,db_session
from .models import Meeting,Segment,AudioChunk,Mom,Job,Submission,AuditEvent,now
from .schemas import MeetingCreate,CaptureUpdate,SegmentBatch,MomData,Approval,RecoveryRequest
from .security import require_actor
from .providers import azure_speech_token

app=FastAPI(title='Meeting Intelligence API',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=[o.strip() for o in get_settings().cors_origins.split(',')],allow_methods=['GET','POST','PATCH'],allow_headers=['Authorization','Content-Type','Idempotency-Key'])

@app.on_event('startup')
def initialize():
    get_settings().storage_dir.mkdir(parents=True,exist_ok=True)
    Base.metadata.create_all(bind=engine)  # dev bootstrap; production migration tool required

def audit(db,mid,actor,action,detail=None):db.add(AuditEvent(meeting_id=mid,actor=actor,action=action,detail=detail or {}))
def get_meeting(db,mid,actor):
    m=db.get(Meeting,mid)
    if not m:raise HTTPException(404,'Meeting not found')
    if m.created_by!=actor:raise HTTPException(403,'Meeting access denied')
    return m

def view(m):
    return {'id':m.id,'title':m.title,'status':m.status,'language':m.language,'source':m.source,'consent_confirmed':m.consent_confirmed,'capture_status':m.capture_status,'stt_status':m.stt_status,'sync_status':m.sync_status,'created_at':m.created_at,'started_at':m.started_at,'ended_at':m.ended_at,'revision':m.revision}

@app.get('/api/health')
def health():return {'status':'alive'}

@app.get('/api/ready')
def ready(db:Session=Depends(db_session)):
    db.execute(select(Meeting).limit(1)).all()
    return {'status':'ready','database':True}

@app.post('/api/meetings',status_code=201)
def create(body:MeetingCreate,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    if not body.consent_confirmed:raise HTTPException(422,'Recording authorization/notice must be confirmed before creating a recording session')
    m=Meeting(title=body.title,language=body.language,source=body.source,consent_confirmed=True,created_by=actor)
    db.add(m);db.flush();audit(db,m.id,actor,'meeting.created');db.commit();return view(m)

@app.get('/api/meetings')
def list_meetings(db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    return [view(m) for m in db.scalars(select(Meeting).where(Meeting.created_by==actor).order_by(Meeting.created_at.desc()).limit(100))]

@app.get('/api/meetings/{mid}')
def details(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    return view(get_meeting(db,mid,actor))

@app.post('/api/meetings/{mid}/start')
def start(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    if m.status!='created':raise HTTPException(409,'Meeting can only start from created status')
    # This starts the backend session, NOT the microphone. Client reports actual capture separately.
    m.status='starting';m.started_at=now();m.revision+=1
    audit(db,mid,actor,'meeting.start_requested');db.commit();return view(m)

@app.post('/api/meetings/{mid}/capture-status')
def update_capture(mid:str,body:CaptureUpdate,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    if m.status in ('submitted','stopped','processing'):raise HTTPException(409,'Meeting not capturing')
    if m.status=='starting' and body.capture_status=='active':m.status='recording'
    elif body.capture_status=='lost' and m.status in ('recording','paused'):m.status='interrupted'
    m.capture_status=body.capture_status;m.stt_status=body.stt_status;m.sync_status=body.sync_status;m.revision+=1
    audit(db,mid,actor,'capture.status',body.model_dump());db.commit();return view(m)

@app.post('/api/meetings/{mid}/pause')
def pause(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    if m.status!='recording':raise HTTPException(409,'Only recording meeting can be paused')
    m.status='paused';m.capture_status='paused';m.revision+=1;audit(db,mid,actor,'meeting.paused');db.commit();return view(m)

@app.post('/api/meetings/{mid}/resume')
def resume(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    if m.status not in ('paused','interrupted'):raise HTTPException(409,'Meeting is not paused/interrupted')
    m.status='starting';m.capture_status='unknown';m.revision+=1;audit(db,mid,actor,'meeting.resume_requested');db.commit();return view(m)

@app.post('/api/meetings/{mid}/stop')
def stop(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    if m.status in ('stopped','processing','ready_review','submitted'):return view(m)
    if m.status not in ('starting','recording','paused','interrupted'):raise HTTPException(409,'Meeting was not started')
    m.status='stopped';m.capture_status='stopped';m.ended_at=now();m.revision+=1
    audit(db,mid,actor,'meeting.stopped');db.commit();return view(m)

@app.post('/api/speech/token')
async def token(actor:str=Depends(require_actor),settings:Settings=Depends(get_settings)):
    return await azure_speech_token(settings)

@app.post('/api/meetings/{mid}/segments')
def segments(mid:str,body:SegmentBatch,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    if m.status in ('created','submitted'):raise HTTPException(409,'Meeting not in transcription state')
    inserted=0
    for item in body.segments:
        old=db.scalar(select(Segment).where(Segment.meeting_id==mid,Segment.client_event_id==item.client_event_id))
        if old:continue
        db.add(Segment(meeting_id=mid,**item.model_dump()));inserted+=1
    db.commit();return {'accepted':inserted,'duplicates':len(body.segments)-inserted}

@app.get('/api/meetings/{mid}/transcript')
def transcript(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    get_meeting(db,mid,actor)
    ss=db.scalars(select(Segment).where(Segment.meeting_id==mid).order_by(Segment.start_ms,Segment.created_at)).all()
    return {'meeting_id':mid,'segments':[{'id':s.id,'client_event_id':s.client_event_id,'text':s.text,'start_ms':s.start_ms,'end_ms':s.end_ms,'speaker_id':s.speaker_id,'source':s.source} for s in ss]}

@app.post('/api/meetings/{mid}/audio-chunks',status_code=201)
async def chunk(mid:str,sequence:int=Form(...),start_ms:int=Form(...),end_ms:int=Form(...),sha256:str=Form(...),audio:UploadFile=File(...),db:Session=Depends(db_session),actor:str=Depends(require_actor),settings:Settings=Depends(get_settings)):
    m=get_meeting(db,mid,actor)
    if m.status=='created':raise HTTPException(409,'Meeting not started')
    if sequence<0 or start_ms<0 or end_ms<start_ms:raise HTTPException(422,'Invalid chunk timeline')
    data=await audio.read(settings.max_audio_chunk_mb*1024*1024+1)
    if len(data)>settings.max_audio_chunk_mb*1024*1024:raise HTTPException(413,'Audio chunk too large')
    actual=hashlib.sha256(data).hexdigest()
    if actual!=sha256.lower():raise HTTPException(422,'Audio checksum mismatch')
    existing=db.scalar(select(AudioChunk).where(AudioChunk.meeting_id==mid,AudioChunk.sequence==sequence))
    if existing:
        if existing.sha256!=actual:raise HTTPException(409,'Chunk sequence already exists with different content')
        return {'id':existing.id,'duplicate':True}
    typ=audio.content_type or 'application/octet-stream'
    if typ not in ('audio/wav','audio/x-wav','audio/webm','audio/ogg','audio/mp4','application/octet-stream'):
        raise HTTPException(415,'Unsupported audio format')
    folder=settings.storage_dir/mid;folder.mkdir(parents=True,exist_ok=True)
    destination=folder/f'{sequence:09d}.bin';temp=folder/f'.{sequence:09d}.tmp'
    with temp.open('wb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(temp,destination)
    item=AudioChunk(meeting_id=mid,sequence=sequence,start_ms=start_ms,end_ms=end_ms,sha256=actual,path=str(destination),size_bytes=len(data),media_type=typ)
    db.add(item);db.flush();audit(db,mid,actor,'audio.chunk_committed',{'sequence':sequence,'size':len(data)});db.commit()
    return {'id':item.id,'duplicate':False}

@app.get('/api/meetings/{mid}/audio-chunks')
def chunks(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    get_meeting(db,mid,actor)
    cc=db.scalars(select(AudioChunk).where(AudioChunk.meeting_id==mid).order_by(AudioChunk.sequence)).all()
    return [{'sequence':c.sequence,'start_ms':c.start_ms,'end_ms':c.end_ms,'sha256':c.sha256,'size_bytes':c.size_bytes} for c in cc]

@app.post('/api/meetings/{mid}/mom/generate',status_code=202)
def generate(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    mom=db.get(Mom,mid)
    if mom and mom.status=='approved':raise HTTPException(409,'Approved MoM cannot be regenerated; create a revision workflow')
    if m.status not in ('stopped','ready_review','processing'):raise HTTPException(409,'Stop meeting before MoM generation')
    pending=db.scalar(select(Job).where(Job.meeting_id==mid,Job.kind=='generate_mom',Job.status.in_(['pending','running'])))
    if pending:return {'job_id':pending.id,'status':pending.status}
    job=Job(meeting_id=mid,kind='generate_mom');db.add(job);m.status='processing';db.flush();audit(db,mid,actor,'mom.queued');db.commit();return {'job_id':job.id,'status':job.status}

@app.get('/api/meetings/{mid}/jobs')
def jobs(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    get_meeting(db,mid,actor)
    jj=db.scalars(select(Job).where(Job.meeting_id==mid).order_by(Job.created_at.desc())).all()
    return [{'id':j.id,'kind':j.kind,'status':j.status,'attempts':j.attempts,'error':j.error} for j in jj]

@app.get('/api/meetings/{mid}/mom')
def get_mom(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    get_meeting(db,mid,actor)
    mom=db.get(Mom,mid)
    if not mom:raise HTTPException(404,'MoM not generated')
    return {'status':mom.status,'data':mom.data,'approved_by':mom.approved_by}

@app.patch('/api/meetings/{mid}/mom')
def edit_mom(mid:str,body:MomData,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    get_meeting(db,mid,actor)
    mom=db.get(Mom,mid)
    if not mom:raise HTTPException(404,'MoM not generated')
    if mom.status=='approved':raise HTTPException(409,'Approved MoM cannot be edited; create a revision workflow')
    mom.data=body.model_dump();mom.updated_at=now();audit(db,mid,actor,'mom.edited');db.commit();return {'status':mom.status,'data':mom.data}

@app.post('/api/meetings/{mid}/mom/approve')
def approve(mid:str,body:Approval,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor);mom=db.get(Mom,mid)
    if not mom:raise HTTPException(404,'MoM not generated')
    if mom.status=='approved':return {'status':'approved'}
    if mom.data.get('transcript_gaps'):raise HTTPException(409,'Transcript gaps must be resolved or explicitly acknowledged under a configured policy')
    mom.status='approved';mom.approved_by=actor;mom.updated_at=now();audit(db,mid,actor,'mom.approved');db.commit();return {'status':'approved'}

@app.post('/api/meetings/{mid}/form/preview')
def form_preview(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor);mom=db.get(Mom,mid)
    if not mom:raise HTTPException(404,'MoM not generated')
    return {'meeting_id':mid,'fields':mom.data,'target_mapping':'generic-preview-only','ready_to_submit':mom.status=='approved'}

@app.post('/api/meetings/{mid}/form/submit',status_code=202)
def form_submit(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor),settings:Settings=Depends(get_settings)):
    get_meeting(db,mid,actor);mom=db.get(Mom,mid)
    if not mom or mom.status!='approved':raise HTTPException(409,'MoM must be approved')
    if not settings.internal_form_submission_enabled:raise HTTPException(503,'Internal form submission disabled pending approved API mapping')
    previous=db.get(Submission,mid)
    if previous:return {'status':previous.status,'receipt':previous.receipt}
    sub=Submission(meeting_id=mid,status='pending',idempotency_key='mom-'+mid)
    db.add(sub);job=Job(meeting_id=mid,kind='submit_form');db.add(job);db.flush();audit(db,mid,actor,'form.queued');db.commit()
    return {'status':'pending','job_id':job.id}

@app.get('/api/meetings/{mid}/submission')
def submission(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    get_meeting(db,mid,actor);sub=db.get(Submission,mid)
    return {'status':sub.status,'receipt':sub.receipt} if sub else {'status':'not_submitted'}

@app.get('/api/meetings/{mid}/audit')
def audits(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    get_meeting(db,mid,actor)
    events=db.scalars(select(AuditEvent).where(AuditEvent.meeting_id==mid).order_by(AuditEvent.created_at)).all()
    return [{'action':e.action,'at':e.created_at,'actor':e.actor,'detail':e.detail} for e in events]

@app.get('/api/meetings/{mid}/coverage')
def coverage(mid:str,db:Session=Depends(db_session),actor:str=Depends(require_actor)):
    m=get_meeting(db,mid,actor)
    chunks=db.scalars(select(AudioChunk).where(AudioChunk.meeting_id==mid).order_by(AudioChunk.start_ms)).all()
    segs=db.scalars(select(Segment).where(Segment.meeting_id==mid).order_by(Segment.start_ms)).all()
    gaps=[];cursor=0
    for c in chunks:
        if c.start_ms>cursor+250:gaps.append({'start_ms':cursor,'end_ms':c.start_ms,'type':'audio_not_persisted'})
        cursor=max(cursor,c.end_ms)
    # Missing audio coverage is only inferred from saved chunks, never from transcript alone.
    return {'meeting_id':mid,'persisted_audio_chunks':len(chunks),'final_transcript_segments':len(segs),'audio_timeline_gaps':gaps,'persisted_audio_through_ms':cursor,'audio_capture_claimed_complete':False,'warning':'Audio gaps compare received chunks only; chunks never uploaded cannot be inferred. Check client recording state and un-synced IndexedDB audio before approval.'}
