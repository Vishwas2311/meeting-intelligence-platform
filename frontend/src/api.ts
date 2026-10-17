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
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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
  });
  let data: unknown;
  try {
    data = await r.json();
  } catch {
    data = { detail: `HTTP ${r.status}` };
  }
  if (!r.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${r.status})`);
  }
  return data as T;
}
export async function sendChunk(mid: string, seq: number, start: number, end: number, blob: Blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  const sha = Array.from(new Uint8Array(hash), (v) => v.toString(16).padStart(2, '0')).join('');
  const form = new FormData();
  form.append('sequence', String(seq));
  form.append('start_ms', String(start));
  form.append('end_ms', String(end));
  form.append('sha256', sha);
  form.append('audio', blob, `chunk-${seq}.webm`);
  const r = await fetch(`${API}/api/meetings/${mid}/audio-chunks`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  if (!r.ok) throw new Error(`Audio sync failed: ${r.status} ${await r.text()}`);
  return r.json();
}
