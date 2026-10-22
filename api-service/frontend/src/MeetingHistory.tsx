import { RefreshCcw, NotebookPen, ChevronRight, History, AudioLines } from 'lucide-react';
import { languageLabel } from './transcriber';
import type { Meeting } from './types';
import type { LocalRecapDraft } from './recapDraft';

type Props = {
  meetings: Meeting[];
  localNotes: LocalRecapDraft[];
  cachedCount: number;
  historyOffline: boolean;
  storageInfo: string;
  historyQuery: string;
  historyMore: boolean;
  onReload: (more?: boolean) => void;
  onSavedCopies: () => Promise<void>;
  onSearch: () => void;
  onQuery: (query: string) => void;
  onOpenLocal: (id: string) => void;
  onOpenMeeting: (id: string) => Promise<void>;
  sourceLabel: (source: string) => string;
};
export function MeetingHistory({
  meetings,
  localNotes,
  cachedCount,
  historyOffline,
  storageInfo,
  historyQuery,
  historyMore,
  onReload,
  onSavedCopies,
  onSearch,
  onQuery,
  onOpenLocal,
  onOpenMeeting,
  sourceLabel,
}: Props) {
  return (
    <section className="panel history-panel">
      <div className="panel-heading">
        <div>
          <h3>Previous meetings</h3>
          <p>
            {meetings.length} meetings · {cachedCount}/10 saved for offline viewing.{' '}
            {historyOffline
              ? 'Showing offline copies.'
              : 'Backend history is retained beyond 10 meetings.'}
          </p>
          <p>{storageInfo}</p>
        </div>
        <button className="secondary small" onClick={() => onReload()}>
          <RefreshCcw size={14} />
          Reload
        </button>
        <button className="secondary small" disabled={!cachedCount} onClick={onSavedCopies}>
          Saved copies
        </button>
      </div>
      <form
        className="history-search"
        onSubmit={(e) => {
          e.preventDefault();
          onSearch();
        }}
      >
        <input
          aria-label="Search meetings"
          placeholder="Search meeting titles"
          value={historyQuery}
          onChange={(e) => onQuery(e.target.value)}
        />
        <button className="secondary small">Search</button>
      </form>
      <div className="meeting-list">
        {localNotes
          .filter((item) => item.title.toLowerCase().includes(historyQuery.toLowerCase()))
          .map((item) => (
            <button
              className="meeting-row"
              key={`local-${item.id}`}
              onClick={() => onOpenLocal(item.id)}
            >
              <span className="meeting-row-icon">
                <NotebookPen size={19} />
              </span>
              <div>
                <b>{item.title || 'Recovered meeting notes'}</b>
                <small>
                  Meeting Notes ·{' '}
                  {item.updatedAt
                    ? new Date(item.updatedAt).toLocaleString()
                    : 'Recovered local draft'}
                </small>
              </div>
              <span className="pill">On this device</span>
              <ChevronRight size={17} />
            </button>
          ))}
        {meetings.length === 0 && localNotes.length === 0 ? (
          <div className="empty">
            <History size={28} />
            <b>No meetings yet</b>
            <span>Your recorded sessions will appear here.</span>
          </div>
        ) : (
          meetings
            .filter((m) => !localNotes.some((item) => item.id === m.id))
            .map((m) => (
              <button className="meeting-row" key={m.id} onClick={() => void onOpenMeeting(m.id)}>
                <span className="meeting-row-icon">
                  <AudioLines size={19} />
                </span>
                <div>
                  <b>{m.title}</b>
                  <small>
                    {sourceLabel(m.source)} · {languageLabel(m.language)} ·{' '}
                    {m.created_at ? new Date(m.created_at).toLocaleString() : m.id.slice(0, 8)}
                  </small>
                </div>
                <span className="pill">
                  {m.status === 'ready_review' ? 'Minutes saved' : m.status.replaceAll('_', ' ')}
                </span>
                <ChevronRight size={17} />
              </button>
            ))
        )}
      </div>
      {historyMore && !historyOffline && (
        <button className="secondary small" onClick={() => onReload(true)}>
          Load older meetings
        </button>
      )}
    </section>
  );
}
