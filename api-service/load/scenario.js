import http from 'k6/http';
import {check, sleep} from 'k6';
import {SharedArray} from 'k6/data';
import {sha256} from 'k6/crypto';
import {Rate} from 'k6/metrics';

const users = Number(__ENV.USERS || 100);
if (__ENV.APPROVED_TEST_TARGET !== 'true' || !__ENV.BASE_URL || !__ENV.IDENTITY_FILE)
  throw new Error('Set an approved dedicated test target and restricted per-owner identity file.');
const identities = new SharedArray('owners', () => JSON.parse(open(__ENV.IDENTITY_FILE)));
if (identities.length < users) throw new Error('Each concurrent session needs its own test identity.');
const integrity = new Rate('integrity_ok');
export const options = {
  stages: [{duration:__ENV.RAMP || '10m',target:users},{duration:__ENV.HOLD || '30m',target:users},{duration:'2m',target:0}],
  thresholds: {
    http_req_failed: ['rate<0.005'],
    'http_req_duration{operation:browse}': ['p(95)<500','p(99)<1500'],
    'http_req_duration{operation:upload}': ['p(95)<2000'],
    'http_req_duration{operation:queue}': ['p(95)<1000'],
    integrity_ok: ['rate==1'],
    checks: ['rate>0.995'],
  },
};
let mid, job, sequence = 0, lastAudio = 0, lastText = 0;
const bytes = 'synthetic-audio-payload';
const digest = sha256(bytes,'hex');
function call(path, method='GET', body, operation='browse') {
  const token=identities[__VU-1].token;
  const response=http.request(method,__ENV.BASE_URL+path,body === undefined ? null : JSON.stringify(body),{
    headers:{Authorization:`Bearer ${token}`,...(body === undefined ? {} : {'Content-Type':'application/json'})},
    tags:{operation,name:path.replace(/[0-9a-f-]{36}/g,':id')},timeout:'30s',
  });
  check(response,{'expected success':r=>r.status>=200 && r.status<300});
  return response;
}
function create() {
  const response=call('/api/meetings','POST',{title:'Synthetic load session',consent_confirmed:true},'queue');
  integrity.add(response.status===201);
  mid=response.json('id');
}
export default function () {
  const role=(__VU-1)%10;
  if (role<6) {
    call('/api/meetings?limit=50');
    sleep(10);
  } else if (role<8) {
    if (!mid) {create();call(`/api/meetings/${mid}/start`,'POST');}
    const time=Date.now();
    if (time-lastAudio>=5000) {
      const response=http.post(`${__ENV.BASE_URL}/api/meetings/${mid}/audio-chunks`,{
        sequence:String(sequence),start_ms:String(sequence*5000),end_ms:String((sequence+1)*5000),sha256:digest,
        audio:http.file(bytes,`part-${sequence}.webm`,'audio/webm'),
      },{headers:{Authorization:`Bearer ${identities[__VU-1].token}`},tags:{operation:'upload',name:'audio-chunk'},timeout:'60s'});
      integrity.add(response.status===201);
      sequence++; lastAudio=time;
    }
    if (time-lastText>=3000) {
      call(`/api/meetings/${mid}/segments`,'POST',{segments:[{client_event_id:`turn-${time}`,text:'Synthetic load transcript',start_ms:Math.max(0,(sequence-1)*5000),end_ms:sequence*5000}]},'ingest');
      lastText=time;
    }
    if (sequence>=1439) {
      call(`/api/meetings/${mid}/manifest`,'POST',{expected_chunks:sequence,recorded_through_ms:sequence*5000,finalized:true,pauses:[]});
      call(`/api/meetings/${mid}/stop`,'POST'); mid=undefined;sequence=0;
    }
    sleep(1);
  } else {
    if (!mid) {
      create();call(`/api/meetings/${mid}/start`,'POST');call(`/api/meetings/${mid}/stop`,'POST');
      call(`/api/meetings/${mid}/segments`,'POST',{segments:[{client_event_id:'summary-source',text:'Synthetic discussion without decisions.',start_ms:0,end_ms:1000}]});
      const response=call(`/api/meetings/${mid}/mom/generate`,'POST',undefined,'queue');
      job=response.json('job_id');
    }
    if (job) {
      const result=call(`/api/meetings/${mid}/jobs/${job}`).json();
      if (result.status==='completed') {integrity.add(true);job=undefined;}
      if (result.status==='failed') integrity.add(false);
    } else call(`/api/meetings/${mid}/mom`);
    sleep(3);
  }
}
