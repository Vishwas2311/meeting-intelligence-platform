import hashlib

def create(client,auth):
    r=client.post('/api/meetings',headers=auth,json={'title':'Release planning','source':'microphone','consent_confirmed':True})
    assert r.status_code==201,r.text
    return r.json()['id']

def test_auth_and_consent(client,auth):
    assert client.get('/api/meetings').status_code==401
    r=client.post('/api/meetings',headers=auth,json={'title':'X','consent_confirmed':False})
    assert r.status_code==422

def test_lifecycle_transcript_idempotency(client,auth):
    mid=create(client,auth)
    assert client.post(f'/api/meetings/{mid}/start',headers=auth).status_code==200
    r=client.post(f'/api/meetings/{mid}/capture-status',headers=auth,json={'capture_status':'active','stt_status':'connected','sync_status':'synced'})
    assert r.json()['status']=='recording'
    assert client.post(f'/api/meetings/{mid}/pause',headers=auth).json()['status']=='paused'
    assert client.post(f'/api/meetings/{mid}/resume',headers=auth).json()['status']=='starting'
    seg={'client_event_id':'first','text':'Action items are due Friday','start_ms':1000,'end_ms':4500,'source':'live'}
    for expected in (1,0):
        r=client.post(f'/api/meetings/{mid}/segments',headers=auth,json={'segments':[seg]})
        assert r.status_code==200 and r.json()['accepted']==expected
    assert len(client.get(f'/api/meetings/{mid}/transcript',headers=auth).json()['segments'])==1
    assert client.post(f'/api/meetings/{mid}/stop',headers=auth).json()['status']=='stopped'
    assert client.post(f'/api/meetings/{mid}/mom/generate',headers=auth).status_code==202
    assert client.post(f'/api/meetings/{mid}/mom/generate',headers=auth).status_code==202
    assert len(client.get(f'/api/meetings/{mid}/jobs',headers=auth).json())==1

def test_chunk_integrity(client,auth):
    mid=create(client,auth);client.post(f'/api/meetings/{mid}/start',headers=auth)
    data=b'RIFFtestWAVE';digest=hashlib.sha256(data).hexdigest()
    kwargs={'headers':auth,'data':{'sequence':'0','start_ms':'0','end_ms':'1000','sha256':digest},'files':{'audio':('part.wav',data,'audio/wav')}}
    assert client.post(f'/api/meetings/{mid}/audio-chunks',**kwargs).status_code==201
    assert client.post(f'/api/meetings/{mid}/audio-chunks',**kwargs).json()['duplicate'] is True
    wrong={**kwargs,'data':{**kwargs['data'],'sha256':'0'*64}}
    assert client.post(f'/api/meetings/{mid}/audio-chunks',**wrong).status_code==422

def test_no_unconfigured_azure_or_form_calls(client,auth):
    mid=create(client,auth)
    assert client.post('/api/speech/token',headers=auth).status_code==503
    assert client.post(f'/api/meetings/{mid}/form/submit',headers=auth).status_code==409
