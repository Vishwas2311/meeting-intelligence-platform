import { databaseName, seal, unsealResult } from './localVault';
export interface LocalRecapDraft {
  id: string;
  title: string;
  meetingDate: string;
  participants: string;
  language: string;
  notes: string;
  reviewed: boolean;
  parts: Blob[];
  filename: string;
  interrupted: boolean;
  remote: boolean;
  serverRevision: number;
  createdAt?: string;
  updatedAt?: string;
  dirty?: boolean;
  liveIncomplete?: boolean;
  finalizationRequired?: boolean;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName('meeting-intelligence-personal-recap'), 1);
    request.onupgradeneeded = () => request.result.createObjectStore('draft');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function transaction<T>(
  mode: IDBTransactionMode,
  act: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const txn = db.transaction('draft', mode);
    const request = act(txn.objectStore('draft'));
    txn.oncomplete = () => {
      db.close();
      if (mode === 'readonly') void unsealResult(request.result, 'draft').then(resolve, reject);
      else resolve(request.result);
    };
    txn.onerror = txn.onabort = () => {
      db.close();
      reject(txn.error ?? new Error('Recap draft could not be saved locally.'));
    };
  });
}

export async function readRecapDraft(id?: string) {
  const key =
    id ?? (await transaction<string | undefined>('readonly', (store) => store.get('current')));
  return key
    ? transaction<LocalRecapDraft | undefined>('readonly', (store) => store.get(key))
    : undefined;
}
export async function saveRecapDraft(draft: LocalRecapDraft) {
  const encrypted = await seal(draft, 'draft');
  return transaction('readwrite', (store) => {
    store.put(draft.id, 'current');
    return store.put(encrypted, draft.id);
  });
}
export async function listRecapDrafts(): Promise<LocalRecapDraft[]> {
  const values = await transaction<unknown[]>('readonly', (store) => store.getAll());
  return values.filter(
    (value): value is LocalRecapDraft => !!value && typeof value === 'object' && 'id' in value,
  );
}
