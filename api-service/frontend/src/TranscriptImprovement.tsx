import { useEffect, useState } from 'react';
import { request, errorMessage } from './api';
import { TranscriptEntry } from './TranscriptEntry';
import type { Segment } from './types';

interface Comparison {
  status: string;
  error: string | null;
  candidate_id: string | null;
  resolved: boolean;
  stale: boolean;
  suggestions: Record<string, string>;
  original: Segment[];
  candidate: Segment[];
  speaker_names: Record<string, string>;
}

export function TranscriptImprovement({
  mid,
  onApplied,
}: {
  mid: string;
  onApplied: () => Promise<unknown>;
}) {
  const [value, setValue] = useState<Comparison | null>(null);
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const running = !!value && ['pending', 'running'].includes(value.status);
  useEffect(() => {
    let disposed = false,
      checking = false;
    const load = async () => {
      if (checking) return;
      checking = true;
      try {
        const next = await request<Comparison>(`/api/meetings/${mid}/improvement`);
        if (!disposed) setValue(next);
      } catch (failure) {
        if (!disposed) setError(errorMessage(failure));
      } finally {
        checking = false;
      }
    };
    void load();
    const timer = running || open ? setInterval(load, 2000) : undefined;
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [mid, running, open]);
  const recovered = [
    ...new Set(value?.candidate.map((s) => s.speaker_id).filter((s): s is string => !!s)),
  ];
  const original = [
    ...new Set(value?.original.map((s) => s.speaker_id).filter((s): s is string => !!s)),
  ];
  const names = Object.fromEntries(
    original.map((speaker, index) => [
      speaker,
      value?.speaker_names[speaker] || `Original speaker ${index + 1}`,
    ]),
  );
  const recoveredNames = Object.fromEntries(
    recovered.map((speaker, index) => [speaker, `Audio speaker ${index + 1}`]),
  );
  async function start() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const job = await request<{ status: string }>(`/api/meetings/${mid}/improve`, 'POST');
      const next = await request<Comparison>(`/api/meetings/${mid}/improvement`);
      setValue({ ...next, status: job.status });
      setOpen(true);
      setMapping({});
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function apply(keep: boolean) {
    if (!value?.candidate_id || busy) return;
    setBusy(true);
    setError('');
    try {
      await request(`/api/meetings/${mid}/improvement/apply`, 'POST', {
        candidate_id: value.candidate_id,
        keep_original: keep,
        speaker_mapping: keep
          ? {}
          : Object.fromEntries(
              recovered.map((speaker) => [
                speaker,
                mapping[speaker] === '__new' ? null : mapping[speaker],
              ]),
            ),
      });
      await onApplied();
      setValue(await request<Comparison>(`/api/meetings/${mid}/improvement`));
      setOpen(false);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="improvement-actions">
      <button className="secondary small" disabled={busy || !!running} onClick={() => void start()}>
        {running ? 'Improving transcript…' : 'Improve transcript from audio'}
      </button>
      {value?.candidate_id && !value.resolved && (
        <button className="secondary small" onClick={() => setOpen(true)}>
          Review audio transcript
        </button>
      )}
      {value?.status === 'failed' && (
        <span role="status">
          {value.error || 'Improvement failed. Original transcript retained.'}
        </span>
      )}
      {!open && error && <span role="alert">{error}</span>}
      <small>
        Optional Azure processing. Original transcript and speaker names stay unchanged until you
        apply a version.
      </small>
      {open && (
        <div
          className="evidence-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Compare audio transcript"
          onKeyDown={(event) => {
            if (event.key === 'Escape' && !busy) setOpen(false);
            if (event.key === 'Tab') {
              const controls = Array.from(
                event.currentTarget.querySelectorAll<HTMLElement>(
                  'button:not(:disabled), select:not(:disabled)',
                ),
              );
              const first = controls[0],
                last = controls[controls.length - 1];
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }
          }}
        >
          <section className="minute-card improvement-dialog">
            <div className="notes-header-actions">
              <h2>Compare audio transcript</h2>
              <button
                autoFocus
                className="secondary"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                Close comparison
              </button>
            </div>
            {error && <p role="alert">{error}</p>}
            {running && (
              <p role="status">
                Azure is processing the saved audio. You can close this comparison and reopen it
                later.
              </p>
            )}
            {value?.stale && (
              <p role="alert">
                The recording or transcript changed. Create a fresh improvement before applying.
              </p>
            )}
            {value?.status === 'failed' && (
              <p role="alert">{value.error} Your original transcript is retained.</p>
            )}
            {value?.resolved && (
              <p>This candidate has already been reviewed. Your selected transcript is retained.</p>
            )}
            {value?.candidate_id && !value.resolved && (
              <>
                <p>
                  Confirm each speaker match. Suggestions use timing overlap, not verified voice
                  identity. Uncertain voices should stay separate.
                </p>
                {recovered.map((speaker) => (
                  <label className="improvement-mapping" key={speaker}>
                    {recoveredNames[speaker]}
                    <select
                      aria-label={`Match ${recoveredNames[speaker]}`}
                      value={mapping[speaker] || ''}
                      onChange={(event) =>
                        setMapping({ ...mapping, [speaker]: event.target.value })
                      }
                    >
                      <option value="">Choose a speaker match</option>
                      <option value="__new">Keep separate speaker — identity unconfirmed</option>
                      {original.map((target) => (
                        <option key={target} value={target}>
                          {names[target]}
                          {value.suggestions[speaker] === target ? ' (suggested)' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
                {!recovered.length && (
                  <p>
                    No speaker identities were returned. The candidate will remain unidentified.
                  </p>
                )}
                <div className="improvement-columns">
                  <div>
                    <h3>Original transcript</h3>
                    {value.original.map((s) => (
                      <TranscriptEntry
                        key={s.id}
                        segment={s}
                        speakerLabel={s.speaker_id ? names[s.speaker_id] : undefined}
                      />
                    ))}
                  </div>
                  <div>
                    <h3>Audio-based candidate</h3>
                    {value.candidate.map((s) => (
                      <TranscriptEntry
                        key={s.id}
                        segment={s}
                        speakerLabel={s.speaker_id ? recoveredNames[s.speaker_id] : undefined}
                      />
                    ))}
                  </div>
                </div>
                <div className="notes-header-actions">
                  <button
                    className="secondary"
                    disabled={busy || value.stale || !!running}
                    onClick={() => void apply(true)}
                  >
                    Keep original transcript
                  </button>
                  <button
                    className="primary"
                    disabled={
                      busy ||
                      value.stale ||
                      !!running ||
                      recovered.some((speaker) => !mapping[speaker])
                    }
                    onClick={() => void apply(false)}
                  >
                    Apply reviewed transcript
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
