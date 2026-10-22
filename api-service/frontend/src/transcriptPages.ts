import { request } from './api';
import type { Segment } from './types';

export async function loadTranscript(mid: string) {
  const segments: Segment[] = [];
  let after: string | null = null;
  let speaker_names: Record<string, string> = {};
  let revision: number | undefined;
  for (let page = 0; page < 51; page++) {
    const value: {
      segments: Segment[];
      speaker_names: Record<string, string>;
      next_cursor?: string | null;
      evidence_revision?: number;
    } = await request(
      `/api/meetings/${encodeURIComponent(mid)}/transcript?limit=200${after ? `&after=${encodeURIComponent(after)}` : ''}`,
    );
    if (revision !== undefined && value.evidence_revision !== revision)
      throw new Error('Transcript changed while loading. Refresh to read one consistent version.');
    revision = value.evidence_revision;
    segments.push(...value.segments);
    speaker_names = value.speaker_names;
    if (!value.next_cursor) return { segments, speaker_names };
    if (value.next_cursor === after)
      throw new Error('Transcript cursor did not advance. Reload the meeting.');
    after = value.next_cursor;
  }
  throw new Error(
    'This legacy transcript exceeds the supported page limit. Contact your administrator.',
  );
}
