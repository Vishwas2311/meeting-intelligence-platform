import { syncRecovery } from './recoverySync';
import { MeetingHistory } from './MeetingHistory';
import { loadTranscript } from './transcriptPages';
import { registerSessionDrain, finishRecorder, sessionClosing } from './sessionLifecycle';
import { AuthGate } from './AuthGate';
import { ownerKey } from './localVault';
import React, { useEffect, useMemo, useRef, useState } from 'react';
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
  Settings,
  ClipboardList,
  AudioLines,
  RefreshCcw,
  ShieldCheck,
  NotebookPen,
} from 'lucide-react';
import {
  request,
  getToken,
  setToken,
  localDevelopment,
  errorMessage,
  ApiError,
  reportClientEvent,
} from './api';
import {
  saveChunk,
  pendingRecoveryIds,
  savePendingSegment,
  pendingSegments,
  cachedMeetings,
  cacheMeeting,
  savePendingStop,
  pendingStops,
  saveLoss,
  pendingLosses,
  saveManifest,
  recordingManifests,
} from './offline';
import { usePolling } from './usePolling';
import { Stopwatch, formatElapsed } from './stopwatch';
import { speechOperation } from './speechOperation';
import { createSpeechPreparation } from './speechPreparation';
import { createTranscriber, languageLabel } from './transcriber';
import { captureAudio } from './audioCapture';
import type { AudioCapture } from './audioCapture';
import type { CachedMeeting, Meeting, Segment, Job, Minutes, SpeechToken } from './types';
import './style.css';
import { MinutesReview } from './MinutesReview';
import { StorageSettings } from './StorageSettings';
import { TranscriptEntry } from './TranscriptEntry';
import { scopedSpeaker, speakerLabels } from './speakers';
import { SpeakerRenameDialog } from './SpeakerRenameDialog';
import { useSpeechLanguages } from './useSpeechLanguages';
import { PostMeetingRecap } from './PostMeetingRecap';
import { TranscriptImprovement } from './TranscriptImprovement';
import { listRecapDrafts } from './recapDraft';
import type { LocalRecapDraft } from './recapDraft';
type Tab = 'meeting' | 'history' | 'mom' | 'settings' | 'recap';
function RecordingTimer({ clock, fallback }: { clock: Stopwatch; fallback: number }) {
  const [value, setValue] = useState(clock.read());
  useEffect(() => {
    const timer = setInterval(() => setValue(clock.read()), 100);
    return () => clearInterval(timer);
  }, [clock]);
  const shown = value || fallback;
  return (
    <div className="timer" role="timer" aria-label="Recording time">
      <strong>{formatElapsed(shown)}</strong>
      <span className="timer-fraction">.{Math.floor(shown / 100) % 10}</span>
    </div>
  );
}
function App() {
  const { label: autoLanguageLabel, description: autoLanguageDescription } = useSpeechLanguages();
  const [recapBusy, setRecapBusy] = useState(false);
  const [recapId, setRecapId] = useState<string | null>(null);
  const [localNotes, setLocalNotes] = useState<LocalRecapDraft[]>([]);
  async function notesHistory() {
    try {
      setLocalNotes(
        (await listRecapDrafts())
          .filter(
            (item) =>
              (!item.remote || item.dirty || item.interrupted) &&
              (!!item.notes.trim() || !!item.parts.length || item.dirty),
          )
          .sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || '')),
      );
    } catch (failure) {
      message(`Local notes could not be loaded: ${errorMessage(failure)}`);
    }
  }
  const [tab, setTab] = useState<Tab>('meeting'),
    [apiToken, updateToken] = useState(getToken()),
    [savedToken, updateSavedToken] = useState(getToken()),
    [connectionBusy, setConnectionBusy] = useState(false),
    [meetings, setMeetings] = useState<Meeting[]>([]),
    [meeting, setMeeting] = useState<Meeting | null>(null),
    [title, setTitle] = useState('Team Discussion'),
    [source, setSource] = useState('meeting-audio'),
    [lang, setLang] = useState('auto'),
    [consent, setConsent] = useState(false),
    [segments, setSegments] = useState<Segment[]>([]),
    [speakerNames, setSpeakerNames] = useState<Record<string, string>>({}),
    [renamingSpeaker, setRenamingSpeaker] = useState<string | null>(null),
    [partial, setPartial] = useState(''),
    [partialSpeaker, setPartialSpeaker] = useState<string | null>(null),
    [partialStart, setPartialStart] = useState(0),
    [mom, setMom] = useState<Minutes | null>(null),
    [editMom, setEditMom] = useState(''),
    [jobs, setJobs] = useState<Job[]>([]),
    [warning, setWarning] = useState(''),
    [syncWarning, setSyncWarning] = useState(''),
    [notice, setNotice] = useState(''),
    [streamHealth, setStreamHealth] = useState('offline'),
    [captureHealth, setCaptureHealth] = useState('inactive'),
    [syncCount, setSyncCount] = useState(0),
    [elapsed, setElapsed] = useState(0),
    [generateBusy, setGenerateBusy] = useState(false),
    [audioAvailable, setAudioAvailable] = useState(false);
  const [cachedCount, setCachedCount] = useState(0),
    [historyOffline, setHistoryOffline] = useState(false),
    [storageInfo, setStorageInfo] = useState('Checking local storage…');
  const [awaitingJob, setAwaitingJob] = useState<string | null>(null);
  const [historyQuery, setHistoryQuery] = useState('');
  const [historyMore, setHistoryMore] = useState(false);
  const [workerReady, setWorkerReady] = useState<boolean | null>(null);
  const [reviewingVersion, setReviewingVersion] = useState(false);
  const prepareSpeech = useMemo(
    () =>
      createSpeechPreparation(
        () => import('microsoft-cognitiveservices-speech-sdk'),
        () => request<SpeechToken>('/api/speech/token', 'POST'),
      ),
    [savedToken],
  );
  const speechGeneration = useRef(0);
  const reconnectAttempts = useRef(0);
  const [networkOnline, setNetworkOnline] = useState(navigator.onLine);
  const recordingPauses = useRef<{ start_ms: number; end_ms: number }[]>([]);
  const pausedAt = useRef<number | null>(null);
  const statusVersion = useRef(0);
  const caching = useRef(false);
  const cacheQueue = useRef<Meeting[] | null>(null);
  const listRequest = useRef(0);
  const minutesReadVersion = useRef(0);
  const pauseFlush = useRef<Promise<void>>(Promise.resolve());
  const flushed = useRef<(() => void) | null>(null);
  const clock = useRef(new Stopwatch());
  const transcriptRef = useRef<HTMLDivElement>(null);
  const captureAction = useRef(false);
  const stoppingRecognition = useRef(new Set<SpeechSDK.ConversationTranscriber>());
  const [followLatest, setFollowLatest] = useState(true),
    [captureBusy, setCaptureBusy] = useState(false);
  useEffect(() => {
    if (followLatest && tab === 'meeting') {
      const pane = transcriptRef.current;
      if (pane) pane.scrollTop = pane.scrollHeight;
    }
  }, [segments.length, partial, followLatest, tab]);
  async function runCapture(action: () => Promise<void>) {
    if (captureAction.current) return;
    captureAction.current = true;
    setCaptureBusy(true);
    try {
      await action();
    } finally {
      captureAction.current = false;
      setCaptureBusy(false);
    }
  }
  const recog = useRef<SpeechSDK.ConversationTranscriber | null>(null),
    audioCapture = useRef<AudioCapture | null>(null),
    recorder = useRef<MediaRecorder | null>(null),
    media = useRef<MediaStream | null>(null),
    meetingRef = useRef<Meeting | null>(null),
    startedAt = useRef(0),
    recorderTimeline = useRef<{ start: number; sequence: number; end?: number }>({
      start: 0,
      sequence: 0,
    }),
    syncing = useRef<Promise<void> | null>(null),
    pendingWrites = useRef<Promise<void>>(Promise.resolve()),
    storageFailed = useRef(false),
    selectionRequest = useRef(0),
    momLoading = useRef(false),
    stopRequested = useRef(false),
    lastTokenRefresh = useRef<ReturnType<typeof setInterval> | null>(null);
  const selected = meeting,
    momDirty = !!mom && editMom !== JSON.stringify(mom.data, null, 2);
  const labels = useMemo(() => speakerLabels(segments, speakerNames), [segments, speakerNames]);
  const renameSpeaker = !historyOffline && networkOnline ? setRenamingSpeaker : undefined;
  const dirty = useRef(momDirty);
  dirty.current = momDirty;
  usePolling(
    loadMom,
    tab === 'mom' && (!!awaitingJob || jobs.some((j) => ['pending', 'running'].includes(j.status))),
  );

  function canUseCache(error: unknown) {
    return !(error instanceof ApiError) || error.status >= 500;
  }
  const transcriptCache = useRef<{
    id: string;
    revision?: number;
    segments: Segment[];
    speaker_names: Record<string, string>;
  } | null>(null);
  async function snapshot(m: Meeting): Promise<Omit<CachedMeeting, 'cachedAt'>> {
    const [transcript, latestJobs] = await Promise.all([
      transcriptCache.current?.id === m.id &&
      transcriptCache.current.revision === m.evidence_revision &&
      !media.current
        ? Promise.resolve(transcriptCache.current)
        : loadTranscript(m.id).then((value) => {
            transcriptCache.current = { ...value, id: m.id, revision: m.evidence_revision };
            return value;
          }),
      request<Job[]>(`/api/meetings/${m.id}/jobs`),
    ]);
    // Read minutes after jobs so a completed job cannot leave an older draft on screen.
    const minutes = await request<Minutes>(`/api/meetings/${m.id}/mom`).catch((error) => {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    });
    if (
      m.status === 'processing' &&
      !latestJobs.some((j) => ['pending', 'running'].includes(j.status))
    )
      m = await request<Meeting>(`/api/meetings/${m.id}`);
    return {
      id: m.id,
      meeting: m,
      segments: transcript.segments,
      speakerNames: transcript.speaker_names || {},
      minutes,
      jobs: latestJobs,
    };
  }
  async function keepSnapshot(value: Omit<CachedMeeting, 'cachedAt'>) {
    try {
      await cacheMeeting(value);
      setCachedCount((await cachedMeetings()).length);
    } catch (error) {
      message(
        `Offline history could not be saved: ${errorMessage(error)}. Backend history is still retained.`,
      );
    }
  }
  function current(m: Meeting | null) {
    if (
      m &&
      meetingRef.current?.id === m.id &&
      (m.revision ?? 0) < (meetingRef.current.revision ?? 0)
    )
      return;
    if (meetingRef.current?.id !== m?.id) {
      clock.current.reset();
      setElapsed(0);
      setAudioAvailable(false);
      setCaptureHealth('inactive');
      setStreamHealth('offline');
      setPartial('');
      setPartialSpeaker(null);
      setSpeakerNames({});
      setRenamingSpeaker(null);
      setMom(null);
      setEditMom('');
      setJobs([]);
      setAwaitingJob(null);
      storageFailed.current = false;
    }
    meetingRef.current = m;
    setMeeting(m);
    if (
      m &&
      !media.current &&
      !captureAction.current &&
      ['recording', 'starting', 'paused', 'interrupted'].includes(m.status)
    )
      message(
        'This tab has no active recorder. Stop the interrupted session, then start a new recording.',
      );
    if (m) localStorage.setItem(ownerKey('lastMeetingId'), m.id);
    else localStorage.removeItem(ownerKey('lastMeetingId'));
  }
  function message(s: string) {
    setWarning(s);
  }
  function clearConnectionWarning() {
    setWarning((old) =>
      ['Backend unavailable.', 'Showing the last saved copy.', 'Could not refresh minutes:'].some(
        (prefix) => old.startsWith(prefix),
      )
        ? ''
        : old,
    );
  }
  function elapsedMs() {
    return performance.now() - startedAt.current;
  }
  async function fillOfflineCache(records: Meeting[]) {
    cacheQueue.current = records;
    if (caching.current) return;
    caching.current = true;
    try {
      while (cacheQueue.current) {
        const latest = cacheQueue.current;
        cacheQueue.current = null;
        const saved = await cachedMeetings();
        for (const m of latest.slice(0, 10)) {
          if (cacheQueue.current) break;
          const old = saved.find((item) => item.id === m.id);
          if (
            old &&
            old.meeting.revision === m.revision &&
            !['recording', 'starting', 'processing', 'paused'].includes(m.status)
          )
            continue;
          await keepSnapshot(await snapshot(m));
        }
      }
    } catch (error) {
      setStorageInfo(`Offline cache update pending: ${errorMessage(error)}`);
    } finally {
      caching.current = false;
    }
  }
  async function list(more = false) {
    const version = ++listRequest.current;
    try {
      const after = more ? meetings.at(-1)?.id : undefined;
      const records = await request<Meeting[]>(
        `/api/meetings?limit=50${after ? `&after=${encodeURIComponent(after)}` : ''}&q=${encodeURIComponent(historyQuery)}`,
      );
      if (version !== listRequest.current) return;
      setMeetings((old) =>
        more ? [...old, ...records.filter((m) => !old.some((item) => item.id === m.id))] : records,
      );
      setHistoryMore(records.length === 50);
      setHistoryOffline(false);
      clearConnectionWarning();
      if (!more && !historyQuery) void fillOfflineCache(records);
    } catch (e: unknown) {
      if (version !== listRequest.current) return;
      if (canUseCache(e)) {
        try {
          const cached = await cachedMeetings();
          if (cached.length) {
            setMeetings(cached.map((item) => item.meeting));
            setCachedCount(cached.length);
            setHistoryOffline(true);
            setWarning(
              'Backend unavailable. Showing saved offline history; edits and approvals need the backend.',
            );
            return;
          }
        } catch {
          /* The original network failure still needs to be shown. */
        }
      }
      message(errorMessage(e));
    }
  }
  async function connect(save: boolean) {
    const token = localDevelopment ? apiToken.trim() : undefined;
    setWarning('');
    setNotice('');
    if (!localDevelopment && !getToken()) {
      message('Sign in with your company account again.');
      return;
    }
    setConnectionBusy(true);
    try {
      const data = await request<Meeting[]>('/api/meetings', 'GET', undefined, token);
      if (save) {
        setToken(token || '');
        updateToken(token || '');
        updateSavedToken(token || '');
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
            ? 'Local API authentication failed. Restart the backend and frontend after updating api-service/.env.'
            : 'Your company session expired. Sign in again.'
          : errorMessage(e),
      );
    } finally {
      setConnectionBusy(false);
    }
  }
  async function refresh(mid: string, preferCached = false) {
    if (
      meetingRef.current?.id !== mid &&
      (dirty.current || generateBusy || captureAction.current)
    ) {
      message(
        'Save or discard your draft and wait for the current operation before opening another meeting.',
      );
      return null;
    }
    const version = ++selectionRequest.current;
    function showSaved(value: CachedMeeting) {
      if (version !== selectionRequest.current) return null;
      current(value.meeting);
      setSegments(value.segments);
      setSpeakerNames(value.speakerNames || {});
      if (!dirty.current) {
        setMom(value.minutes);
        setEditMom(value.minutes ? JSON.stringify(value.minutes.data, null, 2) : '');
      }
      setJobs(value.jobs);
      setHistoryOffline(true);
      setWarning('Showing the last saved copy. Connect the backend to synchronize changes.');
      return value;
    }
    try {
      if (preferCached) {
        const cached = (await cachedMeetings()).find((item) => item.id === mid);
        if (cached) return showSaved(cached);
      }
      const value = await snapshot(await request<Meeting>(`/api/meetings/${mid}`));
      if (version !== selectionRequest.current) return null;
      current(value.meeting);
      setSpeakerNames(value.speakerNames || {});
      const queued = (await pendingSegments())
        .filter((s) => s.meetingId === mid)
        .map((s) => s.segment);
      setSegments([
        ...value.segments,
        ...queued.filter(
          (s) => !value.segments.some((old) => old.client_event_id === s.client_event_id),
        ),
      ]);
      if (!dirty.current) {
        setMom(value.minutes);
        setEditMom(value.minutes ? JSON.stringify(value.minutes.data, null, 2) : '');
      }
      setJobs(value.jobs);
      setHistoryOffline(false);
      clearConnectionWarning();
      await keepSnapshot(value);
      return value;
    } catch (e: unknown) {
      if (canUseCache(e)) {
        try {
          const cached = (await cachedMeetings()).find((item) => item.id === mid);
          if (cached) return showSaved(cached);
        } catch {
          /* Keep the original failure visible if recovery storage also fails. */
        }
      }
      message(errorMessage(e));
      return null;
    }
  }
  async function pendingSync() {
    if (sessionClosing()) return;
    if (syncing.current) return syncing.current;
    if (!navigator.onLine || (!localDevelopment && !getToken())) return;
    syncing.current = syncRecovery({
      onCount: setSyncCount,
      onWarning: setSyncWarning,
      onStopped: (meeting) => {
        if (meetingRef.current?.id === meeting.id && !media.current) current(meeting);
      },
    })
      .catch((error) => {
        message('Local recovery storage unavailable: ' + errorMessage(error));
      })
      .finally(() => {
        syncing.current = null;
      });
    return syncing.current;
  }
  useEffect(() => {
    (async () => {
      try {
        for (const manifest of await recordingManifests()) {
          if (!manifest.data.finalized)
            await saveLoss(manifest.id, manifest.data.recorded_through_ms, 'recording-tab-closed');
        }
        setCachedCount((await cachedMeetings()).length);
        const persistent = await navigator.storage?.persist?.();
        const estimate = await navigator.storage?.estimate?.();
        setStorageInfo(
          `${persistent ? 'Persistent storage granted' : 'Browser storage is best effort'}${estimate?.usage !== undefined ? ` · ${(estimate.usage / 1048576).toFixed(1)} MB used` : ''}`,
        );
      } catch (error) {
        setStorageInfo(`Local storage unavailable: ${errorMessage(error)}`);
      }
    })();
  }, []);
  useEffect(() => {
    const fn = () => {
      setNetworkOnline(navigator.onLine);
      if (navigator.onLine) {
        pendingSync();
        void loadMom();
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
    if (
      streamHealth !== 'disconnected' ||
      captureHealth !== 'active' ||
      captureBusy ||
      !networkOnline ||
      reconnectAttempts.current >= 3
    )
      return;
    const timer = setTimeout(
      () => {
        reconnectAttempts.current++;
        void runCapture(retryStt);
      },
      2000 * 2 ** reconnectAttempts.current,
    );
    return () => clearTimeout(timer);
  }, [streamHealth, captureHealth, captureBusy, networkOnline]);
  useEffect(() => {
    if (localDevelopment || savedToken) {
      const warm = () => {
        if (navigator.onLine) void prepareSpeech().catch(() => {});
      };
      warm();
      const timer = setInterval(warm, 60000);
      window.addEventListener('focus', warm);
      return () => {
        clearInterval(timer);
        window.removeEventListener('focus', warm);
      };
    }
  }, [prepareSpeech, savedToken]);
  useEffect(() => {
    if (localDevelopment || savedToken) {
      list();
      pendingSync();
      const id = localStorage.getItem(ownerKey('lastMeetingId'));
      if (id)
        void refresh(id).then((loaded) => {
          if (loaded?.meeting.source === 'personal-recap') {
            setRecapId(id);
            setTab(loaded.minutes ? 'mom' : 'recap');
          }
        });
    }
  }, [savedToken]);
  useEffect(() => {
    const f = (event: BeforeUnloadEvent) => {
      if (media.current) {
        event.preventDefault();
        event.returnValue = 'Recording will stop if this tab closes.';
      }
    };
    window.addEventListener('beforeunload', f);
    return () => window.removeEventListener('beforeunload', f);
  }, []);
  useEffect(
    () => () => {
      speechGeneration.current++;
      if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop();
      media.current?.getTracks().forEach((track) => track.stop());
      recog.current?.close();
      media.current = null;
      recog.current = null;
      recorder.current = null;
    },
    [],
  );
  useEffect(
    () =>
      registerSessionDrain(async () => {
        stopRequested.current = true;
        speechGeneration.current++;
        if (lastTokenRefresh.current) clearInterval(lastTokenRefresh.current);
        const mid = meetingRef.current?.id;
        const active = recorder.current && recorder.current.state !== 'inactive';
        recorderTimeline.current.end = Math.round(elapsedMs());
        try {
          await finishRecorder(recorder.current);
        } finally {
          audioCapture.current?.stop();
          media.current?.getTracks().forEach((track) => track.stop());
          recog.current?.close();
          media.current = null;
          recorder.current = null;
        }
        await pendingWrites.current;
        if (active && mid) {
          if (storageFailed.current)
            throw new Error(
              'Local recording storage failed. Export your audio before signing out.',
            );
          await saveLoss(mid, recorderTimeline.current.end || 0, 'signed-out-during-capture');
          await savePendingStop(mid);
        }
      }),
    [],
  );
  async function status(c: string, s: string, transcriptIncomplete = false) {
    const mid = meetingRef.current?.id;
    if (!mid) return;
    const version = ++statusVersion.current;
    try {
      if (transcriptIncomplete) {
        pendingWrites.current = pendingWrites.current
          .then(() => saveLoss(mid, elapsedMs(), 'transcription-interrupted'))
          .then(() => {});
        await pendingWrites.current;
        void pendingSync();
      }
      const m = await request<Meeting>(`/api/meetings/${mid}/capture-status`, 'POST', {
        capture_status: c,
        stt_status: s,
        sync_status: navigator.onLine ? 'pending' : 'offline',
        transcript_incomplete: transcriptIncomplete,
      });
      if (
        meetingRef.current?.id !== mid ||
        version !== statusVersion.current ||
        stopRequested.current
      )
        return;
      current(m);
      setWarning((old) =>
        old.startsWith('Capture status could not reach the backend:') ? '' : old,
      );
    } catch (e: unknown) {
      message(`Capture status could not reach the backend: ${errorMessage(e)}`);
    }
  }
  async function saveSpeakerName(name: string) {
    const mid = meetingRef.current?.id;
    const speakerId = renamingSpeaker;
    if (!mid || !speakerId) throw new Error('Open a meeting and select a speaker first.');
    await pendingWrites.current;
    await pendingSync();
    const saved = await request<{ speaker_id: string; display_name: string }>(
      `/api/meetings/${mid}/speakers`,
      'PATCH',
      { speaker_id: speakerId, display_name: name },
    );
    if (meetingRef.current?.id !== mid) return;
    minutesReadVersion.current++;
    setSpeakerNames((old) => ({ ...old, [saved.speaker_id]: saved.display_name }));
    setNotice('Speaker name saved.');
    try {
      transcriptCache.current = null;
      const fresh = await request<Meeting>(`/api/meetings/${mid}`);
      if (meetingRef.current?.id !== mid) return;
      current(fresh);
      await keepSnapshot(await snapshot(fresh));
    } catch {
      message('Speaker name saved online. Refresh the meeting to update its offline copy.');
    }
  }
  async function startStreaming(stream: MediaStream) {
    const generation = ++speechGeneration.current;
    setStreamHealth('connecting');
    const { speechsdk, credentials: response } = await prepareSpeech();
    if (
      generation !== speechGeneration.current ||
      stopRequested.current ||
      media.current !== stream ||
      recorder.current?.state !== 'recording'
    )
      return;
    const rec = createTranscriber(
      speechsdk,
      response,
      meetingRef.current?.language ?? lang,
      stream,
    );
    // Azure guest IDs belong to one transcription session; reconnects must not inherit names.
    const speakerSession = crypto.randomUUID();
    const timelineOffset = elapsedMs();
    const streamingMeetingId = meetingRef.current?.id;
    recog.current = rec;
    rec.transcribing = (_s, e) => {
      if (recog.current === rec && meetingRef.current?.id === streamingMeetingId) {
        setPartial(e.result.text || '');
        setPartialSpeaker(scopedSpeaker(speakerSession, e.result.speakerId));
        setPartialStart(
          Math.max(0, Math.round(timelineOffset + Number(e.result.offset || 0) / 10000)),
        );
      }
    };
    rec.transcribed = (_s, e) => {
      if (
        meetingRef.current?.id !== streamingMeetingId ||
        (recog.current !== rec && !stoppingRecognition.current.has(rec))
      )
        return;
      if (e.result.reason !== speechsdk.ResultReason.RecognizedSpeech || !e.result.text?.trim())
        return;
      setPartial('');
      setPartialSpeaker(null);
      const mid = meetingRef.current?.id;
      if (!mid) return;
      const offset = Number(e.result.offset || 0) / 10000;
      const duration = Number(e.result.duration || 0) / 10000;
      const item: Segment = {
        client_event_id: `${mid}-${Date.now()}-${crypto.randomUUID()}`,
        text: e.result.text,
        start_ms: Math.max(0, Math.round(timelineOffset + offset)),
        end_ms: Math.max(0, Math.round(timelineOffset + offset + duration)),
        source: 'live',
        speaker_id: scopedSpeaker(speakerSession, e.result.speakerId),
      };
      pendingWrites.current = pendingWrites.current
        .then(async () => {
          await savePendingSegment(mid, item);
          void pendingSync();
        })
        .catch((error) => {
          if (!storageFailed.current)
            failCapture(
              'storage-error',
              `Transcript could not be saved locally: ${errorMessage(error)}. Recording stopped and is incomplete.`,
            );
        });
      setSegments((old) => [...old, item]);
    };
    rec.canceled = (_s, e) => {
      if (
        !stopRequested.current &&
        recog.current === rec &&
        !stoppingRecognition.current.has(rec)
      ) {
        setStreamHealth('disconnected');
        setWarning(
          `Azure recognition interrupted (${String(e.reason)}). Audio continues locally; transcription will retry automatically.`,
        );
        status('active', 'disconnected', true);
      }
    };
    rec.sessionStarted = () => {
      if (stopRequested.current || recog.current !== rec) return;
      reconnectAttempts.current = 0;
      setStreamHealth('connected');
      setWarning((old) =>
        [
          'Azure recognition interrupted',
          'Audio is recording locally, but Azure STT is unavailable:',
          'Transcription stopped unexpectedly.',
        ].some((prefix) => old.startsWith(prefix))
          ? 'Transcription reconnected. Audio from the interruption still needs transcript recovery.'
          : old,
      );
      status('active', 'connected');
    };
    rec.sessionStopped = () => {
      if (
        !stopRequested.current &&
        recog.current === rec &&
        !stoppingRecognition.current.has(rec)
      ) {
        setStreamHealth('disconnected');
        message(
          'Transcription stopped unexpectedly. Audio continues locally; transcript coverage needs recovery.',
        );
        status('active', 'disconnected', true);
      }
    };
    try {
      await speechOperation(
        (success, failure) => rec.startTranscribingAsync(success, failure),
        15000,
      );
    } catch (error) {
      rec.close();
      if (recog.current === rec) recog.current = null;
      throw error;
    }
    if (recog.current !== rec || generation !== speechGeneration.current || stopRequested.current)
      return;
    lastTokenRefresh.current = setInterval(async () => {
      try {
        const { credentials } = await prepareSpeech();
        if (recog.current === rec) rec.authorizationToken = credentials.token;
      } catch {
        setWarning('Speech credential refresh failed; recording continues locally if available.');
      }
    }, 60 * 1000);
  }
  function connectTranscription(stream: MediaStream) {
    const mid = meetingRef.current?.id;
    const generation = speechGeneration.current + 1;
    void startStreaming(stream).catch((error) => {
      if (
        generation !== speechGeneration.current ||
        stopRequested.current ||
        media.current !== stream ||
        meetingRef.current?.id !== mid ||
        recorder.current?.state !== 'recording'
      )
        return;
      setStreamHealth('disconnected');
      void status('active', 'disconnected', true);
      message(`Audio is recording locally, but Azure STT is unavailable: ${errorMessage(error)}`);
    });
  }
  function failCapture(health: string, warning: string) {
    reportClientEvent(health === 'storage-error' ? 'local_storage_failed' : 'capture_interrupted');
    storageFailed.current = true;
    stopRequested.current = true;
    clock.current.pause();
    setElapsed(clock.current.read());
    recorderTimeline.current.end = Math.round(elapsedMs());
    setCaptureHealth(health);
    setStreamHealth('disconnected');
    message(warning);
    if (recorder.current?.state !== 'inactive') {
      try {
        recorder.current?.stop();
      } catch {
        /* The device may already have stopped. */
      }
    }
    audioCapture.current?.stop();
    void status('lost', 'disconnected', true);
    void stopRecognition().catch(() => {});
  }
  useEffect(() => {
    const timer = setInterval(() => {
      if (media.current && elapsedMs() >= 7_195_000) {
        message(
          'The two-hour recording limit is reached. Stop and start a new meeting to continue.',
        );
        void stop();
      }
    }, 1000);
    return () => clearInterval(timer);
  }, []);
  function startRecorder(stream: MediaStream) {
    if (!MediaRecorder.isTypeSupported('audio/webm'))
      throw new Error(
        'This browser does not support audio/webm local recording; use recent Edge/Chrome.',
      );
    const r = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    const mid = meetingRef.current?.id;
    if (!mid) throw new Error('No meeting selected for recording.');
    const recordingStartedAt = startedAt.current;
    const timeline: { start: number; sequence: number; end?: number } = { start: 0, sequence: 0 };
    recorder.current = r;
    recorderTimeline.current = timeline;
    r.ondataavailable = (e) => {
      flushed.current?.();
      flushed.current = null;
      if (!e.data.size) return;
      const end = timeline.end ?? Math.round(performance.now() - recordingStartedAt);
      const start = timeline.start;
      timeline.start = end;
      const c = {
        id: `${mid}:${timeline.sequence}`,
        meetingId: mid,
        sequence: timeline.sequence++,
        startMs: start,
        endMs: end,
        blob: e.data,
      };
      pendingWrites.current = pendingWrites.current
        .then(async () => {
          await saveChunk(c);
          await saveManifest(mid, {
            expected_chunks: timeline.sequence,
            recorded_through_ms: end,
            finalized: false,
            pauses: [...recordingPauses.current],
          });
          if (meetingRef.current?.id === mid) setAudioAvailable(true);
          void pendingSync();
        })
        .catch((err: unknown) => {
          if (meetingRef.current?.id !== mid) {
            message(
              `An earlier recording's audio could not be saved: ${errorMessage(err)}. Keep this tab open for recovery.`,
            );
            return;
          }
          if (!storageFailed.current)
            failCapture(
              'storage-error',
              `Audio could not be saved locally: ${errorMessage(err)}. Recording stopped and is incomplete.`,
            );
        });
    };
    r.onerror = () => {
      if (recorder.current !== r) return;
      failCapture(
        'failed',
        'Recorder failed. Stop the meeting and check device/storage. The recording is incomplete.',
      );
    };
    r.onstop = () => {
      if (recorder.current !== r) return;
      setCaptureHealth((old) =>
        ['failed', 'lost', 'storage-error'].includes(old) ? old : 'stopped',
      );
    };
    r.start(5000);
    clock.current.reset();
    clock.current.resume();
    setElapsed(0);
    setCaptureHealth('active');
  }
  async function begin() {
    if (!localDevelopment && !savedToken) {
      message('Sign in with your company account again.');
      setTab('settings');
      return;
    }
    if (!consent) {
      message('Confirm recording authorization and participant notification first.');
      return;
    }
    setWarning('');
    setNotice('');
    let capture: AudioCapture | null = null;
    let created: Meeting | null = null;
    try {
      capture = await captureAudio(source);
      const stream = capture.stream;
      audioCapture.current = capture;
      const storage = await request<{ low_disk: boolean; can_upload: boolean }>(
        '/api/storage/allowance',
      );
      if (!storage.can_upload)
        throw new Error(
          'Free disk space before starting another recording. Existing history is preserved.',
        );
      if (storage.low_disk)
        message('Disk space is low. Save a backup and free space before a long meeting.');
      const m = await request<Meeting>('/api/meetings', 'POST', {
        title,
        language: lang,
        source,
        consent_confirmed: consent,
      });
      created = m;
      current(m);
      current(await request<Meeting>(`/api/meetings/${m.id}/start`, 'POST'));
      storageFailed.current = false;
      stopRequested.current = false;
      reconnectAttempts.current = 0;
      recordingPauses.current = [];
      pausedAt.current = null;
      await saveManifest(m.id, {
        expected_chunks: 0,
        recorded_through_ms: 0,
        finalized: false,
        pauses: [],
      });
      await request(`/api/meetings/${m.id}/manifest`, 'POST', {
        expected_chunks: 0,
        recorded_through_ms: 0,
        finalized: false,
        pauses: [],
      });
      media.current = stream;
      capture.onEnded = () => {
        if (media.current !== stream) return;
        failCapture(
          'lost',
          'Audio capture ended unexpectedly. The microphone or meeting audio was disconnected. No new audio is being recorded. The session is incomplete.',
        );
      };
      if (stream.getAudioTracks().some((track) => track.readyState !== 'live'))
        throw new Error('An audio source ended before recording started. Retry Start Meeting.');
      startedAt.current = performance.now();
      startRecorder(stream);
      void status('active', 'reconnecting');
      connectTranscription(stream);
      setNotice('Meeting started. Verify audio source is capturing all intended participants.');
    } catch (e: unknown) {
      capture?.stop();
      audioCapture.current = null;
      media.current = null;
      recorder.current = null;
      clock.current.pause();
      setElapsed(clock.current.read());
      if (created) {
        try {
          const latest = await request<Meeting>(`/api/meetings/${created.id}`);
          if (latest.status !== 'created')
            current(await request<Meeting>(`/api/meetings/${created.id}/stop`, 'POST'));
        } catch {
          await savePendingStop(created.id).catch(() => {});
          current({ ...created, status: 'stopped', capture_status: 'stopped' });
        }
      }
      setWarning(`Cannot start meeting: ${errorMessage(e)}`);
    }
  }
  async function stopRecognition() {
    speechGeneration.current++;
    if (lastTokenRefresh.current) clearInterval(lastTokenRefresh.current);
    lastTokenRefresh.current = null;
    const r = recog.current;
    recog.current = null;
    setPartial('');
    if (r) {
      stoppingRecognition.current.add(r);
      try {
        await speechOperation(
          (success, failure) => r.stopTranscribingAsync(success, failure),
          5000,
        );
      } finally {
        try {
          r.close();
        } finally {
          stoppingRecognition.current.delete(r);
        }
      }
    }
  }
  async function pause() {
    try {
      pausedAt.current = Math.round(elapsedMs());
      recorderTimeline.current.end = pausedAt.current;
      pauseFlush.current = new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => {
          flushed.current = null;
          reject(new Error('Paused audio flush timed out; stop and recover the recording.'));
        }, 5000);
        flushed.current = () => {
          clearTimeout(timeout);
          resolve();
        };
      });
      void pauseFlush.current.catch(() => {});
      recorder.current?.requestData();
      recorder.current?.pause();
      clock.current.pause();
      setElapsed(clock.current.read());
      setCaptureHealth('paused');
      setStreamHealth('paused');
      await stopRecognition();
      const m = await request<Meeting>(`/api/meetings/${selected?.id}/pause`, 'POST');
      current(m);
      setNotice('Recording paused. Paused audio is intentionally not captured.');
    } catch (e: unknown) {
      if (selected && recorder.current?.state === 'paused') {
        await status('paused', 'disconnected', true);
        if (meetingRef.current?.id === selected.id)
          current({ ...meetingRef.current, status: 'paused', capture_status: 'paused' });
      } else if (media.current) await status('active', 'disconnected', true);
      message(errorMessage(e));
    }
  }
  async function resume() {
    if (!media.current || !recorder.current || recorder.current.state === 'inactive') {
      message(
        'This tab has no active recorder. Stop the interrupted meeting, then start a new recording.',
      );
      return;
    }
    try {
      let m: Meeting;
      try {
        m = await request<Meeting>(`/api/meetings/${selected?.id}/resume`, 'POST');
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 409) throw error;
        // A failed pause request can leave the backend recording while the local recorder is paused.
        m = await request<Meeting>(`/api/meetings/${selected?.id}`);
        if (m.status !== 'recording') throw error;
      }
      current(m);
      await pauseFlush.current;
      await pendingWrites.current;
      const now = Math.round(elapsedMs());
      if (pausedAt.current !== null)
        recordingPauses.current.push({ start_ms: pausedAt.current, end_ms: now });
      pausedAt.current = null;
      delete recorderTimeline.current.end;
      recorderTimeline.current.start = now;
      recorder.current?.resume();
      clock.current.resume();
      setCaptureHealth('active');
      void status('active', 'reconnecting');
      connectTranscription(media.current!);
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
      connectTranscription(media.current);
    } catch (e: unknown) {
      setStreamHealth('disconnected');
      await status('active', 'disconnected', true);
      message(`Azure reconnect failed: ${errorMessage(e)}`);
    }
  }
  async function stop() {
    stopRequested.current = true;
    let unverifiedShutdown = !media.current;
    let audioUnverified = !media.current;
    const speechShutdown = stopRecognition().catch((error) => {
      unverifiedShutdown = true;
      setWarning(`Speech shutdown failed: ${errorMessage(error)}. Local capture will still stop.`);
    });
    clock.current.pause();
    setElapsed(clock.current.read());
    recorderTimeline.current.end = Math.round(elapsedMs());
    if (pausedAt.current !== null) {
      recordingPauses.current.push({
        start_ms: pausedAt.current,
        end_ms: recorderTimeline.current.end,
      });
      pausedAt.current = null;
    }
    const audioShutdown = new Promise<void>((resolve) => {
      const r = recorder.current;
      if (!r || r.state === 'inactive') return resolve();
      const finished = () => {
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(() => {
        unverifiedShutdown = true;
        audioUnverified = true;
        r.removeEventListener('stop', finished);
        message('Recorder did not finish saving its final audio chunk. The session is incomplete.');
        resolve();
      }, 5000);
      r.addEventListener('stop', finished, { once: true });
      try {
        r.stop();
      } catch {
        unverifiedShutdown = true;
        audioUnverified = true;
        finished();
      }
    });
    audioCapture.current?.stop();
    audioCapture.current = null;
    media.current = null;
    setStreamHealth('stopped');
    setCaptureHealth('stopped');
    await audioShutdown;
    await speechShutdown;
    if (unverifiedShutdown) {
      storageFailed.current = true;
      await status('lost', 'disconnected', true);
    }
    // The stop event follows dataavailable, but its asynchronous IndexedDB commit can finish later.
    await pendingWrites.current;
    if (selected && !audioUnverified) {
      await saveManifest(selected.id, {
        expected_chunks: recorderTimeline.current.sequence,
        recorded_through_ms: recorderTimeline.current.end ?? 0,
        finalized: true,
        pauses: [...recordingPauses.current],
      });
    }
    let stopSaved = false;
    if (selected) {
      try {
        await savePendingStop(selected.id);
        stopSaved = true;
      } catch (error) {
        storageFailed.current = true;
        message(`The stop request could not be saved locally: ${errorMessage(error)}`);
      }
    }
    await pendingSync();
    await pendingSync();
    try {
      if (selected && !stopSaved)
        current(await request<Meeting>(`/api/meetings/${selected.id}/stop`, 'POST'));
      if (selected && (await pendingStops()).some((item) => item.id === selected.id)) {
        current({ ...selected, status: 'stopped', capture_status: 'stopped' });
        setWarning(
          'Stopped locally. Audio, transcript and the stop request remain saved until the backend reconnects.',
        );
        return;
      }
      setNotice(
        'Meeting stopped. Confirm audio synchronization and transcript coverage before generating MoM.',
      );
      void list();
    } catch (e: unknown) {
      message(errorMessage(e));
    }
  }
  async function generate() {
    if (!selected) return;
    if (mom && mom.status !== 'outdated') {
      setTab('mom');
      return;
    }
    setWarning('');
    setNotice('');
    setGenerateBusy(true);
    try {
      const saved = await request<Minutes>(`/api/meetings/${selected.id}/mom`).catch((error) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      });
      if (saved && saved.status !== 'outdated') {
        if (meetingRef.current?.id !== selected.id) return;
        setMom(saved);
        if (!dirty.current) setEditMom(JSON.stringify(saved.data, null, 2));
        setTab('mom');
        return;
      }
      await pendingWrites.current;
      await pendingSync();
      const audio = (await pendingRecoveryIds('chunks')).filter(
        (item) => item.meetingId === selected.id,
      );
      const text = (await pendingRecoveryIds('segments')).filter(
        (item) => item.meetingId === selected.id,
      );
      if (
        audio.length ||
        text.length ||
        (await pendingLosses()).some((item) => item.meetingId === selected.id) ||
        (await recordingManifests()).some(
          (item) => item.id === selected.id && (!item.synced || !item.data.finalized),
        )
      )
        throw new Error(
          'Recording coverage is incomplete or has unsynchronized audio/transcript. Restore the backend connection and recover the transcript before retrying.',
        );
      const r = await request<{ job_id: string }>(
        `/api/meetings/${selected.id}/mom/generate`,
        'POST',
      );
      setAwaitingJob(r.job_id);
      setJobs((old) => [
        { id: r.job_id, kind: 'generate_mom', status: 'pending', attempts: 0, error: null },
        ...old.filter((j) => j.id !== r.job_id),
      ]);
      setNotice(`MoM queued: ${r.job_id}. Worker must be running.`);
      setTab('mom');
      await loadMom();
    } catch (e: unknown) {
      message(errorMessage(e));
    } finally {
      setGenerateBusy(false);
    }
  }
  async function recover() {
    if (!selected) return;
    setGenerateBusy(true);
    try {
      await pendingWrites.current;
      await pendingSync();
      if (
        (await pendingRecoveryIds('chunks')).some((c) => c.meetingId === selected.id) ||
        (await pendingLosses()).some((c) => c.meetingId === selected.id)
      )
        throw new Error('Synchronize pending audio and recovery events first.');
      const value = await request<{ job_id: string }>(
        `/api/meetings/${selected.id}/recover`,
        'POST',
      );
      setAwaitingJob(value.job_id);
      setJobs((old) => [
        {
          id: value.job_id,
          kind: 'recover_transcript',
          status: 'pending',
          attempts: 0,
          error: null,
        },
        ...old,
      ]);
      setTab('mom');
      setNotice('Recovering transcript from the saved recording. Original segments are retained.');
      await loadMom();
    } catch (error) {
      message(errorMessage(error));
    } finally {
      setGenerateBusy(false);
    }
  }
  async function loadMom() {
    if (!meetingRef.current || momLoading.current) return;
    momLoading.current = true;
    const mid = meetingRef.current.id;
    const readVersion = minutesReadVersion.current;
    try {
      const value = await snapshot(await request<Meeting>(`/api/meetings/${mid}`));
      if (meetingRef.current?.id !== mid || readVersion !== minutesReadVersion.current) return;
      current(value.meeting);
      if (!dirty.current) setMom(value.minutes);
      if (!dirty.current) {
        setEditMom(value.minutes ? JSON.stringify(value.minutes.data, null, 2) : '');
      }
      setJobs(value.jobs);
      setAwaitingJob((id) =>
        id && value.jobs.some((j) => j.id === id && ['pending', 'running'].includes(j.status))
          ? id
          : null,
      );
      if (!media.current) setSegments(value.segments);
      setSpeakerNames(value.speakerNames || {});
      const readiness = await request<{ worker: boolean }>('/api/ready').catch(() => null);
      if (readiness) setWorkerReady(readiness.worker);
      setHistoryOffline(false);
      clearConnectionWarning();
      if (
        !value.minutes &&
        !value.jobs.some((j) => j.status === 'pending' || j.status === 'running')
      )
        setNotice(
          value.jobs.some((j) => j.kind === 'recover_transcript' && j.status === 'completed')
            ? 'Transcript recovered and verified. Generate minutes when ready.'
            : '',
        );
      await keepSnapshot(value);
    } catch (error) {
      if (meetingRef.current?.id !== mid || readVersion !== minutesReadVersion.current) return;
      if (canUseCache(error)) setHistoryOffline(true);
      message(`Could not refresh minutes: ${errorMessage(error)}. Your current draft was kept.`);
    } finally {
      momLoading.current = false;
    }
  }
  async function saveMom() {
    if (!selected) return;
    setWarning('');
    setNotice('');
    try {
      const parsed = JSON.parse(editMom);
      const m = await request<Minutes>(`/api/meetings/${selected.id}/mom`, 'PATCH', {
        expected_revision: mom?.revision ?? 0,
        data: parsed,
      });
      if (meetingRef.current?.id !== selected.id) return;
      minutesReadVersion.current++;
      setMom(m);
      setEditMom(JSON.stringify(m.data, null, 2));
      setNotice('MoM draft saved.');
      await keepSnapshot(await snapshot(selected));
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
      await request(`/api/meetings/${selected.id}/mom/approve`, 'POST', {
        approved: true,
        expected_revision: mom?.revision ?? 0,
      });
      if (meetingRef.current?.id !== selected.id) return;
      minutesReadVersion.current++;
      setMom((old) => (old ? { ...old, status: 'approved' } : old));
      await loadMom();
      setNotice('MoM approved.');
    } catch (e: unknown) {
      message(errorMessage(e));
    }
  }

  function newMeeting() {
    if (dirty.current || generateBusy || captureAction.current) {
      message(
        'Save or discard your draft and wait for the current operation before starting another meeting.',
      );
      return;
    }
    selectionRequest.current++;
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
  const historicDuration =
    !elapsed && selected?.started_at && selected.ended_at
      ? Math.max(0, new Date(selected.ended_at).getTime() - new Date(selected.started_at).getTime())
      : 0;
  const wordCount = segments.reduce(
    (count, s) => count + (s.text.trim().match(/\S+/g)?.length || 0),
    0,
  );
  const sourceLabel = (value: string) =>
    value === 'personal-recap'
      ? 'Meeting Notes'
      : value === 'meeting-audio'
        ? 'Microphone + meeting audio'
        : value === 'microphone'
          ? 'Microphone'
          : value === 'browser-tab'
            ? 'Shared tab audio'
            : 'Desktop agent';
  const processingJobs = jobs.filter(
    (job) => ['pending', 'running'].includes(job.status) || (!mom && job.status === 'failed'),
  );

  return (
    <div className={tab === 'mom' ? 'app minutes-app' : 'app'}>
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
              ['recap', NotebookPen, 'Meeting Notes'],
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
              disabled={recapBusy || (id === 'recap' && !!media.current)}
              onClick={() => {
                if (id === 'recap') setRecapId(null);
                if (id === 'meeting' && selected?.source === 'personal-recap') {
                  current(null);
                  setSegments([]);
                }
                setTab(id);
                if (id === 'history') {
                  void list();
                  void notesHistory();
                }
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
              {tab === 'meeting'
                ? 'LIVE SESSION'
                : tab === 'mom'
                  ? 'MINUTES'
                  : tab === 'recap'
                    ? 'NOTES'
                    : tab.toUpperCase()}
            </span>
            <h1>
              {tab === 'meeting'
                ? 'Live Meeting Studio'
                : tab === 'history'
                  ? 'Meeting History'
                  : tab === 'mom'
                    ? 'Meeting Minutes'
                    : tab === 'recap'
                      ? 'Meeting Notes'
                      : 'Settings & Connections'}
            </h1>
          </div>
          <div className={'header-state ' + (captureHealth === 'active' ? 'recording' : '')}>
            <span className="dot" />
            {recapBusy
              ? 'Notes in progress'
              : captureHealth === 'active'
                ? 'Recording'
                : captureHealth === 'paused'
                  ? 'Recording paused'
                  : 'Recorder inactive'}
          </div>
        </header>

        <main
          className={
            'workspace-main ' +
            (tab === 'meeting' ? 'meeting-workspace' : tab === 'mom' ? 'minutes-workspace' : '')
          }
        >
          {tab === 'recap' && (
            <PostMeetingRecap
              initialId={recapId}
              onBusyChange={setRecapBusy}
              onSaved={() => void list()}
              onHistory={() => {
                setTab('history');
                void list();
                void notesHistory();
              }}
              onOpenMemo={async (mid, jobId) => {
                const loaded = await refresh(mid);
                if (!loaded)
                  throw new Error('The memo could not be opened. Your recap notes remain saved.');
                setAwaitingJob(jobId ?? null);
                setTab('mom');
              }}
            />
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
                  {!selected && <span className="sub">Set up your next conversation</span>}
                  <div className="timer-block">
                    <span className="label">
                      {historicDuration ? 'SESSION DURATION (INCLUDING PAUSES)' : 'RECORDING TIME'}
                    </span>
                    <RecordingTimer
                      key={selected?.id || 'new'}
                      clock={clock.current}
                      fallback={elapsed || historicDuration}
                    />
                  </div>
                </div>

                {!isSetup && (
                  <div className="capture-actions">
                    {isRunning ? (
                      <>
                        <button
                          className="secondary"
                          disabled={captureBusy || recorder.current?.state !== 'recording'}
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
                          disabled={captureBusy || recorder.current?.state !== 'paused'}
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
                        <button
                          className="primary"
                          disabled={generateBusy || captureBusy}
                          onClick={mom ? () => setTab('mom') : generate}
                        >
                          <FileText size={16} />
                          {mom ? 'View saved minutes' : generateBusy ? 'Queuing…' : 'Generate MoM'}
                        </button>
                        <button
                          className="secondary"
                          disabled={captureBusy || generateBusy}
                          onClick={newMeeting}
                        >
                          New Meeting
                        </button>
                        <button
                          className="secondary small"
                          disabled={generateBusy || captureBusy}
                          onClick={recover}
                        >
                          Recover transcript
                        </button>
                      </>
                    )}
                    {isRunning && !['connected', 'connecting'].includes(streamHealth) && (
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
                        <select
                          aria-label="Audio source"
                          value={source}
                          onChange={(e) => setSource(e.target.value)}
                        >
                          <option value="meeting-audio">
                            Online meeting: microphone + meeting audio
                          </option>
                          <option value="microphone">In-person: microphone only</option>
                          <option value="browser-tab">
                            Shared audio only (without my microphone)
                          </option>
                          <option value="desktop-agent" disabled>
                            Desktop agent — coming soon
                          </option>
                        </select>
                      </label>
                      <label>
                        Language
                        <select
                          aria-label="Language mode"
                          value={lang === 'auto' ? 'auto' : 'specific'}
                          onChange={(e) => setLang(e.target.value === 'auto' ? 'auto' : 'en-IN')}
                          title={lang === 'auto' ? autoLanguageDescription : undefined}
                          aria-description={lang === 'auto' ? autoLanguageDescription : undefined}
                        >
                          <option value="auto">{autoLanguageLabel}</option>
                          <option value="specific">Specific language</option>
                        </select>
                      </label>
                      {lang !== 'auto' && (
                        <label>
                          Recognition language
                          <input
                            value={lang}
                            onChange={(e) => setLang(e.target.value)}
                            placeholder="e.g. fr-FR"
                          />
                        </label>
                      )}
                      <p className="source-help">
                        Supported recording: up to 2 hours and 250 MiB. Start a new meeting to
                        continue beyond this limit.
                      </p>
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
                          <span
                            title={
                              selected.language === 'auto' ? autoLanguageDescription : undefined
                            }
                          >
                            {languageLabel(selected.language)}
                          </span>
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
                {selected && !media.current && !isSetup && selected.source !== 'personal-recap' && (
                  <TranscriptImprovement
                    key={selected.id}
                    mid={selected.id}
                    onApplied={() => refresh(selected.id)}
                  />
                )}
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
                        <TranscriptEntry
                          key={s.client_event_id || i}
                          segment={s}
                          speakerLabel={s.speaker_id ? labels[s.speaker_id] : undefined}
                          onRename={renameSpeaker}
                        />
                      ))}
                      {partial && (
                        <TranscriptEntry
                          pending
                          segment={{
                            client_event_id: 'interim',
                            text: partial,
                            start_ms: partialStart,
                            end_ms: partialStart,
                            speaker_id: partialSpeaker,
                          }}
                          speakerLabel={
                            partialSpeaker
                              ? labels[partialSpeaker] || 'Identifying speaker…'
                              : undefined
                          }
                        />
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
            <MeetingHistory
              meetings={meetings}
              localNotes={localNotes}
              cachedCount={cachedCount}
              historyOffline={historyOffline}
              storageInfo={storageInfo}
              historyQuery={historyQuery}
              historyMore={historyMore}
              sourceLabel={sourceLabel}
              onQuery={setHistoryQuery}
              onSearch={() => void list()}
              onReload={(more) => void list(more)}
              onSavedCopies={async () => {
                try {
                  setMeetings((await cachedMeetings()).map((item) => item.meeting));
                  setHistoryOffline(true);
                  setNotice('Showing saved copies. Reload to return to current backend history.');
                } catch (error) {
                  message(errorMessage(error));
                }
              }}
              onOpenLocal={(id) => {
                setRecapId(id);
                setTab('recap');
              }}
              onOpenMeeting={async (id) => {
                if (media.current && selected?.id !== id) {
                  message('Stop the current recording before opening another meeting.');
                  return;
                }
                const loaded = await refresh(id, historyOffline);
                if (loaded) {
                  if (loaded.meeting.source === 'personal-recap' && !loaded.minutes) {
                    setRecapId(id);
                    setTab('recap');
                  } else setTab(loaded.minutes && !media.current ? 'mom' : 'meeting');
                }
              }}
            />
          )}

          {tab === 'mom' && (
            <section className="panel minutes-panel">
              {!mom && (
                <div className="panel-heading">
                  <div>
                    <h3>Review minutes</h3>
                    <p>Review the meeting record before approval.</p>
                  </div>
                  <div className="minutes-heading-actions">
                    {selected && <span className="pill">Not ready</span>}
                    <button className="secondary small" onClick={loadMom}>
                      <RefreshCcw size={14} />
                      Refresh
                    </button>
                  </div>
                </div>
              )}
              {!selected ? (
                <div className="empty">
                  <ClipboardList size={28} />
                  <b>Choose a meeting first</b>
                  <span>Open a session from Meeting History to review its minutes.</span>
                  <button
                    className="secondary"
                    onClick={() => {
                      setTab('history');
                      void list();
                      void notesHistory();
                    }}
                  >
                    Browse meetings
                  </button>
                </div>
              ) : (
                <>
                  {mom ? (
                    <>
                      <MinutesReview
                        key={selected.id}
                        value={editMom}
                        onChange={setEditMom}
                        readOnly={['approved', 'outdated'].includes(mom.status) || historyOffline}
                        segments={segments}
                        speakerLabels={labels}
                        onRenameSpeaker={renameSpeaker}
                        meetingId={selected.id}
                        onViewingVersionChange={setReviewingVersion}
                        status={mom.status}
                        onRefresh={loadMom}
                        recollection={selected.source === 'personal-recap'}
                      />
                      <div className="minutes-actions">
                        <span role="status" className={momDirty ? 'draft-unsaved' : ''}>
                          {reviewingVersion
                            ? 'Earlier revision · read only'
                            : momDirty
                              ? 'Unsaved changes · save before approving'
                              : historyOffline
                                ? 'Offline copy · read only'
                                : mom.status === 'outdated'
                                  ? 'Transcript updated · regenerate minutes'
                                  : notice === 'MoM draft saved.' || notice === 'MoM approved.'
                                    ? notice
                                    : mom.status === 'approved'
                                      ? 'Approved meeting record'
                                      : ''}
                        </span>
                        <div className="buttons">
                          {mom.status === 'outdated' && (
                            <button
                              className="primary small"
                              disabled={
                                generateBusy ||
                                reviewingVersion ||
                                jobs.some((j) => ['pending', 'running'].includes(j.status))
                              }
                              onClick={generate}
                            >
                              Generate updated minutes
                            </button>
                          )}
                          {mom.status === 'approved' && !historyOffline && (
                            <button
                              className="secondary small"
                              disabled={reviewingVersion}
                              onClick={async () => {
                                try {
                                  await request(`/api/meetings/${selected.id}/mom/revise`, 'POST', {
                                    expected_revision: mom.revision ?? 0,
                                  });
                                  await loadMom();
                                } catch (error) {
                                  message(errorMessage(error));
                                }
                              }}
                            >
                              Create new revision
                            </button>
                          )}
                          {momDirty && mom.status !== 'approved' && (
                            <button
                              className="secondary"
                              disabled={reviewingVersion}
                              onClick={async () => {
                                try {
                                  const latest = await request<Minutes>(
                                    `/api/meetings/${selected.id}/mom`,
                                  );
                                  if (meetingRef.current?.id !== selected.id) return;
                                  setMom(latest);
                                  setEditMom(JSON.stringify(latest.data, null, 2));
                                } catch (error) {
                                  message(
                                    `Could not load the latest draft: ${errorMessage(error)}. Your edits were kept.`,
                                  );
                                }
                              }}
                            >
                              Discard changes
                            </button>
                          )}
                          <button
                            className="secondary"
                            disabled={
                              ['approved', 'outdated'].includes(mom.status) ||
                              reviewingVersion ||
                              historyOffline ||
                              jobs.some((j) => ['pending', 'running'].includes(j.status))
                            }
                            onClick={saveMom}
                          >
                            Save Draft
                          </button>
                          <button
                            className="primary"
                            disabled={
                              ['approved', 'outdated'].includes(mom.status) ||
                              reviewingVersion ||
                              momDirty ||
                              historyOffline ||
                              jobs.some((j) => ['pending', 'running'].includes(j.status))
                            }
                            onClick={approve}
                          >
                            Approve
                          </button>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="empty">
                      <FileText size={28} />
                      <b>
                        {jobs.some(
                          (j) =>
                            j.kind === 'recover_transcript' &&
                            ['pending', 'running'].includes(j.status),
                        )
                          ? 'Recovering transcript'
                          : jobs.some((j) => j.status === 'pending' || j.status === 'running')
                            ? 'Minutes are processing'
                            : jobs.some((j) => j.status === 'failed')
                              ? 'Generation failed'
                              : jobs.some(
                                    (j) =>
                                      j.kind === 'recover_transcript' && j.status === 'completed',
                                  )
                                ? 'Transcript recovered'
                                : 'No minutes generated yet'}
                      </b>
                      <span>
                        {jobs.some((j) => j.status === 'pending' || j.status === 'running')
                          ? 'Progress updates automatically. Completed transcript sections are saved between retries.'
                          : jobs.some((j) => j.status === 'failed')
                            ? 'Review the processing error and retry from Workspace.'
                            : 'Stop the meeting, then choose Generate MoM in the session panel.'}
                      </span>
                      {jobs.some(
                        (j) => j.kind === 'recover_transcript' && j.status === 'completed',
                      ) &&
                        !jobs.some((j) => ['pending', 'running'].includes(j.status)) && (
                          <button
                            className="primary small"
                            disabled={generateBusy}
                            onClick={generate}
                          >
                            Generate MoM
                          </button>
                        )}
                    </div>
                  )}
                  {processingJobs.length > 0 && (
                    <details
                      className="jobs minutes-jobs"
                      open={
                        jobs.some((j) => ['pending', 'running'].includes(j.status)) ||
                        (!mom && jobs.some((j) => j.status === 'failed'))
                      }
                    >
                      <summary>
                        Processing activity{' '}
                        <span>
                          {jobs.some((j) => ['pending', 'running'].includes(j.status))
                            ? 'In progress'
                            : jobs[0]?.status || 'No jobs'}
                        </span>
                      </summary>
                      {workerReady === false &&
                        jobs.some((j) => ['pending', 'running'].includes(j.status)) && (
                          <p role="status">
                            Worker is unavailable or restarting. Your queued work is saved.
                          </p>
                        )}
                      {processingJobs.map((j) => (
                        <div key={j.id}>
                          <span>
                            {j.kind} — {j.status}
                          </span>
                          <small>
                            {j.status === 'pending' && j.run_after
                              ? `Attempt ${(j.retry_attempts ?? j.attempts) + 1} of ${j.max_attempts ?? 3} scheduled for ${new Date(j.run_after).toLocaleTimeString()}. `
                              : ''}
                            {j.error || `Attempts: ${j.attempts}`}{' '}
                            {j.completed_sections
                              ? ` · ${j.completed_sections} completed stages saved`
                              : ''}
                          </small>
                        </div>
                      ))}
                    </details>
                  )}
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
                      API credentials load automatically from api-service/.env. No token entry is
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
                  <p>You are signed in with your company account. Access renews automatically.</p>
                  <button disabled={connectionBusy} onClick={() => connect(false)}>
                    Test connection
                  </button>
                </>
              )}
              <div className="settings-note">
                <ShieldCheck size={17} />
                <span>Provider credentials are managed by your administrator.</span>
              </div>
              <StorageSettings />
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
          {warning || syncWarning || (notice && tab === 'settings') ? (
            <div
              className={'app-feedback' + (warning || syncWarning ? ' feedback-error' : '')}
              role={warning || syncWarning ? 'alert' : 'status'}
            >
              {(warning || syncWarning) && <AlertCircle size={14} />}
              <span>{warning || syncWarning || notice}</span>
              <button
                aria-label="Dismiss notification"
                onClick={() => {
                  setWarning('');
                  setSyncWarning('');
                  setNotice('');
                }}
              >
                ×
              </button>
            </div>
          ) : (
            <span className="footer-note">
              Private meeting workspace<span className="footer-separator">·</span>Browser capture
              stops when this tab closes
            </span>
          )}
        </footer>
        {renamingSpeaker && (
          <SpeakerRenameDialog
            key={renamingSpeaker}
            label={labels[renamingSpeaker] || 'Speaker'}
            onSave={saveSpeakerName}
            onClose={() => setRenamingSpeaker(null)}
          />
        )}
      </div>
    </div>
  );
}
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </React.StrictMode>,
);
