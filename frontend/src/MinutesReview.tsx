import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { TextareaHTMLAttributes } from 'react';
import {
  FileText,
  MessagesSquare,
  CheckCircle2,
  ListChecks,
  Flag,
  CircleHelp,
  History,
  RefreshCcw,
} from 'lucide-react';
import type { Minutes, Segment } from './types';
import { request, errorMessage } from './api';
import { TranscriptEntry } from './TranscriptEntry';
import { formatElapsed } from './stopwatch';

type Data = Minutes['data'];
type PointsKey = 'discussion_points' | 'risks' | 'unanswered_questions';
const sections = [
  { key: 'summary', title: 'Summary', icon: FileText },
  { key: 'discussion_points', title: 'Discussion', icon: MessagesSquare },
  { key: 'decisions', title: 'Decisions', icon: CheckCircle2 },
  { key: 'action_items', title: 'Action items', icon: ListChecks },
  { key: 'risks', title: 'Risks & plans', icon: Flag },
  { key: 'unanswered_questions', title: 'Open questions', icon: CircleHelp },
  { key: 'transcript', title: 'Transcript', icon: FileText },
] as const;
type SectionKey = (typeof sections)[number]['key'];

// Keep the document as the only scroll area, even when an edited field becomes long.
function GrowingTextarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null);
  function resize() {
    const field = ref.current;
    if (!field) return;
    field.style.height = 'auto';
    field.style.height = `${field.scrollHeight + field.offsetHeight - field.clientHeight}px`;
  }
  useLayoutEffect(resize, [props.value]);
  useEffect(() => {
    const field = ref.current;
    if (!field) return;
    let width = field.clientWidth;
    const observer = new ResizeObserver(() => {
      if (field.clientWidth !== width) {
        width = field.clientWidth;
        resize();
      }
    });
    observer.observe(field);
    return () => observer.disconnect();
  }, []);
  return <textarea {...props} ref={ref} rows={1} />;
}
export function MinutesReview({
  value,
  onChange,
  readOnly,
  segments,
  speakerLabels = {},
  onRenameSpeaker,
  meetingId,
  onViewingVersionChange,
  status,
  onRefresh,
}: {
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
  segments: Segment[];
  speakerLabels?: Record<string, string>;
  onRenameSpeaker?: (speakerId: string) => void;
  meetingId: string;
  onViewingVersionChange: (viewing: boolean) => void;
  status: string;
  onRefresh: () => void;
}) {
  const data: Data = JSON.parse(value);
  const [section, setSection] = useState<SectionKey>('summary');
  const scrollRef = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef<Partial<Record<SectionKey, HTMLElement>>>({});
  const [evidence, setEvidence] = useState<string | null>(null);
  const [versions, setVersions] = useState<
    { id: string; status: string; at: string; data: Data; provenance: { model?: string } }[]
  >([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [version, setVersion] = useState<Data | null>(null);
  useEffect(() => {
    onViewingVersionChange(!!version);
    return () => onViewingVersionChange(false);
  }, [version, onViewingVersionChange]);
  useEffect(() => {
    if (!evidence) return;
    const previous = document.activeElement as HTMLElement | null;
    return () => previous?.focus();
  }, [evidence]);
  const update = (changes: Partial<Data>) =>
    onChange(JSON.stringify({ ...data, ...changes }, null, 2));
  const shown = version ?? data;
  const evidenceSegment = segments.find((s) => s.id === evidence);
  function evidenceLinks(ids: unknown) {
    return Array.isArray(ids) && ids.length ? (
      <div className="evidence-links">
        {ids.map((id) => (
          <button key={String(id)} className="text-button" onClick={() => setEvidence(String(id))}>
            View supporting transcript
          </button>
        ))}
      </div>
    ) : (
      <small>No evidence linked</small>
    );
  }
  function items(key: 'decisions' | 'action_items') {
    const entries = shown[key] || [];
    return (
      <div className="minute-items">
        {entries.length ? (
          entries.map((item, index) => (
            <article className="minute-item" key={index}>
              <span className="item-number">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <GrowingTextarea
                  aria-label={`${key === 'decisions' ? 'Decision' : 'Action'} ${index + 1}`}
                  readOnly={readOnly || !!version}
                  value={String(
                    item.text ??
                      item.description ??
                      item.decision ??
                      item.action ??
                      item.task ??
                      '',
                  )}
                  onChange={(e) =>
                    update({
                      [key]: entries.map((v, i) =>
                        i === index ? { ...v, text: e.target.value } : v,
                      ),
                    })
                  }
                />
                {key === 'action_items' && (
                  <div className="action-meta">
                    <label>
                      Owner
                      <input
                        value={String(item.owner ?? '')}
                        placeholder="Not stated"
                        readOnly={readOnly || !!version}
                        onChange={(e) =>
                          update({
                            [key]: entries.map((v, i) =>
                              i === index ? { ...v, owner: e.target.value || null } : v,
                            ),
                          })
                        }
                      />
                    </label>
                    <label>
                      Due date
                      <input
                        value={String(item.due_date ?? '')}
                        placeholder="Not stated"
                        readOnly={readOnly || !!version}
                        onChange={(e) =>
                          update({
                            [key]: entries.map((v, i) =>
                              i === index ? { ...v, due_date: e.target.value || null } : v,
                            ),
                          })
                        }
                      />
                    </label>
                  </div>
                )}
                {evidenceLinks(item.source_segment_ids)}
                {!readOnly && !version && (
                  <details>
                    <summary>Edit evidence</summary>
                    <select
                      aria-label={`Evidence for ${key} ${index + 1}`}
                      multiple
                      value={
                        Array.isArray(item.source_segment_ids)
                          ? item.source_segment_ids.map(String)
                          : []
                      }
                      onChange={(e) =>
                        update({
                          [key]: entries.map((v, i) =>
                            i === index
                              ? {
                                  ...v,
                                  source_segment_ids: Array.from(
                                    e.target.selectedOptions,
                                    (o) => o.value,
                                  ),
                                }
                              : v,
                          ),
                        })
                      }
                    >
                      {segments.map((s) => (
                        <option key={s.id} value={s.id}>
                          {(s.speaker_id && speakerLabels[s.speaker_id]) ||
                            s.speaker_id?.trim() ||
                            'Unidentified speaker'}{' '}
                          · {formatElapsed(s.start_ms)} — {s.text.slice(0, 120)}
                        </option>
                      ))}
                    </select>
                    <button
                      className="text-button"
                      onClick={() => update({ [key]: entries.filter((_, i) => i !== index) })}
                    >
                      Remove item
                    </button>
                  </details>
                )}
              </div>
            </article>
          ))
        ) : (
          <p className="minute-empty">
            {key === 'decisions' ? 'No decisions were recorded.' : 'No actions were assigned.'}
          </p>
        )}
        {!readOnly && !version && (
          <button
            className="secondary small"
            onClick={() =>
              update({
                [key]: [
                  ...entries,
                  {
                    text: '',
                    source_segment_ids: [],
                    ...(key === 'action_items' ? { owner: null, due_date: null } : {}),
                  },
                ],
              })
            }
          >
            Add {key === 'decisions' ? 'decision' : 'action'}
          </button>
        )}
      </div>
    );
  }
  const locked = readOnly || !!version;
  function points(key: PointsKey, label: string) {
    const entries = shown[key] || [];
    return (
      <div className="minute-points">
        {entries.length ? (
          <ol>
            {entries.map((point, index) => (
              <li key={index}>
                {locked ? (
                  <span>{point}</span>
                ) : (
                  <div className="point-editor">
                    <GrowingTextarea
                      aria-label={`${label} ${index + 1}`}
                      value={point}
                      placeholder="Write a point…"
                      onChange={(e) =>
                        update({ [key]: entries.map((v, i) => (i === index ? e.target.value : v)) })
                      }
                    />
                    <button
                      className="remove-point"
                      aria-label={`Remove ${label.toLowerCase()} ${index + 1}`}
                      onClick={() => update({ [key]: entries.filter((_, i) => i !== index) })}
                    >
                      ×
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="minute-empty">Nothing recorded in this section.</p>
        )}
        {!locked && (
          <button
            className="text-button add-point"
            onClick={() => update({ [key]: [...entries, ''] })}
          >
            + Add point
          </button>
        )}
      </div>
    );
  }
  function jump(key: SectionKey) {
    setSection(key);
    // The transcript has its own reading view; wait for that view to mount before scrolling.
    requestAnimationFrame(() => {
      const node = sectionRefs.current[key];
      if (!node || !scrollRef.current) return;
      scrollRef.current.scrollTo({
        top: key === 'summary' || key === 'transcript' ? 0 : node.offsetTop - 20,
        behavior: 'instant',
      });
    });
  }
  return (
    <div className="minutes-document">
      <div className="minutes-document-toolbar">
        <div className="minutes-toolbar-label">
          <span className="pill">{status}</span>
          {version && <span className="minutes-toolbar-help">Viewing an earlier revision</span>}
        </div>
        <div>
          <button className="secondary small" onClick={onRefresh}>
            <RefreshCcw size={14} />
            Refresh
          </button>
          {version && (
            <button className="primary small" onClick={() => setVersion(null)}>
              Back to current draft
            </button>
          )}
          <button
            className="secondary small"
            onClick={async () => {
              setHistoryOpen(!historyOpen);
              if (!historyOpen)
                try {
                  setVersions(await request(`/api/meetings/${meetingId}/mom/revisions`));
                  setHistoryError('');
                } catch (error) {
                  setHistoryError(errorMessage(error));
                }
            }}
          >
            <History size={14} />
            Version history
          </button>
        </div>
      </div>
      {historyOpen && (
        <div className="revision-strip">
          {historyError ||
            (versions.length
              ? versions.map((v) => (
                  <button className="secondary small" key={v.id} onClick={() => setVersion(v.data)}>
                    {new Date(v.at).toLocaleString()} · {v.status}
                    {v.provenance.model ? ` · ${v.provenance.model}` : ''}
                  </button>
                ))
              : 'No earlier revisions.')}
        </div>
      )}
      <div className="minutes-layout">
        <nav className="minutes-outline" aria-label="Minutes sections">
          <span className="outline-label">IN THIS MEETING</span>
          {sections.map(({ key, title, icon: Icon }) => (
            <button
              key={key}
              aria-current={section === key ? 'location' : undefined}
              onClick={() => jump(key)}
            >
              <Icon size={16} />
              <span>{title}</span>
              {key !== 'summary' && (
                <small>{key === 'transcript' ? segments.length : shown[key]?.length || 0}</small>
              )}
            </button>
          ))}
        </nav>
        <div
          className="minutes-scroll"
          ref={scrollRef}
          onScroll={() => {
            if (section === 'transcript') return;
            const top = (scrollRef.current?.scrollTop || 0) + 60;
            const current = [...sections]
              .reverse()
              .find(
                ({ key }) =>
                  key !== 'transcript' && (sectionRefs.current[key]?.offsetTop ?? Infinity) <= top,
              );
            if (current) setSection(current.key);
          }}
        >
          <div className="minutes-paper">
            {section === 'transcript' ? (
              <section
                className="minute-card transcript-card"
                ref={(node) => {
                  if (node) sectionRefs.current.transcript = node;
                }}
              >
                <h3>
                  <FileText size={18} />
                  Transcript <span>{segments.length}</span>
                </h3>
                {segments.length ? (
                  segments.map((segment) => (
                    <TranscriptEntry
                      key={segment.id || segment.client_event_id}
                      segment={segment}
                      speakerLabel={
                        segment.speaker_id ? speakerLabels[segment.speaker_id] : undefined
                      }
                      onRename={version ? undefined : onRenameSpeaker}
                    />
                  ))
                ) : (
                  <p className="minute-empty">No transcript is saved for this meeting.</p>
                )}
              </section>
            ) : (
              <>
                <header className="document-title">
                  <GrowingTextarea
                    id="mom-title"
                    aria-label="Minutes title"
                    readOnly={locked}
                    value={shown.title}
                    onChange={(e) => update({ title: e.target.value })}
                  />
                </header>
                <section
                  className="minute-card summary-card"
                  ref={(node) => {
                    if (node) sectionRefs.current.summary = node;
                  }}
                >
                  <h3>
                    <FileText size={18} />
                    Meeting summary
                  </h3>
                  <GrowingTextarea
                    id="mom-editor"
                    aria-label="Meeting summary"
                    readOnly={readOnly || !!version}
                    value={shown.summary}
                    onChange={(e) => update({ summary: e.target.value })}
                  />
                </section>
                <section
                  className="minute-card discussion-card"
                  ref={(node) => {
                    if (node) sectionRefs.current.discussion_points = node;
                  }}
                >
                  <h3>
                    <MessagesSquare size={18} />
                    Discussion <span>{shown.discussion_points?.length || 0}</span>
                  </h3>
                  {points('discussion_points', 'Discussion point')}
                </section>
                <section
                  className="minute-card decisions-card"
                  ref={(node) => {
                    if (node) sectionRefs.current.decisions = node;
                  }}
                >
                  <h3>
                    <CheckCircle2 size={18} />
                    Decisions made <span>{shown.decisions?.length || 0}</span>
                  </h3>
                  {items('decisions')}
                </section>
                <section
                  className="minute-card actions-card"
                  ref={(node) => {
                    if (node) sectionRefs.current.action_items = node;
                  }}
                >
                  <h3>
                    <ListChecks size={18} />
                    Action items <span>{shown.action_items?.length || 0}</span>
                  </h3>
                  {items('action_items')}
                </section>
                <section
                  className="minute-card"
                  ref={(node) => {
                    if (node) sectionRefs.current.risks = node;
                  }}
                >
                  <h3>
                    <Flag size={18} />
                    Risks & plans <span>{shown.risks?.length || 0}</span>
                  </h3>
                  {points('risks', 'Risk or plan')}
                </section>
                <section
                  className="minute-card questions-card"
                  ref={(node) => {
                    if (node) sectionRefs.current.unanswered_questions = node;
                  }}
                >
                  <h3>
                    <CircleHelp size={18} />
                    Open questions <span>{shown.unanswered_questions?.length || 0}</span>
                  </h3>
                  {points('unanswered_questions', 'Open question')}
                </section>
              </>
            )}
          </div>
        </div>
      </div>
      {evidence && (
        <div
          className="evidence-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Supporting transcript"
          onKeyDown={(e) => {
            if (e.key === 'Escape') setEvidence(null);
            if (e.key === 'Tab') {
              e.preventDefault();
              e.currentTarget.querySelector('button')?.focus();
            }
          }}
        >
          <section className="minute-card">
            <h3>Supporting transcript</h3>
            {evidenceSegment ? (
              <TranscriptEntry
                segment={evidenceSegment}
                speakerLabel={
                  evidenceSegment.speaker_id ? speakerLabels[evidenceSegment.speaker_id] : undefined
                }
              />
            ) : (
              <p>
                No matching segment is available for this legacy reference. Review the original
                transcript.
              </p>
            )}
            <button autoFocus className="secondary small" onClick={() => setEvidence(null)}>
              Close transcript
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
