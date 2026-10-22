import type { CachedMeeting, Segment } from './types';
import { databaseName, ownerKey, seal, unsealResult } from './localVault';

export interface PendingChunk {
  id: string;
  meetingId: string;
  sequence: number;
  startMs: number;
  endMs: number;
  blob: Blob;
}
const dbName = 'meeting-intelligence-recovery';
const table = 'chunks';
function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(databaseName(dbName), 3);
    r.onupgradeneeded = () => {
      const d = r.result;
      if (!d.objectStoreNames.contains(table)) d.createObjectStore(table, { keyPath: 'id' });
      for (const name of ['segments', 'history', 'stops', 'losses', 'manifests']) {
        if (!d.objectStoreNames.contains(name)) d.createObjectStore(name, { keyPath: 'id' });
      }
    };
    r.onsuccess = () => {
      r.result.onversionchange = () => r.result.close();
      resolve(r.result);
    };
    r.onerror = () => reject(r.error);
  });
}
async function txn<T>(
  mode: IDBTransactionMode,
  act: (store: IDBObjectStore) => IDBRequest<T>,
  storeName = table,
): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    try {
      const transaction = db.transaction(storeName, mode);
      const request = act(transaction.objectStore(storeName));
      // Request success precedes commit; only committed chunks are safe to acknowledge.
      transaction.oncomplete = () => {
        db.close();
        if (mode === 'readonly') void unsealResult(request.result, storeName).then(resolve, reject);
        else resolve(request.result);
      };
      transaction.onerror = transaction.onabort = () => {
        db.close();
        reject(transaction.error ?? new Error('Local recording transaction aborted'));
      };
    } catch (error) {
      db.close();
      reject(error);
    }
  });
}
export async function saveChunk(c: PendingChunk) {
  const encrypted = await seal(c, table);
  return txn('readwrite', (store) => store.put(encrypted));
}
export function removeChunk(id: string) {
  return txn('readwrite', (store) => store.delete(id));
}
export function pendingChunks(): Promise<PendingChunk[]> {
  if (!import.meta.env.PROD) return txn('readonly', (store) => store.getAll());
  return readRecoveryPage<PendingChunk>(table);
}

async function readRecoveryPage<T>(name: string): Promise<T[]> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(name, 'readonly');
    const cursor = transaction.objectStore(name).openCursor();
    const rows: unknown[] = [];
    let bytes = 0;
    cursor.onsuccess = () => {
      const item = cursor.result;
      if (!item) return;
      const size = String(item.value.ciphertext || '').length;
      // One record may exceed the page target, but is still bounded by the chunk limit.
      if (rows.length && bytes + size > 8 * 1024 ** 2) return;
      rows.push(item.value);
      bytes += size;
      if (rows.length < 20) item.continue();
    };
    transaction.oncomplete = () => {
      db.close();
      void unsealResult(rows, name).then((value) => resolve(value as T[]), reject);
    };
    transaction.onerror = transaction.onabort = () => {
      db.close();
      reject(transaction.error || new Error('Recovery read failed'));
    };
  });
}

export async function pendingRecoveryIds(name: 'chunks' | 'segments') {
  const ids = await txn<IDBValidKey[]>('readonly', (store) => store.getAllKeys(), name);
  return ids.map((id) => ({ meetingId: String(id).split(':')[0] }));
}
export async function savePendingStop(mid: string) {
  const encrypted = await seal({ id: mid }, 'stops');
  return txn('readwrite', (store) => store.put(encrypted), 'stops');
}
export function pendingStops(): Promise<{ id: string }[]> {
  return txn('readonly', (store) => store.getAll(), 'stops');
}
export function removePendingStop(mid: string) {
  return txn('readwrite', (store) => store.delete(mid), 'stops');
}

