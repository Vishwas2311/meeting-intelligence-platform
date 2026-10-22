import { Pencil, UserRound } from 'lucide-react';
import { formatElapsed } from './stopwatch';
import type { Segment } from './types';

export function TranscriptEntry({
  segment,
  pending = false,
  speakerLabel,
  onRename,
}: {
  segment: Segment;
  pending?: boolean;
  speakerLabel?: string;
  onRename?: (speakerId: string) => void;
}) {
  const raw = segment.speaker_id?.trim();
  const speaker = raw && !/^(unknown|unidentified)$/i.test(raw) ? raw : null;
  return (
    <article className={`transcript-entry${pending ? ' interim' : ''}`}>
      <span className="transcript-avatar" aria-hidden="true">
        <UserRound size={17} />
      </span>
      <div className="transcript-entry-body">
        <header className="transcript-entry-heading">
          <strong>{speakerLabel || speaker || 'Unidentified speaker'}</strong>
          {speaker && onRename && !pending && (
            <button
              className="speaker-rename"
              title="Rename speaker"
              aria-label={`Rename ${speakerLabel || speaker}`}
              onClick={() => onRename(speaker)}
            >
              <Pencil size={12} />
            </button>
          )}
          <time
            dateTime={`PT${segment.start_ms / 1000}S`}
            title={`${formatElapsed(segment.start_ms)} – ${formatElapsed(segment.end_ms)}`}
          >
            {formatElapsed(segment.start_ms)}
          </time>
          {pending && <span className="transcript-pending">Recognizing…</span>}
        </header>
        <p>{segment.text}</p>
      </div>
    </article>
  );
}
