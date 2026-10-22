import { useEffect, useMemo, useRef, useState } from 'react';
import { registerSessionDrain, finishRecorder } from './sessionLifecycle';
import { FileText, Mic, Plus, RefreshCcw, Square, Upload } from 'lucide-react';
import { errorMessage, getRecapAudio, request, uploadRecapAudio } from './api';
import { readRecapDraft, saveRecapDraft } from './recapDraft';
import type { LocalRecapDraft } from './recapDraft';
import type { Job } from './types';
import { useSpeechLanguages } from './useSpeechLanguages';
import { formatElapsed } from './stopwatch';
import { notesSpeech } from './notesSpeech';

interface SavedRecap {
  finalization?: { status: string; live_notes: string; final_notes: string | null } | null;
  id: string;
  title: string;
  language: string;
  meeting_date: string | null;
  participants: string[];
  notes: string;
  reviewed: boolean;
  revision: number;
  audio_available: boolean;
  memo_status: string | null;
}
type Phase =
  'idle' | 'starting' | 'recording' | 'stopping' | 'uploading' | 'transcribing' | 'generating';
const freshDraft = (): LocalRecapDraft => {
  const stamp = new Date().toISOString();
  return {
    id: crypto.randomUUID(),
    title: `Meeting notes · ${new Date(stamp).toLocaleString()}`,
    meetingDate: '',
    participants: '',
    language: 'auto',
    notes: '',
    reviewed: false,
    parts: [],
    filename: '',
    interrupted: false,
    remote: false,
    serverRevision: 0,
    createdAt: stamp,
    updatedAt: stamp,
    dirty: false,
    liveIncomplete: false,
  };
};
const payload = (draft: LocalRecapDraft) => ({
  title:
    draft.title.trim() ||
    `Meeting notes · ${new Date(draft.createdAt || Date.now()).toLocaleString()}`,
  language: draft.language,
  meeting_date: draft.meetingDate || null,
  participants: draft.participants
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean),
  notes: draft.notes.trim(),
  reviewed: draft.reviewed,
});