export interface RecordingManifest {
  expected_chunks: number;
  recorded_through_ms: number;
  finalized: boolean;
  pauses: { start_ms: number; end_ms: number }[];
}
export interface SavedManifest {
  id: string;
  version: string;
  synced: boolean;
  data: RecordingManifest;
}
export async function saveManifest(mid: string, data: RecordingManifest) {
  const encrypted = await seal(
    { id: mid, version: crypto.randomUUID(), synced: false, data },
    'manifests',
  );
  return txn('readwrite', (store) => store.put(encrypted), 'manifests');
}
export function recordingManifests(): Promise<SavedManifest[]> {
  return txn('readonly', (store) => store.getAll(), 'manifests');
}
export async function acknowledgeManifest(value: SavedManifest) {
  const db = await open();
  return new Promise<void>((resolve, reject) => {
    const transaction = db.transaction('manifests', 'readwrite');
    const store = transaction.objectStore('manifests');
    const request = store.get(value.id);
    request.onsuccess = () => {
      if (request.result?.version === value.version) store.put({ ...request.result, synced: true });
    };
    transaction.oncomplete = () => {
      db.close();
      resolve();
    };
    transaction.onerror = transaction.onabort = () => {
      db.close();
      reject(transaction.error);
    };
  });
}
export async function saveLoss(mid: string, at: number, reason: string) {
  const id = crypto.randomUUID();
  const encrypted = await seal(
    { id, meetingId: mid, event: { id, at_ms: Math.round(Math.max(0, at)), reason } },
    'losses',
  );
  return txn('readwrite', (store) => store.put(encrypted), 'losses');
}
export function pendingLosses(): Promise<
  { id: string; meetingId: string; event: { id: string; at_ms: number; reason: string } }[]
> {
  return txn('readonly', (store) => store.getAll(), 'losses');
}
export function removeLoss(id: string) {
  return txn('readwrite', (store) => store.delete(id), 'losses');
}
const segmentsTable = 'meeting-intelligence-segments';
export function localSegmentsKey(mid: string) {
  return ownerKey(`${segmentsTable}-${mid}`);
}
export async function savePendingSegment(mid: string, seg: Segment) {
  const encrypted = await seal(
    { id: `${mid}:${seg.client_event_id}`, meetingId: mid, segment: seg },
    'segments',
  );
  return txn('readwrite', (store) => store.put(encrypted), 'segments');
}
export async function pendingSegments(): Promise<
  { id: string; meetingId: string; segment: Segment }[]
> {
  // Upgrade the old localStorage outbox only after each IndexedDB write commits.
  if (import.meta.env.PROD) return readRecoveryPage('segments');
  const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)).filter(
    (key): key is string => !!key?.startsWith(`${segmentsTable}-`),
  );
  for (const key of keys) {
    const mid = key.slice(segmentsTable.length + 1);
    const raw: unknown = JSON.parse(localStorage.getItem(key) || '[]');
    if (!Array.isArray(raw))
      throw new Error('Invalid legacy transcript queue; keep this browser profile for recovery.');
    for (const segment of raw) {
      if (
        !segment ||
        typeof segment.client_event_id !== 'string' ||
        typeof segment.text !== 'string'
      ) {
        throw new Error('Invalid legacy transcript segment; recovery data was retained.');
      }
      await savePendingSegment(mid, segment);
    }
    localStorage.removeItem(key);
  }
  return txn('readonly', (store) => store.getAll(), 'segments');
}
export function dropPendingSegment(mid: string, eventId: string) {
  return txn('readwrite', (store) => store.delete(`${mid}:${eventId}`), 'segments');
}

export function cachedMeetings(): Promise<CachedMeeting[]> {
  return txn<CachedMeeting[]>('readonly', (store) => store.getAll(), 'history').then((items) =>
    items.sort((a, b) =>
      (b.meeting.created_at || b.cachedAt).localeCompare(a.meeting.created_at || a.cachedAt),
    ),
  );
}

export async function cacheMeeting(value: Omit<CachedMeeting, 'cachedAt'>) {
  const cachedAt = new Date().toISOString();
  const encrypted = await seal({ ...value, cachedAt }, 'history');
  const db = await open();
  return new Promise<void>((resolve, reject) => {
    const transaction = db.transaction('history', 'readwrite');
    const store = transaction.objectStore('history');
    store.put({ ...encrypted, cache_order: value.meeting.created_at || cachedAt });
    const all = store.getAll();
    all.onsuccess = () => {
      const records = (all.result as (CachedMeeting & { cache_order?: string })[]).sort((a, b) =>
        (b.cache_order || b.meeting?.created_at || b.cachedAt).localeCompare(
          a.cache_order || a.meeting?.created_at || a.cachedAt,
        ),
      );
      // This trims only the browser read cache. Never delete backend history or pending uploads.
      for (const old of records.slice(10)) store.delete(old.id);
    };
    transaction.oncomplete = () => {
      db.close();
      resolve();
    };
    transaction.onerror = transaction.onabort = () => {
      db.close();
      reject(transaction.error || new Error('History could not be saved locally'));
    };
  });
}
