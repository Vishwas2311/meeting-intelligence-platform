export const localDevelopment = import.meta.env.DEV;
const API = localDevelopment ? '' : import.meta.env.VITE_API_URL || 'http://localhost:8000';
const tokenKey = 'meeting-intelligence-dev-token';
if (localDevelopment) sessionStorage.removeItem(tokenKey);
export function setToken(token: string) {
  sessionStorage.setItem(tokenKey, token);
}
export function getToken() {
  return sessionStorage.getItem(tokenKey) || '';
}
function authHeaders(token = getToken()): Record<string, string> {
  return localDevelopment ? {} : { Authorization: `Bearer ${token}` };
}
export async function downloadBackup() {
  const response = await fetch(`${API}/api/storage/backup`, {
    method: 'POST',
    headers: authHeaders(),
    signal: AbortSignal.timeout(120000),
  });
  if (!response.ok)
    throw new Error('Backup could not be verified. Check saved audio and available disk space.');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = 'meeting-intelligence-backup.zip';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function isMeeting(value: unknown) {
  return (
    isRecord(value) &&
    [
      'id',
      'title',
      'source',
      'language',
      'status',
      'capture_status',
      'stt_status',
      'sync_status',
    ].every((field) => typeof value[field] === 'string')
  );
}

export async function request<T = unknown>(
  path: string,
  method = 'GET',
  body?: unknown,
  token = getToken(),
): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...authHeaders(token),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(path === '/api/speech/token' ? 30000 : 15000),
  });
  let data: unknown;
  try {
    data = await r.json();
  } catch {
    throw new ApiError(
      r.ok
        ? 'Backend returned an invalid response. Retry after checking the backend.'
        : `Request failed (HTTP ${r.status})`,
      r.ok ? 502 : r.status,
    );
  }
  if (!r.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
    throw new ApiError(
      typeof detail === 'string' ? detail : `Request failed (${r.status})`,
      r.status,
    );
  }
  if (
    path.split('?')[0] === '/api/meetings' &&
    method === 'GET' &&
    (!Array.isArray(data) || !data.every(isMeeting))
  )
    throw new ApiError(
      'Backend returned an invalid meeting list. Retry after checking the backend.',
      502,
    );
  if (
    ((path === '/api/meetings' && method === 'POST') ||
      /^\/api\/meetings\/[^/]+(?:\/(?:start|stop|pause|resume|capture-status))?$/.test(path)) &&
    !isMeeting(data)
  )
    throw new ApiError('Backend returned invalid meeting details. Your saved data was kept.', 502);
  if (
    path.endsWith('/transcript') &&
    (!isRecord(data) ||
      !Array.isArray(data.segments) ||
      !data.segments.every(
        (item) =>
          isRecord(item) &&
          typeof item.client_event_id === 'string' &&
          typeof item.text === 'string' &&
          Number.isFinite(item.start_ms) &&
          Number.isFinite(item.end_ms),
      ))
  )
    throw new ApiError('Backend returned an invalid transcript. Your saved data was kept.', 502);
  if (
    path.endsWith('/jobs') &&
    (!Array.isArray(data) ||
      !data.every(
        (item) =>
          isRecord(item) &&
          typeof item.id === 'string' &&
          typeof item.status === 'string' &&
          typeof item.kind === 'string',
      ))
  )
    throw new ApiError(
      'Backend returned invalid processing status. Retry after checking the backend.',
      502,
    );
  if (
    path.endsWith('/mom') &&
    (!isRecord(data) ||
      typeof data.status !== 'string' ||
      !isRecord(data.data) ||
      typeof data.data.title !== 'string' ||
      typeof data.data.summary !== 'string')
  )
    throw new ApiError('Backend returned invalid minutes. Your current draft was kept.', 502);
  return data as T;
}
export async function sendChunk(mid: string, seq: number, start: number, end: number, blob: Blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  const sha = Array.from(new Uint8Array(hash), (v) => v.toString(16).padStart(2, '0')).join('');
  const form = new FormData();
  form.append('sequence', String(seq));
  // Normalize here too so older fractional timestamps in the recovery queue can upload.
  form.append('start_ms', String(Math.round(start)));
  form.append('end_ms', String(Math.round(end)));
  form.append('sha256', sha);
  form.append('audio', blob, `chunk-${seq}.webm`);
  const r = await fetch(`${API}/api/meetings/${mid}/audio-chunks`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
    signal: AbortSignal.timeout(60000),
  });
  if (!r.ok)
    throw new ApiError(
      `Audio upload failed (HTTP ${r.status}). The recording remains saved locally for retry.`,
      r.status,
    );
  const ack: unknown = await r.json().catch(() => null);
  if (!isRecord(ack) || typeof ack.id !== 'string' || typeof ack.duplicate !== 'boolean')
    throw new ApiError(
      'Backend did not confirm the audio upload. The recording remains saved locally for retry.',
      502,
    );
  return ack;
}