export function PostMeetingRecap({
  initialId,
  onBusyChange,
  onOpenMemo,
  onSaved,
  onHistory,
}: {
  initialId: string | null;
  onBusyChange: (busy: boolean) => void;
  onOpenMemo: (mid: string, jobId?: string) => Promise<void>;
  onSaved: () => void;
  onHistory: () => void;
}) {
  const { label: autoLanguageLabel, description: autoLanguageDescription } = useSpeechLanguages();
  const [draft, setDraft] = useState(freshDraft);
  const draftRef = useRef(draft);
  const [phase, setPhaseState] = useState<Phase>('idle');
  const phaseRef = useRef<Phase>('idle');
  const setPhase = (value: Phase) => {
    phaseRef.current = value;
    setPhaseState(value);
  };
  const [loading, setLoading] = useState(true),
    [error, setError] = useState('');
  const [audioAvailable, setAudioAvailable] = useState(false);
  const audioSaved = useRef(false);
  const [memoStatus, setMemoStatus] = useState<string | null>(null);
  const [finalization, setFinalization] = useState<SavedRecap['finalization']>(null);
  const [localSaved, setLocalSaved] = useState(false),
    [syncing, setSyncing] = useState(false),
    [syncError, setSyncError] = useState(false);
  const conflict = useRef(false);
  const [elapsed, setElapsed] = useState(0),
    [level, setLevel] = useState(0),
    [partial, setPartial] = useState(''),
    [speechState, setSpeechState] = useState('');
  const partialRef = useRef('');
  const speech = useRef<ReturnType<typeof notesSpeech> | null>(null);
  const recorder = useRef<MediaRecorder | null>(null),
    stream = useRef<MediaStream | null>(null),
    meter = useRef<AudioContext | null>(null);
  const frame = useRef(0),
    recordingStart = useRef(0),
    loadVersion = useRef(0);
  const writes = useRef<Promise<unknown>>(Promise.resolve()),
    syncQueue = useRef<Promise<unknown>>(Promise.resolve());
  const action = useRef(false),
    alive = useRef(true);
  const fileInput = useRef<HTMLInputElement>(null),
    textArea = useRef<HTMLTextAreaElement>(null);
  const stopPromise = useRef<Promise<void> | null>(null);
  const captureActive = ['starting', 'recording', 'stopping'].includes(phase);
  useEffect(
    () =>
      registerSessionDrain(async () => {
        const active = recorder.current && recorder.current.state !== 'inactive';
        try {
          await finishRecorder(recorder.current);
        } finally {
          releaseMic();
          speech.current?.close();
          speech.current = null;
          recorder.current = null;
        }
        if (active) update({ interrupted: true, liveIncomplete: true, reviewed: false });
        await writes.current;
      }),
    [],
  );
  const busy = loading || phase !== 'idle',
    locked = memoStatus === 'approved';
  const audio = useMemo(
    () =>
      !captureActive && draft.parts.length
        ? new Blob(draft.parts, { type: draft.parts[0].type })
        : null,
    [draft.parts, captureActive],
  );
  const [audioUrl, setAudioUrl] = useState('');

  function update(changes: Partial<LocalRecapDraft>, dirty = true) {
    const next = {
      ...draftRef.current,
      ...changes,
      ...(dirty ? { dirty: true, updatedAt: new Date().toISOString() } : {}),
    };
    draftRef.current = next;
    if (alive.current) {
      setDraft(next);
      setLocalSaved(false);
    }
    writes.current = writes.current.catch(() => {}).then(() => saveRecapDraft(next));
    void writes.current
      .then(() => {
        if (alive.current && draftRef.current === next) setLocalSaved(true);
      })
      .catch(() => {
        if (alive.current)
          setError(
            'Local save failed. Keep this page open and download your audio before leaving.',
          );
      });
    return next;
  }
  async function run(task: () => Promise<void>) {
    if (action.current) return;
    action.current = true;
    setError('');
    try {
      await task();
    } catch (failure) {
      setError(errorMessage(failure));
      setPhase('idle');
    } finally {
      action.current = false;
    }
  }
  async function syncNow(includeAudio = true) {
    const task = async () => {
      if (conflict.current)
        throw new Error(
          'Notes changed in another tab. Your local version is preserved; save a copy to continue.',
        );
      const current = draftRef.current,
        body = payload(current);
      setSyncing(true);
      try {
        let revision = current.serverRevision;
        if (!current.remote) {
          const saved = await request<SavedRecap>('/api/recaps', 'POST', {
            id: current.id,
            title: body.title,
            language: body.language,
            meeting_date: body.meeting_date,
            participants: body.participants,
          });
          revision = saved.revision;
          update({ remote: true, serverRevision: revision }, false);
        }
        const saved = await request<SavedRecap>(`/api/recaps/${current.id}/notes`, 'PATCH', {
          ...body,
          revision,
        });
        if (draftRef.current.id !== current.id) return;
        const unchanged = JSON.stringify(payload(draftRef.current)) === JSON.stringify(body);
        update({ serverRevision: saved.revision, remote: true, dirty: !unchanged }, false);
        if (includeAudio && !audioSaved.current && draftRef.current.parts.length) {
          const latest = draftRef.current;
          await uploadRecapAudio(
            latest.id,
            new Blob(latest.parts, { type: latest.parts[0].type }),
            latest.filename || 'notes.webm',
            false,
          );
          audioSaved.current = true;
          setAudioAvailable(true);
        }
        await writes.current;
        setSyncError(false);
        onSaved();
      } catch (failure) {
        if ((failure as { status?: number }).status === 409) conflict.current = true;
        setSyncError(true);
        throw failure;
      } finally {
        setSyncing(false);
      }
    };
    const pending = syncQueue.current.catch(() => {}).then(task);
    syncQueue.current = pending;
    return pending;
  }
  async function load(id?: string) {
    const version = ++loadVersion.current;
    setLoading(true);
    setError('');
    conflict.current = false;
    try {
      await writes.current;
      const local = await readRecapDraft(id);
      if (version !== loadVersion.current) return;
      let next = local ?? freshDraft(),
        hasAudio = false;
      next = { ...next, createdAt: next.createdAt || new Date().toISOString() };
      setMemoStatus(null);
      if ((id && !local) || local?.remote) {
        try {
          const saved = await request<SavedRecap>(`/api/recaps/${id ?? local!.id}`);
          if (version !== loadVersion.current) return;
          const keep =
            !!local && (local.dirty || (!!local.notes.trim() && local.notes !== saved.notes));
          next = {
            ...next,
            id: saved.id,
            remote: true,
            ...(keep
              ? {}
              : {
                  title: saved.title,
                  language: saved.language,
                  meetingDate: saved.meeting_date || '',
                  participants: saved.participants.join(', '),
                  notes: saved.notes,
                  reviewed: saved.reviewed,
                  serverRevision: saved.revision,
                  dirty: false,
                }),
          };
          hasAudio = saved.audio_available;
          setFinalization(
            saved.finalization ||
              (next.finalizationRequired
                ? { status: 'failed', live_notes: next.notes, final_notes: null }
                : null),
          );
          setMemoStatus(saved.memo_status);
          const jobs = await request<Job[]>(`/api/meetings/${saved.id}/jobs`);
          if (version !== loadVersion.current) return;
          setPhase(
            jobs.some(
              (j) => j.kind === 'transcribe_recap' && ['pending', 'running'].includes(j.status),
            )
              ? 'transcribing'
              : 'idle',
          );
        } catch (failure) {
          if (!local) throw failure;
          setError('Server unavailable. Your local notes and audio are retained.');
          setPhase('idle');
        }
      } else setPhase('idle');
      if (version !== loadVersion.current) return;
      audioSaved.current = hasAudio;
      setAudioAvailable(hasAudio);
      update(next, false);
      await writes.current;
      if (next.interrupted)
        setError(
          'Recovered an interrupted recording. Review the retained text/audio or recover transcription from audio.',
        );
    } catch (failure) {
      if (version === loadVersion.current) setError(errorMessage(failure));
    } finally {
      if (version === loadVersion.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load(initialId ?? undefined);
  }, [initialId]);
  useEffect(() => {
    onBusyChange(
      ['starting', 'recording', 'stopping', 'uploading', 'generating'].includes(phase) || syncing,
    );
    return () => onBusyChange(false);
  }, [phase, syncing, onBusyChange]);
  useEffect(() => {
    if (
      loading ||
      phase !== 'idle' ||
      locked ||
      conflict.current ||
      (!draft.dirty && !(draft.parts.length && !audioAvailable))
    )
      return;
    const timer = setTimeout(() => {
      void syncNow().catch((failure) => setError(errorMessage(failure)));
    }, 1000);
    return () => clearTimeout(timer);
  }, [
    loading,
    phase,
    locked,
    draft.title,
    draft.meetingDate,
    draft.participants,
    draft.language,
    draft.notes,
    draft.reviewed,
    draft.dirty,
    audioAvailable,
  ]);
  useEffect(() => {
    const url = audio ? URL.createObjectURL(audio) : '';
    setAudioUrl(url);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [audio]);
  useEffect(() => {
    if (captureActive && textArea.current)
      textArea.current.scrollTop = textArea.current.scrollHeight;
  }, [draft.notes, partial, captureActive]);
  useEffect(() => {
    if (phase !== 'recording') return;
    const timer = setInterval(() => {
      const duration = performance.now() - recordingStart.current;
      setElapsed(duration);
      if (duration >= 1800000) void stopRecording();
    }, 200);
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', unload);
    return () => {
      clearInterval(timer);
      window.removeEventListener('beforeunload', unload);
    };
  }, [phase]);
  function releaseMic() {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    cancelAnimationFrame(frame.current);
    void meter.current?.close().catch(() => {});
    meter.current = null;
    setLevel(0);
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ++loadVersion.current;
      speech.current?.close();
      speech.current = null;
      const active = recorder.current;
      if (active && active.state !== 'inactive') {
        active.onstop = null;
        active.stop();
      }
      releaseMic();
    };
  }, []);
  useEffect(() => {
    if (phase !== 'transcribing' || !draft.remote) return;
    let disposed = false,
      checking = false;
    const check = async () => {
      if (checking) return;
      checking = true;
      try {
        const jobs = await request<Job[]>(`/api/meetings/${draft.id}/jobs`),
          latest = jobs.find((j) => j.kind === 'transcribe_recap');
        if (disposed || !latest || ['pending', 'running'].includes(latest.status)) return;
        if (latest.status === 'completed') {
          const saved = await request<SavedRecap>(`/api/recaps/${draft.id}`);
          if (disposed) return;
          setFinalization(saved.finalization);
          if (!saved.finalization)
            update(
              {
                notes: saved.notes,
                reviewed: false,
                serverRevision: saved.revision,
                dirty: false,
                liveIncomplete: false,
              },
              false,
            );
          onSaved();
        } else
          setError(
            latest.error || 'Transcription failed. Audio is retained; retry or type your notes.',
          );
        setPhase('idle');
      } catch (failure) {
        if (!disposed) setError(errorMessage(failure));
      } finally {
        checking = false;
      }
    };
    void check();
    const timer = setInterval(check, 2000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [phase, draft.id, draft.remote]);
  async function newNotes(copy = false) {
    await syncQueue.current.catch(() => {});
    await writes.current;
    const old = draftRef.current,
      next = {
        ...freshDraft(),
        ...(copy
          ? {
              title: `${old.title} (copy)`.slice(0, 255),
              notes: old.notes,
              participants: old.participants,
              meetingDate: old.meetingDate,
              language: old.language,
              dirty: true,
            }
          : {}),
      };
    conflict.current = false;
    audioSaved.current = false;
    setAudioAvailable(false);
    setMemoStatus(null);
    setFinalization(null);
    setElapsed(0);
    setPartial('');
    setSpeechState('');
    setSyncError(false);
    update(next, false);
    await writes.current;
  }
  async function transcribeUpload() {
    setPhase('uploading');
    await syncNow(false);
    const current = draftRef.current,
      blob = current.parts.length ? new Blob(current.parts, { type: current.parts[0].type }) : null;
    if (!blob?.size) throw new Error('Record or upload audio first.');
    const result = await uploadRecapAudio(current.id, blob, current.filename || 'notes.webm');
    audioSaved.current = true;
    setAudioAvailable(true);
    if (result.status === 'failed')
      throw new Error('Transcription failed. Your audio is retained; use Retry transcription.');
    setPhase('transcribing');
  }
  async function startRecording() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
      throw new Error(
        'Microphone recording needs HTTPS on mobile or localhost on this computer. Upload audio instead.',
      );
    if (!window.MediaRecorder) throw new Error('This browser cannot record. Upload audio instead.');
    setPhase('starting');
    await syncQueue.current.catch(() => {});
    const mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    stream.current = mic;
    try {
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find((type) =>
        MediaRecorder.isTypeSupported(type),
      );
      if (!mime) throw new Error('This browser has no supported recorder. Upload audio instead.');
      update({
        parts: [],
        filename: mime.includes('mp4')
          ? 'notes.m4a'
          : mime.includes('ogg')
            ? 'notes.ogg'
            : 'notes.webm',
        interrupted: true,
        liveIncomplete: false,
        reviewed: false,
      });
      await writes.current;
      const r = new MediaRecorder(mic, { mimeType: mime });
      recorder.current = r;
      r.ondataavailable = (event) => {
        if (!event.data.size) return;
        const parts = [...draftRef.current.parts, event.data];
        update({ parts });
        if (parts.reduce((size, part) => size + part.size, 0) > 100 * 1024 * 1024) {
          setError('100 MB limit reached. Download the retained audio.');
          void stopRecording();
        }
      };
      r.onerror = () => {
        setError('Recorder error. Review retained audio and text.');
        void stopRecording();
      };
      mic.getTracks().forEach((track) =>
        track.addEventListener('ended', () => {
          if (recorder.current?.state === 'recording') {
            setError('Microphone disconnected. Captured notes are retained.');
            void stopRecording();
          }
        }),
      );
      r.start(2000);
      recordingStart.current = performance.now();
      setElapsed(0);
      setPhase('recording');
      const id = draftRef.current.id;
      const live = notesSpeech(mic, draftRef.current.language, {
        partial: (text) => {
          if (draftRef.current.id === id && alive.current) {
            partialRef.current = text;
            setPartial(text);
          }
        },
        final: (text) => {
          if (draftRef.current.id !== id || !alive.current) return;
          const notes = [draftRef.current.notes, text].filter(Boolean).join('\n');
          if (notes.length > 20000) {
            update({ liveIncomplete: true });
            setError(
              'Text limit reached. Audio is retained; shorten your notes before generation.',
            );
            return;
          }
          update({ notes, reviewed: false });
        },
        state: (value) => {
          if (alive.current && draftRef.current.id === id) setSpeechState(value);
        },
        failed: () => {
          if (alive.current && draftRef.current.id === id) update({ liveIncomplete: true });
        },
      });
      speech.current = live;
      void live.start();
      try {
        const context = new AudioContext();
        meter.current = context;
        const analyser = context.createAnalyser();
        analyser.fftSize = 256;
        context.createMediaStreamSource(mic).connect(analyser);
        void context.resume().catch(() => {
          if (meter.current === context)
            setSpeechState((old) => `${old} · microphone level unavailable`);
        });
        const values = new Uint8Array(analyser.fftSize);
        const measure = () => {
          if (meter.current !== context || phaseRef.current !== 'recording') return;
          analyser.getByteTimeDomainData(values);
          const rms = Math.sqrt(
            values.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / values.length,
          );
          setLevel(Math.min(100, Math.round(rms * 350)));
          frame.current = requestAnimationFrame(measure);
        };
        measure();
      } catch {
        setSpeechState((old) => `${old} · microphone level unavailable`);
      }
    } catch (failure) {
      releaseMic();
      recorder.current = null;
      throw failure;
    }
  }
  async function stopRecording() {
    if (stopPromise.current) return stopPromise.current;
    const stop = async () => {
      setPhase('stopping');
      setElapsed(performance.now() - recordingStart.current);
      const live = speech.current;
      try {
        const r = recorder.current;
        if (r && r.state !== 'inactive')
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(
              () =>
                reject(
                  new Error(
                    'Recorder did not finalize. Your recovered audio/text remain on this device.',
                  ),
                ),
              10000,
            );
            r.onstop = () => {
              clearTimeout(timer);
              resolve();
            };
            r.stop();
          });
        await live?.stop();
        releaseMic();
        if (partialRef.current.trim()) {
          // Preserve the visible tail when Azure stops before returning a final phrase.
          const notes = [draftRef.current.notes, partialRef.current.trim()]
            .filter(Boolean)
            .join('\n');
          update({ notes: notes.slice(0, 20000), liveIncomplete: true, reviewed: false });
        }
        partialRef.current = '';
        setPartial('');
        update({ interrupted: false, finalizationRequired: true });
        setFinalization({
          status: 'pending',
          live_notes: draftRef.current.notes,
          final_notes: null,
        });
        await writes.current;
        await syncNow();
        await request(`/api/recaps/${draftRef.current.id}/finalize`, 'POST');
        setFinalization({
          status: 'pending',
          live_notes: draftRef.current.notes,
          final_notes: null,
        });
        setPhase('transcribing');
      } finally {
        releaseMic();
        live?.close();
        speech.current = null;
        recorder.current = null;
        if (phaseRef.current !== 'transcribing') setPhase('idle');
      }
    };
    stopPromise.current = stop()
      .catch((failure) => {
        setError(errorMessage(failure));
      })
      .finally(() => {
        stopPromise.current = null;
      });
    return stopPromise.current;
  }
  const saveStatus =
    syncing || !localSaved
      ? 'Saving…'
      : syncError
        ? 'Saved on this device · sync failed'
        : draft.remote && !draft.dirty && (!draft.parts.length || audioAvailable)
          ? 'Saved'
          : 'Saved on this device';

  return (
    <section className="panel recap-panel meeting-notes-panel" aria-label="Meeting Notes">
      <div className="panel-heading recap-heading">
        <div className="notes-save-status" role="status">
          {loading ? 'Loading…' : saveStatus}
        </div>
        <div className="notes-header-actions">
          <button className="secondary" disabled={busy || syncing} onClick={onHistory}>
            History
          </button>
          <button
            className="secondary"
            disabled={busy || syncing}
            onClick={() => void run(() => newNotes())}
          >
            <Plus size={17} />
            New notes
          </button>
        </div>
      </div>
      {error && (
        <div className="recap-message error" role="alert">
          {error}
          {syncError && (
            <button
              className="text-button"
              disabled={busy || syncing}
              onClick={() => void run(() => (conflict.current ? newNotes(true) : syncNow()))}
            >
              {conflict.current ? 'Save as new notes' : 'Retry save'}
            </button>
          )}
        </div>
      )}
      <div className="recap-columns">
        <div className="recap-capture">
          <div className="recap-record-controls">
            {phase === 'recording' ? (
              <button
                className="primary danger recap-main-button"
                onClick={() => void run(stopRecording)}
              >
                <Square size={20} />
                Stop
              </button>
            ) : (
              <button
                className="primary recap-main-button"
                disabled={busy || syncing || !!draft.parts.length || audioAvailable || locked}
                onClick={() => void run(startRecording)}
              >
                <Mic size={20} />
                {phase === 'starting' ? 'Starting microphone…' : 'Record notes'}
              </button>
            )}
            <button
              className="secondary recap-main-button"
              disabled={
                busy ||
                syncing ||
                !!draft.parts.length ||
                audioAvailable ||
                !!draft.notes.trim() ||
                locked
              }
              onClick={() => fileInput.current?.click()}
            >
              <Upload size={19} />
              Upload audio
            </button>
            <input
              ref={fileInput}
              type="file"
              accept="audio/*,.m4a,.mp4,.webm,.wav,.mp3,.ogg,.aac,.flac,.amr,.opus"
              aria-label="Choose notes audio"
              className="recap-file-input"
              disabled={busy || syncing || !!draft.notes.trim() || locked}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (!file) return;
                void run(async () => {
                  setPhase('uploading');
                  if (!file.size || file.size > 100 * 1024 * 1024)
                    throw new Error('Choose audio up to 100 MB and 30 minutes.');
                  update({
                    parts: [file],
                    filename: file.name,
                    interrupted: false,
                    reviewed: false,
                  });
                  await writes.current;
                  await transcribeUpload();
                });
              }}
            />
            {phase === 'recording' && (
              <div className="notes-recording-status">
                <div
                  className="recap-recording-time"
                  role="timer"
                  aria-label="Recording elapsed time"
                >
                  <span className="dot" />
                  Recording · {formatElapsed(elapsed)}
                </div>
                <div
                  className="notes-level"
                  role="meter"
                  aria-label="Microphone level"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={level}
                >
                  <span style={{ width: `${level}%` }} />
                </div>
              </div>
            )}
            {phase === 'stopping' && <span role="status">Finalizing audio and text…</span>}
          </div>
          <div className="new-form">
            <label>
              Title
              <input
                aria-label="Notes title"
                maxLength={255}
                disabled={busy || locked}
                value={draft.title}
                onChange={(event) => update({ title: event.target.value })}
              />
            </label>
            <label>
              Language
              <select
                aria-label="Notes language mode"
                disabled={busy || locked || !!draft.parts.length}
                value={draft.language === 'auto' ? 'auto' : 'specific'}
                title={draft.language === 'auto' ? autoLanguageDescription : undefined}
                onChange={(event) =>
                  update({ language: event.target.value === 'auto' ? 'auto' : 'en-IN' })
                }
              >
                <option value="auto">{autoLanguageLabel}</option>
                <option value="specific">Specific language</option>
              </select>
            </label>
            {draft.language !== 'auto' && (
              <label>
                Recognition language
                <input
                  aria-label="Notes recognition language"
                  disabled={busy || locked || !!draft.parts.length}
                  value={draft.language}
                  onChange={(event) => update({ language: event.target.value })}
                />
              </label>
            )}
            <details className="notes-details">
              <summary>
                Meeting details <small>optional</small>
              </summary>
              <label>
                Meeting date
                <input
                  type="date"
                  aria-label="Notes meeting date"
                  disabled={busy || locked}
                  value={draft.meetingDate}
                  onChange={(event) => update({ meetingDate: event.target.value })}
                />
              </label>
              <label>
                Participants
                <input
                  aria-label="Notes participants"
                  disabled={busy || locked}
                  value={draft.participants}
                  placeholder="Names separated by commas"
                  onChange={(event) => update({ participants: event.target.value })}
                />
              </label>
            </details>
          </div>
          {audioUrl && (
            <div className="recap-audio">
              <audio controls src={audioUrl} aria-label="Play notes audio" />
              <a className="text-button" href={audioUrl} download={draft.filename || 'notes.webm'}>
                Download audio
              </a>
            </div>
          )}
          {!audioUrl && audioAvailable && !captureActive && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const blob = await getRecapAudio(draft.id);
                  const ext = blob.type.includes('mp4')
                    ? 'm4a'
                    : blob.type.includes('mpeg')
                      ? 'mp3'
                      : blob.type.split('/')[1]?.replace('x-', '').split(';')[0] || 'bin';
                  update({ parts: [blob], filename: `notes.${ext}` }, false);
                })
              }
            >
              Load saved audio
            </button>
          )}
          {phase === 'idle' &&
            !locked &&
            (audio || audioAvailable) &&
            (draft.liveIncomplete || !draft.notes.trim()) && (
              <button
                className="secondary"
                disabled={syncing}
                onClick={() =>
                  void run(async () => {
                    if (draft.notes.trim()) {
                      const original = draftRef.current,
                        blob = audio || (await getRecapAudio(original.id));
                      await newNotes();
                      update({
                        title: `${original.title} (recovered)`.slice(0, 255),
                        language: original.language,
                        meetingDate: original.meetingDate,
                        participants: original.participants,
                        parts: [blob],
                        filename: original.filename || 'notes.webm',
                      });
                      await transcribeUpload();
                    } else if (audioAvailable) {
                      await syncQueue.current.catch(() => {});
                      await request(`/api/recaps/${draft.id}/transcribe`, 'POST');
                      setPhase('transcribing');
                    } else await transcribeUpload();
                  })
                }
              >
                <RefreshCcw size={16} />
                {draft.notes.trim() ? 'Recover transcript from audio' : 'Retry transcription'}
              </button>
            )}
        </div>
        <div className="recap-notes">
          <div className="notes-transcript-heading">
            <h2>
              {finalization && finalization.status !== 'accepted'
                ? 'Review transcript'
                : 'Live transcript'}
            </h2>
            <span className="notes-speech-status" role="status">
              {captureActive
                ? speechState
                : phase === 'transcribing'
                  ? 'Finalizing transcript from saved audio…'
                  : phase === 'uploading'
                    ? 'Saving audio…'
                    : ''}
            </span>
          </div>
          <textarea
            ref={textArea}
            aria-label="Live transcript"
            placeholder={
              captureActive
                ? 'Listening… Your words will appear here.'
                : 'Record notes, upload audio, or type here…'
            }
            maxLength={20000}
            disabled={busy || locked}
            value={draft.notes + (partial ? `${draft.notes ? '\n' : ''}${partial}` : '')}
            onChange={(event) => update({ notes: event.target.value, reviewed: false })}
          />
          {finalization && finalization.status === 'completed' && (
            <div className="notes-final-review">
              <h3>Audio-based transcript</h3>
              <p>Your live text and edits above are preserved. Choose which version to review.</p>
              <textarea
                aria-label="Audio-based transcript"
                readOnly
                value={finalization.final_notes || ''}
              />
              <details>
                <summary>Original live transcript</summary>
                <p style={{ whiteSpace: 'pre-wrap' }}>{finalization.live_notes}</p>
              </details>
              <div className="notes-header-actions">
                {(['current', 'audio'] as const).map((choice) => (
                  <button
                    className="secondary"
                    key={choice}
                    disabled={busy || syncing}
                    onClick={() =>
                      void run(async () => {
                        await syncNow();
                        const saved = await request<SavedRecap>(
                          `/api/recaps/${draft.id}/finalize/choose`,
                          'POST',
                          { choice, revision: draftRef.current.serverRevision },
                        );
                        update(
                          {
                            notes: saved.notes,
                            reviewed: false,
                            serverRevision: saved.revision,
                            dirty: false,
                            finalizationRequired: false,
                          },
                          false,
                        );
                        setFinalization(saved.finalization);
                      })
                    }
                  >
                    {choice === 'current' ? 'Keep my edited text' : 'Use audio transcript'}
                  </button>
                ))}
              </div>
            </div>
          )}
          {finalization &&
            !['accepted', 'completed'].includes(finalization.status) &&
            phase === 'idle' && (
              <button
                className="secondary"
                onClick={() =>
                  void run(async () => {
                    await syncNow();
                    await request(`/api/recaps/${draft.id}/finalize`, 'POST');
                    setPhase('transcribing');
                  })
                }
              >
                Retry audio finalization
              </button>
            )}
          <div className="notes-footer">
            <label className="recap-reviewed">
              <input
                type="checkbox"
                disabled={busy || locked || !draft.notes.trim()}
                checked={draft.reviewed}
                onChange={(event) => update({ reviewed: event.target.checked })}
              />
              <span>I reviewed these notes.</span>
            </label>
            <button
              className="primary"
              disabled={
                busy ||
                locked ||
                !draft.notes.trim() ||
                !draft.reviewed ||
                conflict.current ||
                (!!finalization && finalization.status !== 'accepted') ||
                (draft.finalizationRequired && finalization?.status !== 'accepted')
              }
              onClick={() =>
                void run(async () => {
                  setPhase('generating');
                  await syncNow();
                  const result = await request<{ job_id: string }>(
                    `/api/meetings/${draftRef.current.id}/mom/generate`,
                    'POST',
                  );
                  await onOpenMemo(draftRef.current.id, result.job_id);
                })
              }
            >
              <FileText size={18} />
              {phase === 'generating' ? 'Generating…' : 'Generate MoM'}
            </button>
          </div>
          {memoStatus && (
            <button
              className="text-button"
              disabled={busy || syncing}
              onClick={() => void onOpenMemo(draft.id)}
            >
              View saved MoM
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
