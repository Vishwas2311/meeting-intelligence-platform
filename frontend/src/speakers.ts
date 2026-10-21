import type { Segment } from './types';

export function scopedSpeaker(session: string, raw?: string | null): string | null {
  const id = raw?.trim();
  return !id || /^(unknown|unidentified)$/i.test(id) ? null : `live:${session}:${id}`;
}

export function speakerLabels(segments: Segment[], names: Record<string, string>) {
  const labels = new Map<string, string>();
  let next = 1;
  for (const segment of segments) {
    const id = segment.speaker_id?.trim();
    if (!id || /^(unknown|unidentified)$/i.test(id) || labels.has(id)) continue;
    const fallback = id.startsWith('live:') ? `Speaker ${next++}` : id;
    labels.set(id, Object.prototype.hasOwnProperty.call(names, id) ? names[id] : fallback);
  }
  return Object.fromEntries(labels);
}
