import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type * as SpeechSDK from 'microsoft-cognitiveservices-speech-sdk';
import {
  Mic,
  Square,
  Play,
  Pause,
  Wifi,
  WifiOff,
  UploadCloud,
  FileText,
  History,
  AlertCircle,
  CheckCircle2,
  Settings,
  ClipboardList,
  AudioLines,
  RefreshCcw,
  ShieldCheck,
  ChevronRight,
} from 'lucide-react';
import { request, sendChunk, getToken, setToken, localDevelopment, errorMessage } from './api';
import {
  saveChunk,
  pendingChunks,
  removeChunk,
  savePendingSegment,
  loadPendingSegments,
  dropPendingSegment,
} from './offline';
import { Stopwatch, formatElapsed } from './stopwatch';
import type { Meeting, Segment, Job, Minutes, SpeechToken } from './types';
import './style.css';
type Tab = 'meeting' | 'history' | 'mom' | 'settings';
function App() {
  const [tab, setTab] = useState<Tab>('meeting'),
    [apiToken, updateToken] = useState(getToken()),
    [savedToken, updateSavedToken] = useState(getToken()),
    [connectionBusy, setConnectionBusy] = useState(false),
    [meetings, setMeetings] = useState<Meeting[]>([]),
    [meeting, setMeeting] = useState<Meeting | null>(null),
    [title, setTitle] = useState('Team Discussion'),
    [source, setSource] = useState('microphone'),
    [lang, setLang] = useState('en-IN'),
    [consent, setConsent] = useState(false),
    [segments, setSegments] = useState<Segment[]>([]),
    [partial, setPartial] = useState(''),
    [mom, setMom] = useState<Minutes | null>(null),
    [editMom, setEditMom] = useState(''),
    [jobs, setJobs] = useState<Job[]>([]),
    [warning, setWarning] = useState(''),
    [notice, setNotice] = useState(''),
    [streamHealth, setStreamHealth] = useState('offline'),
    [captureHealth, setCaptureHealth] = useState('inactive'),
    [syncCount, setSyncCount] = useState(0),
    [elapsed, setElapsed] = useState(0),
    [generateBusy, setGenerateBusy] = useState(false),
    [audioAvailable, setAudioAvailable] = useState(false);
  const clock = useRef(new Stopwatch());
  const transcriptRef = useRef<HTMLDivElement>(null);
  const [followLatest, setFollowLatest] = useState(true),
    [captureBusy, setCaptureBusy] = useState(false);
  useEffect(() => {
    if (followLatest && tab === 'meeting') {
      const pane = transcriptRef.current;
      if (pane) pane.scrollTop = pane.scrollHeight;
    }
  }, [segments.length, partial, followLatest, tab]);
  async function runCapture(action: () => Promise<void>) {
    if (captureBusy) return;
    setCaptureBusy(true);
    try {
      await action();
    } finally {
      setCaptureBusy(false);
    }
  }
  const recog = useRef<SpeechSDK.SpeechRecognizer | null>(null),
    recorder = useRef<MediaRecorder | null>(null),
    media = useRef<MediaStream | null>(null),
    meetingRef = useRef<Meeting | null>(null),
    startedAt = useRef(0),
    chunkStart = useRef(0),
    seq = useRef(0),
    syncing = useRef(false),
    active = useRef(false),
    stopRequested = useRef(false),
    lastTokenRefresh = useRef<ReturnType<typeof setInterval> | null>(null);
  const selected = meeting,
    momDirty = !!mom && editMom !== JSON.stringify(mom.data, null, 2);
  function current(m: Meeting | null) {
    if (meetingRef.current?.id !== m?.id) {
      clock.current.reset();
      setElapsed(0);
      setAudioAvailable(false);
      setCaptureHealth('inactive');
      setStreamHealth('offline');
      setPartial('');
      setMom(null);
      setEditMom('');
      setJobs([]);
    }
    meetingRef.current = m;
    setMeeting(m);
    if (m) localStorage.setItem('lastMeetingId', m.id);
    else localStorage.removeItem('lastMeetingId');
  }
  function message(s: string) {
    setWarning(s);
  }
  function elapsedMs() {
    return Date.now() - startedAt.current;
  }
  async function list() {
    try {
      setMeetings(await request<Meeting[]>('/api/meetings'));
    } catch (e: unknown) {
      message(errorMessage(e));
    }
  }
  async function connect(save: boolean) {
    const token = apiToken.trim();
    setWarning('');
    setNotice('');
    if (!localDevelopment && !token) {
      message('Enter APP_API_TOKEN from backend/.env.');
      return;
    }
    setConnectionBusy(true);
    try {
      const data = await request<Meeting[]>('/api/meetings', 'GET', undefined, token);
      if (save) {
        setToken(token);
        updateToken(token);
        updateSavedToken(token);
        setMeetings(data);
      }
      setNotice(
        localDevelopment
          ? 'Backend connected using the local environment.'
          : save
            ? 'Connection verified and saved for this browser session.'
            : 'Backend reachable, authentication accepted, and database ready.',
      );
    } catch (e: unknown) {
      message(
        errorMessage(e) === 'Authentication required'
          ? localDevelopment
            ? 'Local API authentication failed. Restart the backend and frontend after updating backend/.env.'
            : 'API token rejected. Copy the APP_API_TOKEN value from backend/.env and save again.'
          : errorMessage(e),
      );
    } finally {
      setConnectionBusy(false);
    }
  }
  async function refresh(mid: string) {
    try {
      const [m, t] = await Promise.all([
        request<Meeting>(`/api/meetings/${mid}`),
        request<{ segments: Segment[] }>(`/api/meetings/${mid}/transcript`),
      ]);
      current(m);
      setSegments(t.segments || []);
      return m;
    } catch (e: unknown) {
      message(errorMessage(e));
      return null;
    }
  }
  async function pendingSync() {
    if (syncing.current || !navigator.onLine || (!localDevelopment && !getToken())) return;
    syncing.current = true;
    try {
      const items = await pendingChunks();
      setSyncCount(items.length);
      for (const c of items.sort((a, b) => a.sequence - b.sequence)) {
        try {
          await sendChunk(c.meetingId, c.sequence, c.startMs, c.endMs, c.blob);
          await removeChunk(c.id);
        } catch {
          break;
        }
      }
      if (meetingRef.current) {
        for (const s of loadPendingSegments(meetingRef.current.id)) {
          try {
            await request(`/api/meetings/${meetingRef.current.id}/segments`, 'POST', {
              segments: [s],
            });
            dropPendingSegment(meetingRef.current.id, s.client_event_id);
          } catch {
            break;
          }
        }
      }
      setSyncCount((await pendingChunks()).length);
    } catch (e: unknown) {
      message('Local recovery storage unavailable: ' + errorMessage(e));
    } finally {
      syncing.current = false;
    }
  }
  useEffect(() => {
    const fn = () => {
      if (navigator.onLine) {
        pendingSync();
        setNotice('Connection restored; synchronizing saved audio.');
      } else
        setWarning(
          'Network disconnected. Live cloud transcription unavailable; recording continues only if local capture is active.',
        );
    };
    window.addEventListener('online', fn);
    window.addEventListener('offline', fn);
    const interval = setInterval(() => {
      if (navigator.onLine) pendingSync();
    }, 5000);
    return () => {
      window.removeEventListener('online', fn);
      window.removeEventListener('offline', fn);
      clearInterval(interval);
    };
  }, []);
  useEffect(() => {
    const interval = setInterval(() => setElapsed(clock.current.read()), 100);
    return () => clearInterval(interval);
  }, []);
  useEffect(() => {
    if (localDevelopment || savedToken) {
      list();
      pendingSync();
      const id = localStorage.getItem('lastMeetingId');
      if (id) refresh(id);
    }
  }, [savedToken]);
  useEffect(() => {
    const f = (event: BeforeUnloadEvent) => {
      if (active.current) {
        event.preventDefault();
        event.returnValue = 'Recording will stop if this tab closes.';
      }
    };
    window.addEventListener('beforeunload', f);
    return () => window.removeEventListener('beforeunload', f);
  }, []);
  async function status(c: string, s: string) {
    const mid = meetingRef.current?.id;
    if (!mid) return;
    try {
      const m = await request<Meeting>(`/api/meetings/${mid}/capture-status`, 'POST', {
        capture_status: c,
        stt_status: s,
        sync_status: navigator.onLine ? 'pending' : 'offline',
      });
      current(m);
    } catch (e: unknown) {
      setNotice(`Status sync pending: ${errorMessage(e)}`);
    }
  }
  async function chooseAudio(): Promise<MediaStream> {
    if (source === 'microphone')
      return navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    if (source === 'browser-tab') {
      const capture = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      const tracks = capture.getAudioTracks();
      capture.getVideoTracks().forEach((t) => t.stop());
      if (!tracks.length)
        throw new Error(
          'Shared tab/screen provided no audio. Share a supported tab with audio enabled.',
        );
      return new MediaStream(tracks);
    }
    throw new Error(
      'Teams desktop audio needs an approved desktop agent; browser microphone cannot capture every remote participant.',
    );
  }
  async function startStreaming(stream: MediaStream) {
    const speechsdk = await import('microsoft-cognitiveservices-speech-sdk');
    const response = await request<SpeechToken>('/api/speech/token', 'POST');
    const config = speechsdk.SpeechConfig.fromAuthorizationToken(response.token, response.region);
    config.speechRecognitionLanguage = lang;
    const capture = speechsdk.AudioConfig.fromStreamInput(stream);
    const rec = new speechsdk.SpeechRecognizer(config, capture);
    recog.current = rec;
    rec.recognizing = (_s, e) => setPartial(e.result.text || '');
    rec.recognized = (_s, e) => {
      if (e.result.reason !== speechsdk.ResultReason.RecognizedSpeech || !e.result.text?.trim())
        return;
      setPartial('');
      const mid = meetingRef.current?.id;
      if (!mid) return;
      const offset = Number(e.result.offset || 0) / 10000;
      const duration = Number(e.result.duration || 0) / 10000;
      const item: Segment = {
        client_event_id: `${mid}-${Date.now()}-${crypto.randomUUID()}`,
        text: e.result.text,
        start_ms: Math.max(0, Math.round(offset)),
        end_ms: Math.max(0, Math.round(offset + duration)),
        source: 'live',
      };
      try {
        savePendingSegment(mid, item);
      } catch (error) {
        message(
          `Transcript could not be saved locally: ${errorMessage(error)}. Keep this tab open.`,
        );
      }
      setSegments((old) => [...old, item]);
      pendingSync();
    };
    rec.canceled = (_s, e) => {
      setStreamHealth('disconnected');
      if (!stopRequested.current) {
        setWarning(
          `Azure recognition interrupted (${String(e.reason)}). Audio recording may continue. Retry streaming after restoring connectivity.`,
        );
        status('active', 'disconnected');
      }
    };
    rec.sessionStarted = () => {
      setStreamHealth('connected');
      status('active', 'connected');
    };
    rec.sessionStopped = () => {
      if (!stopRequested.current) setStreamHealth('disconnected');
    };
    await new Promise<void>((resolve, reject) =>
      rec.startContinuousRecognitionAsync(
        () => resolve(),
        (e) => reject(new Error(String(e))),
      ),
    );
    lastTokenRefresh.current = setInterval(
      async () => {
        try {
          const result = await request<SpeechToken>('/api/speech/token', 'POST');
          if (recog.current) recog.current.authorizationToken = result.token;
        } catch {
          setNotice('Speech credential refresh failed; recording continues locally if available.');
        }
      },
      8 * 60 * 1000,
    );
  }
  function startRecorder(stream: MediaStream) {
    if (!MediaRecorder.isTypeSupported('audio/webm'))
      throw new Error(
        'This browser does not support audio/webm local recording; use recent Edge/Chrome.',
      );
    const r = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    recorder.current = r;
    chunkStart.current = 0;
    seq.current = 0;
    r.ondataavailable = async (e) => {
      if (!e.data.size) return;
      const mid = meetingRef.current?.id;
      if (!mid) return;
      const end = elapsedMs();
      const start = chunkStart.current;
      chunkStart.current = end;
      const c = {
        id: `${mid}:${seq.current}`,
        meetingId: mid,
        sequence: seq.current++,
        startMs: start,
        endMs: end,
        blob: e.data,
      };
      try {
        await saveChunk(c);
        setAudioAvailable(true);
        pendingSync();
      } catch (err: unknown) {
        setCaptureHealth('storage-error');
        setWarning(
          `Audio could not be saved locally: ${errorMessage(err)}. Recording may be incomplete!`,
        );
      }
    };
    r.onerror = () => {
      clock.current.pause();
      setElapsed(clock.current.read());
      setCaptureHealth('failed');
      message('Recorder failed. Stop meeting, check device/storage, and restart capture.');
    };
    r.onstop = () => {
      setCaptureHealth('stopped');
    };
    r.start(5000);
    clock.current.reset();
    clock.current.resume();
    setElapsed(0);
    setCaptureHealth('active');
    active.current = true;
  }
  async function begin() {
    if (!localDevelopment && !savedToken) {
      message('Enter your backend API token under Settings.');
      setTab('settings');
      return;
    }
    if (!consent) {
      message('Confirm recording authorization and participant notification first.');
      return;
    }
    setWarning('');
    setNotice('');
    let stream: MediaStream | null = null;
    try {
      stream = await chooseAudio();
      const m = await request<Meeting>('/api/meetings', 'POST', {
        title,
        language: lang,
        source,
        consent_confirmed: consent,
      });
      current(m);
      await request(`/api/meetings/${m.id}/start`, 'POST');
      startedAt.current = Date.now();
      stopRequested.current = false;
      media.current = stream;
      stream.getAudioTracks().forEach(
        (t) =>
          (t.onended = () => {
            clock.current.pause();
            setElapsed(clock.current.read());
            active.current = false;
            setCaptureHealth('lost');
            setWarning('Audio capture ended unexpectedly. No new audio is being recorded.');
            status('lost', 'disconnected');
          }),
      );
      startRecorder(stream);
      await status('active', 'disconnected');
      try {
        await startStreaming(stream);
      } catch (err: unknown) {
        setStreamHealth('disconnected');
        setWarning(
          `Audio is recording locally, but Azure STT is unavailable: ${errorMessage(err)}`,
        );
      }
      setNotice('Meeting started. Verify audio source is capturing all intended participants.');
    } catch (e: unknown) {
      stream?.getTracks().forEach((t) => t.stop());
      active.current = false;
      clock.current.pause();
      setElapsed(clock.current.read());
      setWarning(`Cannot start meeting: ${errorMessage(e)}`);
    }
  }
  async function stopRecognition() {
    if (lastTokenRefresh.current) clearInterval(lastTokenRefresh.current);
    lastTokenRefresh.current = null;
    const r = recog.current;
    recog.current = null;
    if (r)
      await new Promise<void>((resolve) =>
        r.stopContinuousRecognitionAsync(
          () => {
            r.close();
            resolve();
          },
          () => {
            r.close();
            resolve();
          },
        ),
      );
  }
  async function pause() {
    try {
      await stopRecognition();
      recorder.current?.pause();
      clock.current.pause();
      setElapsed(clock.current.read());
      setCaptureHealth('paused');
      setStreamHealth('paused');
      active.current = false;
      const m = await request<Meeting>(`/api/meetings/${selected?.id}/pause`, 'POST');
      current(m);
      setNotice('Recording paused. Paused audio is intentionally not captured.');
    } catch (e: unknown) {
      message(errorMessage(e));
    }
  }
  async function resume() {
    try {
      const m = await request<Meeting>(`/api/meetings/${selected?.id}/resume`, 'POST');
      current(m);
      recorder.current?.resume();
      clock.current.resume();
      setCaptureHealth('active');
      active.current = true;
      await status('active', 'disconnected');
      try {
        await startStreaming(media.current!);
      } catch (e: unknown) {
        message(`Audio resumed; Azure disconnected: ${errorMessage(e)}`);
      }
    } catch (e: unknown) {
      message(errorMessage(e));
    }
  }
  async function retryStt() {
    if (!media.current) {
      message('No active audio capture source.');
      return;
    }
    try {
      await stopRecognition();
      await startStreaming(media.current);
      setNotice(
        'Azure live transcription resumed; locally stored audio from the interruption still needs reconciliation.',
      );
    } catch (e: unknown) {
      message(`Azure reconnect failed: ${errorMessage(e)}`);
    }
  }
  async function stop() {
    stopRequested.current = true;
    active.current = false;
    try {
      await stopRecognition();
    } catch (error) {
      setWarning(`Speech shutdown failed: ${errorMessage(error)}. Local capture will still stop.`);
    }
    await new Promise<void>((resolve) => {
      const r = recorder.current;
      if (!r || r.state === 'inactive') return resolve();
      r.addEventListener('stop', () => resolve(), { once: true });
      try {
        r.stop();
      } catch {
        resolve();
      }
    });
    clock.current.pause();
    setElapsed(clock.current.read());
    media.current?.getTracks().forEach((t) => t.stop());
    media.current = null;
    setStreamHealth('stopped');
    setCaptureHealth('stopped');
    await pendingSync();
    try {
      if (selected) {
        const m = await request<Meeting>(`/api/meetings/${selected.id}/stop`, 'POST');
        current(m);
      }
      setNotice(
        'Meeting stopped. Confirm audio synchronization and transcript coverage before generating MoM.',
      );
    } catch (e: unknown) {
      message(errorMessage(e));
    }
  }
  async function generate() {
    if (!selected) return;
    setWarning('');
    setNotice('');
    setGenerateBusy(true);
    try {
      const r = await request<{ job_id: string }>(
        `/api/meetings/${selected.id}/mom/generate`,
        'POST',
      );
      setNotice(`MoM queued: ${r.job_id}. Worker must be running.`);
      setTab('mom');
      await loadMom();
    } catch (e: unknown) {
      message(errorMessage(e));
    } finally {
      setGenerateBusy(false);
    }
  }
  async function loadMom() {
    if (!meetingRef.current) return;
    const mid = meetingRef.current.id;
    try {
      const loaded = await request<Minutes>(`/api/meetings/${mid}/mom`);
      setMom(loaded);
      setEditMom(JSON.stringify(loaded.data, null, 2));
    } catch {
      setMom(null);
    }
    try {
      const latestJobs = await request<Job[]>(`/api/meetings/${mid}/jobs`);
      setJobs(latestJobs);
      if (!latestJobs.some((j) => j.status === 'pending' || j.status === 'running'))
        setNotice((old) => (old.startsWith('MoM queued:') ? '' : old));
    } catch (error) {
      message(`Could not load processing jobs: ${errorMessage(error)}`);
    }
  }
  async function saveMom() {
    if (!selected) return;
    setWarning('');
    setNotice('');
    try {
      const parsed = JSON.parse(editMom);
      const m = await request<Minutes>(`/api/meetings/${selected.id}/mom`, 'PATCH', parsed);
      setMom(m);
      setEditMom(JSON.stringify(m.data, null, 2));
      setNotice('MoM draft saved.');
    } catch (e: unknown) {
      message(`Cannot save MoM: ${errorMessage(e)}`);
    }
  }
  async function approve() {
    if (!selected) return;
    setWarning('');
    setNotice('');
    if (momDirty) {
      message('Save your draft changes before approving.');
      return;
    }
    try {
      await request(`/api/meetings/${selected.id}/mom/approve`, 'POST', { approved: true });
      await loadMom();
      setNotice('MoM approved.');
    } catch (e: unknown) {
      message(errorMessage(e));
    }
  }

  function newMeeting() {
    current(null);
    setSegments([]);
    setPartial('');
    setMom(null);
    setEditMom('');
    setJobs([]);
    clock.current.reset();
    setElapsed(0);
    setAudioAvailable(false);
    setCaptureHealth('inactive');
    setStreamHealth('offline');
    setConsent(false);
    setWarning('');
    setNotice('');
    setFollowLatest(true);
  }
  const isSetup = !selected || selected.status === 'created';
  const isRunning =
    !!selected && ['recording', 'interrupted', 'starting'].includes(selected.status);
  const isPaused = selected?.status === 'paused';
  const wordCount = segments.reduce(
    (count, s) => count + (s.text.trim().match(/\S+/g)?.length || 0),
    0,
  );
  const sourceLabel = (value: string) =>
    value === 'microphone'
      ? 'Microphone'
      : value === 'browser-tab'
        ? 'Shared tab audio'
        : 'Desktop agent';

  return (
    <div className="app">
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand">
          <span className="brand-icon">
            <AudioLines size={21} />
          </span>
          <span className="brand-name">
            MEETING<b>INTELLIGENCE</b>
          </span>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav className="menu">
          {(
            [
              ['meeting', Mic, 'Workspace'],
              ['history', History, 'Meeting History'],
              ['mom', ClipboardList, 'Meeting Minutes'],
              ['settings', Settings, 'Configuration'],
            ] as const
          ).map(([id, Icon, label]) => (
            <button
              className={tab === id ? 'nav active' : 'nav'}
              key={id}
              title={label}
              aria-label={label}
              aria-current={tab === id ? 'page' : undefined}
              onClick={() => {
                setTab(id);
                if (id === 'history') list();
                if (id === 'mom') loadMom();
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
              {tab === id && <span className="nav-indicator" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <ShieldCheck size={18} />
          <div>
            Private workspace<small>Your organization controls retention and access.</small>
          </div>
        </div>
      </aside>

      <div className="workspace-shell">
        <header className="topbar">
          <div className="page-title">
            <span className="eyebrow">
              WORKSPACE /{' '}
              {tab === 'meeting' ? 'LIVE SESSION' : tab === 'mom' ? 'MINUTES' : tab.toUpperCase()}
            </span>
            <h1>
              {tab === 'meeting'
                ? 'Live Meeting Studio'
                : tab === 'history'
                  ? 'Meeting History'
                  : tab === 'mom'
                    ? 'Meeting Minutes'
                    : 'Settings & Connections'}
            </h1>
          </div>
          <div className={'header-state ' + (captureHealth === 'active' ? 'recording' : '')}>
            <span className="dot" />
            {captureHealth === 'active'
              ? 'Recording'
              : captureHealth === 'paused'
                ? 'Recording paused'
                : 'Recorder inactive'}
          </div>
        </header>

        <main className={'workspace-main ' + (tab === 'meeting' ? 'meeting-workspace' : '')}>
          {(warning || notice) && (
            <div className="alerts">
              {warning && (
                <div role="alert" className="alert error">
                  <AlertCircle size={17} />
                  <span>{warning}</span>
                  <button onClick={() => setWarning('')} aria-label="Dismiss error">
                    Dismiss
                  </button>
                </div>
              )}
              {notice && (
                <div role="status" className="alert info">
                  <CheckCircle2 size={17} />
                  <span>{notice}</span>
                  <button onClick={() => setNotice('')} aria-label="Dismiss notification">
                    ×
                  </button>
                </div>
              )}
            </div>
          )}

          {tab === 'meeting' && (
            <div className="studio-grid">
              <section className="session-panel" aria-label="Meeting controls">
                <div className="session-top">
                  <div className="session-heading">
                    <span className="label">CURRENT SESSION</span>
                    <span className={'pill ' + (captureHealth === 'active' ? 'live' : '')}>
                      {captureHealth === 'active' ? (
                        <>
                          <span className="dot" />
                          Recording
                        </>
                      ) : isPaused ? (
                        'Paused'
                      ) : (
                        selected?.status || 'Ready'
                      )}
                    </span>
                  </div>
                  <h2 title={selected?.title || 'New meeting'}>
                    {selected?.title || 'New meeting'}
                  </h2>
                  <span className="sub">
                    {selected
                      ? `Session ${selected.id.slice(0, 8)}`
                      : 'Set up your next conversation'}
                  </span>
                  <div className="timer-block">
                    <span className="label">RECORDING TIME</span>
                    <div className="timer" role="timer" aria-label="Recording time">
                      <strong>{formatElapsed(elapsed)}</strong>
                      <span className="timer-fraction">.{Math.floor(elapsed / 100) % 10}</span>
                    </div>
                  </div>
                </div>

                {!isSetup && (
                  <div className="capture-actions">
                    {isRunning ? (
                      <>
                        <button
                          className="secondary"
                          disabled={captureBusy}
                          onClick={() => runCapture(pause)}
                        >
                          <Pause size={16} />
                          Pause
                        </button>
                        <button
                          className="primary danger"
                          disabled={captureBusy}
                          onClick={() => runCapture(stop)}
                        >
                          <Square size={15} />
                          Stop Meeting
                        </button>
                      </>
                    ) : isPaused ? (
                      <>
                        <button
                          className="primary"
                          disabled={captureBusy}
                          onClick={() => runCapture(resume)}
                        >
                          <Play size={16} />
                          Resume
                        </button>
                        <button
                          className="secondary"
                          disabled={captureBusy}
                          onClick={() => runCapture(stop)}
                        >
                          <Square size={15} />
                          Stop
                        </button>
                      </>
                    ) : (
                      <>
                        <button className="primary" disabled={generateBusy} onClick={generate}>
                          <FileText size={16} />
                          {generateBusy ? 'Queuing…' : 'Generate MoM'}
                        </button>
                        <button className="secondary" onClick={newMeeting}>
                          New Meeting
                        </button>
                      </>
                    )}
                    {isRunning && streamHealth !== 'connected' && (
                      <button
                        className="reconnect"
                        disabled={captureBusy}
                        onClick={() => runCapture(retryStt)}
                      >
                        <RefreshCcw size={14} />
                        Reconnect transcription
                      </button>
                    )}
                  </div>
                )}

                <div className="session-body">
                  {isSetup ? (
                    <div className="new-form">
                      <label>
                        Meeting title
                        <input
                          value={title}
                          onChange={(e) => setTitle(e.target.value)}
                          placeholder="e.g. Product planning"
                          maxLength={200}
                        />
                      </label>
                      <label>
                        Audio source
                        <select value={source} onChange={(e) => setSource(e.target.value)}>
                          <option value="microphone">Microphone / in-person</option>
                          <option value="browser-tab">Browser tab / screen audio</option>
                          <option value="desktop-agent" disabled>
                            Desktop agent — coming soon
                          </option>
                        </select>
                      </label>
                      <label>
                        Recognition language
                        <input
                          value={lang}
                          onChange={(e) => setLang(e.target.value)}
                          placeholder="en-IN"
                        />
                      </label>
                      <label className="agree">
                        <input
                          type="checkbox"
                          checked={consent}
                          onChange={(e) => setConsent(e.target.checked)}
                        />
                        <span>
                          I confirm recording is authorized and participants have been notified.
                        </span>
                      </label>
                      <button
                        className="primary start-button"
                        disabled={captureBusy || !title.trim() || !lang.trim()}
                        onClick={() => runCapture(begin)}
                      >
                        <Mic size={17} />
                        {captureBusy ? 'Starting…' : 'Start Meeting'}
                      </button>
                    </div>
                  ) : (
                    <div className="session-details">
                      <span className="label">SESSION DETAILS</span>
                      <div>
                        <Mic size={16} />
                        <span>
                          {sourceLabel(selected.source)}
                          <small>Audio source</small>
                        </span>
                      </div>
                      <div>
                        <AudioLines size={16} />
                        <span>
                          {selected.language}
                          <small>Recognition language</small>
                        </span>
                      </div>
                    </div>
                  )}

                  <div className="health-list">
                    <span className="label">CONNECTION & STORAGE</span>
                    <div>
                      <span>Audio capture</span>
                      <b className={captureHealth === 'active' ? 'healthy' : ''}>
                        <span className="dot" />
                        {captureHealth}
                      </b>
                    </div>
                    <div>
                      <span>Azure transcription</span>
                      <b
                        className={
                          streamHealth === 'connected'
                            ? 'healthy'
                            : streamHealth === 'disconnected'
                              ? 'attention'
                              : ''
                        }
                      >
                        <span className="dot" />
                        {streamHealth}
                      </b>
                    </div>
                    <div>
                      <span>Local audio</span>
                      <b>{audioAvailable ? 'Chunks saved' : 'Not verified'}</b>
                    </div>
                  </div>
                </div>
                <div className="session-note">
                  <ShieldCheck size={14} />
                  <span>
                    {isSetup
                      ? 'Choose a source that captures everyone.'
                      : 'Keep this tab open while recording.'}
                  </span>
                </div>
              </section>

              <section className="transcript-panel" aria-labelledby="transcript-title">
                <div className="transcript-heading">
                  <div className="transcript-title">
                    <span className="transcript-icon">
                      <FileText size={18} />
                    </span>
                    <h3 id="transcript-title">Live Transcript</h3>
                    <span className="count-badge">
                      {segments.length}
                      <span> segments</span>
                    </span>
                  </div>
                  <div className="transcript-tools">
                    <label className="follow-toggle">
                      <input
                        type="checkbox"
                        checked={followLatest}
                        onChange={(e) => setFollowLatest(e.target.checked)}
                      />
                      Follow latest
                    </label>
                    <button
                      className="icon-button"
                      title="Refresh transcript"
                      aria-label="Refresh transcript"
                      disabled={!selected}
                      onClick={() => selected && refresh(selected.id)}
                    >
                      <RefreshCcw size={16} />
                    </button>
                  </div>
                </div>
                <div
                  className="transcript"
                  ref={transcriptRef}
                  tabIndex={0}
                  role="region"
                  aria-label="Transcript content"
                  onScroll={(e) => {
                    const p = e.currentTarget;
                    if (followLatest && p.scrollHeight - p.scrollTop - p.clientHeight > 80)
                      setFollowLatest(false);
                  }}
                >
                  {segments.length === 0 && !partial ? (
                    <div className="empty transcript-empty">
                      <span className="empty-icon">
                        <Mic size={28} />
                      </span>
                      <b>
                        {isSetup
                          ? 'Your conversation, captured here'
                          : selected?.status === 'stopped'
                            ? 'No transcript captured'
                            : isPaused
                              ? 'Transcription paused'
                              : streamHealth === 'connected'
                                ? 'Listening for speech…'
                                : 'Waiting for transcription'}
                      </b>
                      <span>
                        {isSetup
                          ? 'Start a meeting to see the live transcript. Your recording controls stay right beside it.'
                          : selected?.status === 'stopped'
                            ? 'Check audio synchronization and transcription coverage before generating minutes.'
                            : isPaused
                              ? 'Resume recording when you’re ready to continue.'
                              : streamHealth === 'connected'
                                ? 'Finalized speech will appear automatically.'
                                : 'Check the transcription connection in the session panel.'}
                      </span>
                      <div className="empty-hint">
                        <span className="dot" />
                        Finalized text is saved and synchronized
                      </div>
                    </div>
                  ) : (
                    <div className="transcript-lines">
                      {segments.map((s, i) => (
                        <article className="line" key={s.client_event_id || i}>
                          <time>{formatElapsed(s.start_ms)}</time>
                          <div>
                            <small>{s.speaker_id || 'Speaker not identified'}</small>
                            <p>{s.text}</p>
                          </div>
                        </article>
                      ))}
                      {partial && (
                        <div className="line interim">
                          <time>
                            <span className="dot" />
                            LIVE
                          </time>
                          <div>
                            <small>Recognizing…</small>
                            <p>{partial}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="transcript-footer">
                  <span>
                    <span className={'dot ' + (streamHealth === 'connected' ? 'green' : '')} />
                    {partial
                      ? 'Recognizing speech…'
                      : streamHealth === 'connected'
                        ? 'Live transcription connected'
                        : isPaused
                          ? 'Transcription paused'
                          : selected?.status === 'stopped'
                            ? 'Session ended'
                            : 'Transcription idle'}
                  </span>
                  <span>{wordCount.toLocaleString()} words</span>
                </div>
              </section>
            </div>
          )}

          {tab === 'history' && (
            <section className="panel history-panel">
              <div className="panel-heading">
                <div>
                  <h3>Previous meetings</h3>
                  <p>Open a session to review its transcript and minutes.</p>
                </div>
                <button className="secondary small" onClick={list}>
                  <RefreshCcw size={14} />
                  Reload
                </button>
              </div>
              <div className="meeting-list">
                {meetings.length === 0 ? (
                  <div className="empty">
                    <History size={28} />
                    <b>No meetings yet</b>
                    <span>Your recorded sessions will appear here.</span>
                  </div>
                ) : (
                  meetings.map((m) => (
                    <button
                      className="meeting-row"
                      key={m.id}
                      onClick={async () => {
                        if (media.current && selected?.id !== m.id) {
                          message('Stop the current recording before opening another meeting.');
                          return;
                        }
                        const loaded = await refresh(m.id);
                        if (loaded) setTab('meeting');
                      }}
                    >
                      <span className="meeting-row-icon">
                        <AudioLines size={19} />
                      </span>
                      <div>
                        <b>{m.title}</b>
                        <small>
                          {sourceLabel(m.source)} · {m.language} · {m.id.slice(0, 8)}
                        </small>
                      </div>
                      <span className="pill">{m.status}</span>
                      <ChevronRight size={17} />
                    </button>
                  ))
                )}
              </div>
            </section>
          )}

          {tab === 'mom' && (
            <section className="panel minutes-panel">
              <div className="panel-heading">
                <div>
                  <h3>Draft & review</h3>
                  <p>Review and edit AI-generated minutes before approval.</p>
                </div>
                <button className="secondary small" onClick={loadMom}>
                  <RefreshCcw size={14} />
                  Refresh
                </button>
              </div>
              {!selected ? (
                <div className="empty">
                  <ClipboardList size={28} />
                  <b>Choose a meeting first</b>
                  <span>Open a session from Meeting History to review its minutes.</span>
                  <button
                    className="secondary"
                    onClick={() => {
                      setTab('history');
                      list();
                    }}
                  >
                    Browse meetings
                  </button>
                </div>
              ) : (
                <>
                  <div className="mom-status">
                    <span>{selected.title}</span>
                    <span className="pill">{mom?.status || 'Not ready'}</span>
                  </div>
                  {mom ? (
                    <>
                      <label className="editor-label" htmlFor="mom-editor">
                        Structured minutes (JSON)
                      </label>
                      <textarea
                        id="mom-editor"
                        readOnly={mom.status === 'approved'}
                        className="mom-editor"
                        value={editMom}
                        onChange={(e) => setEditMom(e.target.value)}
                        spellCheck={false}
                      />
                      {momDirty && mom.status !== 'approved' && (
                        <p>Save your changes before approving.</p>
                      )}
                      <div className="buttons">
                        <button
                          className="secondary"
                          disabled={mom.status === 'approved'}
                          onClick={saveMom}
                        >
                          Save Draft
                        </button>
                        <button
                          className="primary"
                          disabled={mom.status === 'approved' || momDirty}
                          onClick={approve}
                        >
                          Approve
                        </button>
                      </div>
                    </>
                  ) : (
                    <div className="empty">
                      <FileText size={28} />
                      <b>
                        {jobs.some((j) => j.status === 'pending' || j.status === 'running')
                          ? 'Minutes are processing'
                          : jobs.some((j) => j.status === 'failed')
                            ? 'Generation failed'
                            : 'No minutes generated yet'}
                      </b>
                      <span>
                        {jobs.some((j) => j.status === 'pending' || j.status === 'running')
                          ? 'Refresh to check progress.'
                          : jobs.some((j) => j.status === 'failed')
                            ? 'Review the processing error and retry from Workspace.'
                            : 'Stop the meeting, then choose Generate MoM in the session panel.'}
                      </span>
                    </div>
                  )}
                  <div className="jobs">
                    <h4>Processing jobs</h4>
                    {jobs.length ? (
                      jobs.map((j) => (
                        <div key={j.id}>
                          <span>
                            {j.kind} — {j.status}
                          </span>
                          <small>{j.error || `Attempts: ${j.attempts}`}</small>
                        </div>
                      ))
                    ) : (
                      <small>No background jobs yet.</small>
                    )}
                  </div>
                </>
              )}
            </section>
          )}

          {tab === 'settings' && (
            <section className="panel settings">
              <div className="settings-heading">
                <span className="settings-icon">
                  <ShieldCheck size={23} />
                </span>
                <div>
                  <h3>
                    {localDevelopment ? 'Local development connection' : 'API authentication'}
                  </h3>
                  <p>Manage your connection to the meeting service.</p>
                </div>
              </div>
              {localDevelopment ? (
                <>
                  <div className="connection-detail">
                    <span className="label">AUTHENTICATION</span>
                    <b>Loaded from your local environment</b>
                    <p>
                      API credentials load automatically from backend/.env. No token entry is
                      needed.
                    </p>
                  </div>
                  <div className="buttons">
                    <button
                      className="primary"
                      disabled={connectionBusy}
                      onClick={() => connect(false)}
                    >
                      <RefreshCcw size={16} />
                      {connectionBusy ? 'Testing…' : 'Test Backend'}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <p>
                    The token is kept in this tab’s session. Use enterprise SSO before production.
                  </p>
                  <label>
                    Backend API token
                    <input
                      type="password"
                      autoComplete="off"
                      value={apiToken}
                      onChange={(e) => updateToken(e.target.value)}
                    />
                  </label>
                  <div className="buttons">
                    <button
                      className="primary"
                      disabled={connectionBusy}
                      onClick={() => connect(true)}
                    >
                      Save Connection
                    </button>
                    <button
                      className="secondary"
                      disabled={connectionBusy}
                      onClick={() => connect(false)}
                    >
                      Test Backend
                    </button>
                  </div>
                </>
              )}
              <div className="settings-note">
                <ShieldCheck size={17} />
                <span>Azure Speech and LLM keys stay in backend/.env.</span>
              </div>
            </section>
          )}
        </main>

        <footer className="statusbar">
          <div>
            <span>
              {navigator.onLine ? <Wifi size={13} /> : <WifiOff size={13} />}
              <span>{navigator.onLine ? 'Online' : 'Offline'}</span>
            </span>
            <span className={syncCount ? 'pending-status' : ''}>
              <UploadCloud size={14} />
              {syncCount} pending audio {syncCount === 1 ? 'chunk' : 'chunks'}
            </span>
          </div>
          <span className="footer-note">
            Private meeting workspace<span className="footer-separator">·</span>Browser capture
            stops when this tab closes
          </span>
        </footer>
      </div>
    </div>
  );
}
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
