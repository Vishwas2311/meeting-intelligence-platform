export const localDevelopment = import.meta.env.DEV;
const API = '';
const tokenKey = 'meeting-intelligence-dev-token';
sessionStorage.removeItem(tokenKey);
const responseCache = new Map<string, { etag: string; data: unknown }>();
let sessionGeneration = 0;
export function setToken(token: string) {
  accessToken = token;
  if (!token) {
    sessionGeneration++;
    responseCache.clear();
  }
}
let accessToken = '';
let tokenProvider: (() => Promise<string>) | undefined;
export function setTokenProvider(provider: () => Promise<string>) {
  tokenProvider = provider;
}
export function getToken() {
  return accessToken;
}
async function authHeaders(token?: string): Promise<Record<string, string>> {
  return localDevelopment
    ? {}
    : { Authorization: `Bearer ${token ?? (tokenProvider ? await tokenProvider() : getToken())}` };
}
function requireCurrentSession(generation: number) {
  if (generation !== sessionGeneration)
    throw new ApiError('Your session changed. Saved data was kept; sign in again.', 401);
}
async function sessionFetch(
  generation: number,
  path: string,
  options: RequestInit & { headers?: Record<string, string> },
  token?: string,
) {
  requireCurrentSession(generation);
  const headers = await authHeaders(token);
  requireCurrentSession(generation);
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { ...headers, ...options.headers },
  });
  requireCurrentSession(generation);
  return response;
}
export async function downloadBackup() {
  const generation = sessionGeneration;
  const response = await sessionFetch(generation, '/api/storage/backup', {
    method: 'POST',
    signal: AbortSignal.timeout(120000),
  });
  if (!response.ok)
    throw new Error('Backup could not be verified. Check saved audio and available disk space.');
  const blob = await response.blob();
  requireCurrentSession(generation);
  const url = URL.createObjectURL(blob);
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

export async function uploadRecapAudio(
  mid: string,
  blob: Blob,
  filename: string,
  transcribe = true,
) {
  const generation = sessionGeneration;
  const body = new FormData();
  body.append('audio', blob, filename);
  body.append('transcribe', String(transcribe));
  const response = await sessionFetch(generation, `/api/recaps/${encodeURIComponent(mid)}/audio`, {
    method: 'POST',
    body,
    signal: AbortSignal.timeout(120000),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    requireCurrentSession(generation);
    throw new ApiError(
      response.ok
        ? 'Voice note upload returned an invalid response. Your local copy is retained.'
        : `Voice note upload failed (HTTP ${response.status}). Your local copy is retained.`,
      response.ok ? 502 : response.status,
    );
  }
  requireCurrentSession(generation);
  if (!response.ok)
    throw new ApiError(
      isRecord(data) && typeof data.detail === 'string'
        ? data.detail
        : `Voice note upload failed (${response.status})`,
      response.status,
    );
  if (
    !isRecord(data) ||
    (transcribe
      ? typeof data.job_id !== 'string' ||
        !data.job_id ||
        !['pending', 'running', 'completed', 'failed'].includes(String(data.status))
      : data.status !== 'saved')
  )
    throw new ApiError(
      'Backend did not confirm the voice note upload. Your local copy is retained.',
      502,
    );
  return data as { job_id?: string; status: string };
}

export async function getRecapAudio(mid: string) {
  const generation = sessionGeneration;
  const response = await sessionFetch(generation, `/api/recaps/${encodeURIComponent(mid)}/audio`, {
    signal: AbortSignal.timeout(120000),
  });
  if (!response.ok)
    throw new ApiError('The saved voice note could not be loaded.', response.status);
  const blob = await response.blob();
  requireCurrentSession(generation);
  return blob;
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
  token?: string,
): Promise<T> {
  const generation = sessionGeneration;
  const cacheable =
    method === 'GET' && /^\/api\/meetings\/[^/]+\/(?:mom|transcript)(?:\?|$)/.test(path);
  const cached = cacheable ? responseCache.get(path) : undefined;
  const r = await sessionFetch(
    generation,
    path,
    {
      method,
      headers: {
        ...(cached ? { 'If-None-Match': cached.etag } : {}),
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(path === '/api/speech/token' ? 30000 : 15000),
    },
    token,
  );
  requireCurrentSession(generation);
  if (r.status === 304 && cached) return structuredClone(cached.data) as T;
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
  requireCurrentSession(generation);
  if (!r.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
    throw new ApiError(
      typeof detail === 'string'
        ? detail
        : r.status === 409
          ? 'This record changed in another session. Your draft was kept. Reload the latest version before saving again.'
          : `Request failed (${r.status})`,
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
    path.split('?')[0].endsWith('/transcript') &&
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
  const etag = r.headers.get('etag');
  if (cacheable && etag) {
    if (responseCache.size >= 64) responseCache.delete(responseCache.keys().next().value!);
    responseCache.set(path, { etag, data: structuredClone(data) });
  }
  return data as T;
}
export async function sendChunk(mid: string, seq: number, start: number, end: number, blob: Blob) {
  const generation = sessionGeneration;
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
  const r = await sessionFetch(generation, `/api/meetings/${mid}/audio-chunks`, {
    method: 'POST',
    body: form,
    signal: AbortSignal.timeout(60000),
  });
  if (!r.ok)
    throw new ApiError(
      `Audio upload failed (HTTP ${r.status}). The recording remains saved locally for retry.`,
      r.status,
    );
  const ack: unknown = await r.json().catch(() => null);
  requireCurrentSession(generation);
  if (!isRecord(ack) || typeof ack.id !== 'string' || typeof ack.duplicate !== 'boolean')
    throw new ApiError(
      'Backend did not confirm the audio upload. The recording remains saved locally for retry.',
      502,
    );
  return ack;
}

export function reportClientEvent(
  kind: 'local_storage_failed' | 'recovery_failed' | 'capture_interrupted',
) {
  if (navigator.onLine && (localDevelopment || getToken()))
    void request('/api/client-events', 'POST', { kind }).catch(() => {});
}
