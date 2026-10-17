import type { Segment } from './types';

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
    const r = indexedDB.open(dbName, 1);
    r.onupgradeneeded = () => {
      const d = r.result;
      if (!d.objectStoreNames.contains(table)) d.createObjectStore(table, { keyPath: 'id' });
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
): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    try {
      const transaction = db.transaction(table, mode);
      const request = act(transaction.objectStore(table));
      // Request success precedes commit; only committed chunks are safe to acknowledge.
      transaction.oncomplete = () => {
        db.close();
        resolve(request.result);
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
export function saveChunk(c: PendingChunk) {
  return txn('readwrite', (store) => store.put(c));
}
export function removeChunk(id: string) {
  return txn('readwrite', (store) => store.delete(id));
}
export function pendingChunks(): Promise<PendingChunk[]> {
  return txn('readonly', (store) => store.getAll());
}
const segmentsTable = 'meeting-intelligence-segments';
export function localSegmentsKey(mid: string) {
  return `${segmentsTable}-${mid}`;
}
export function savePendingSegment(mid: string, seg: Segment) {
  const k = localSegmentsKey(mid);
  const all = loadPendingSegments(mid);
  if (!all.some((x) => x.client_event_id === seg.client_event_id)) {
    all.push(seg);
    localStorage.setItem(k, JSON.stringify(all));
  }
}
export function loadPendingSegments(mid: string): Segment[] {
  return JSON.parse(localStorage.getItem(localSegmentsKey(mid)) || '[]');
}
export function dropPendingSegment(mid: string, eventId: string) {
  const k = localSegmentsKey(mid);
  localStorage.setItem(
    k,
    JSON.stringify(loadPendingSegments(mid).filter((x) => x.client_event_id !== eventId)),
  );
}
