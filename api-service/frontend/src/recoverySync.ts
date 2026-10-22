import { request, sendChunk, ApiError, errorMessage } from './api';
import {
  pendingLosses,
  removeLoss,
  pendingChunks,
  removeChunk,
  pendingSegments,
  dropPendingSegment,
  pendingRecoveryIds,
  recordingManifests,
  acknowledgeManifest,
  pendingStops,
  removePendingStop,
} from './offline';
import type { Meeting } from './types';

/** Synchronize only acknowledged owner records; leave failed data in the encrypted outbox. */
export async function syncRecovery({
  onCount,
  onWarning,
  onStopped,
}: {
  onCount: (count: number) => void;
  onWarning: (message: string) => void;
  onStopped: (meeting: Meeting) => void;
}) {
  for (const loss of await pendingLosses()) {
    const ack = await request<{ id: string; saved: boolean }>(
      `/api/meetings/${loss.meetingId}/loss-events`,
      'POST',
      loss.event,
    );
    if (ack.id !== loss.id || ack.saved !== true)
      throw new Error('Transcription interruption was not acknowledged; recovery state retained.');
    await removeLoss(loss.id);
  }
  const items = await pendingChunks();
  onCount(items.length);
  for (const c of items.sort((a, b) => a.sequence - b.sequence)) {
    try {
      await sendChunk(c.meetingId, c.sequence, c.startMs, c.endMs, c.blob);
      await removeChunk(c.id);
    } catch (error) {
      onWarning(`Audio upload pending: ${errorMessage(error)}`);
      if (
        !(error instanceof ApiError) ||
        [401, 403, 429].includes(error.status) ||
        error.status >= 500
      )
        return;
    }
  }
  for (const queued of await pendingSegments()) {
    const { meetingId: mid, segment: s } = queued;
    try {
      const ack = await request<{ accepted: number; duplicates: number }>(
        `/api/meetings/${mid}/segments`,
        'POST',
        {
          segments: [s],
        },
      );
      if (
        !ack ||
        !Number.isInteger(ack.accepted) ||
        !Number.isInteger(ack.duplicates) ||
        ack.accepted + ack.duplicates !== 1
      )
        throw new ApiError(
          'Backend did not confirm the transcript upload. The text remains saved locally.',
          502,
        );
      await dropPendingSegment(mid, s.client_event_id);
    } catch (error) {
      onWarning(`Transcript upload pending: ${errorMessage(error)}`);
      if (
        !(error instanceof ApiError) ||
        [401, 403, 429].includes(error.status) ||
        error.status >= 500
      )
        return;
    }
  }
  onCount((await pendingChunks()).length);
  const remainingAudio = await pendingRecoveryIds('chunks');
  const remainingText = await pendingRecoveryIds('segments');
  onCount(remainingAudio.length);
  for (const value of await recordingManifests()) {
    if (value.synced || remainingAudio.some((item) => item.meetingId === value.id)) continue;
    const ack = await request<{ saved: boolean }>(
      `/api/meetings/${value.id}/manifest`,
      'POST',
      value.data,
    );
    if (ack.saved !== true) throw new Error('Recording manifest was not acknowledged');
    await acknowledgeManifest(value);
  }
  for (const stop of await pendingStops()) {
    if (
      remainingAudio.some((item) => item.meetingId === stop.id) ||
      remainingText.some((item) => item.meetingId === stop.id) ||
      (await recordingManifests()).some((item) => item.id === stop.id && !item.synced)
    )
      continue;
    try {
      const m = await request<Meeting>(`/api/meetings/${stop.id}/stop`, 'POST');
      if (m.id !== stop.id || !['stopped', 'processing', 'ready_review'].includes(m.status))
        throw new ApiError(
          'Backend did not confirm the meeting stop. The stop request remains saved locally.',
          502,
        );
      await removePendingStop(stop.id);
      onStopped(m);
    } catch (error) {
      onWarning(`Meeting stop is saved locally and will retry: ${errorMessage(error)}`);
    }
  }
  if (!remainingAudio.length && !remainingText.length && !(await pendingStops()).length)
    onWarning('');
}
