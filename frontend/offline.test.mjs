import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('./src/offline.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const {
  saveChunk,
  removeChunk,
  pendingChunks,
  savePendingSegment,
  pendingSegments,
  cachedMeetings,
  cacheMeeting,
  savePendingStop,
  pendingStops,
  localSegmentsKey,
} = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));

// Model request success followed by a later commit or abort, as IndexedDB does.
async function beginTransaction(t, operation, result) {
  const request = { result };
  const transaction = {
    error: null,
    objectStore: () => ({ put: () => request, delete: () => request, getAll: () => request }),
  };
  let closed = false;
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'indexedDB');
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'indexedDB', previous);
    else delete globalThis.indexedDB;
  });
  globalThis.indexedDB = {
    open: () => {
      const opening = {
        result: {
          transaction: () => transaction,
          close: () => {
            closed = true;
          },
        },
      };
      queueMicrotask(() => opening.onsuccess());
      return opening;
    },
  };
  const promise = operation();
  queueMicrotask(() => request.onsuccess?.());
  await new Promise((resolve) => setImmediate(resolve));
  return { promise, transaction, closed: () => closed };
}

test('audio save waits for transaction commit before acknowledging persistence', async (t) => {
  const { promise, transaction, closed } = await beginTransaction(
    t,
    () => saveChunk({ id: 'a:0' }),
    'a:0',
  );
  let settled = false;
  promise.then(() => {
    settled = true;
  });
  await Promise.resolve();
  assert.equal(settled, false);
  transaction.oncomplete();
  assert.equal(await promise, 'a:0');
  assert.equal(closed(), true);
});

test('an aborted audio save rejects even after its request succeeded', async (t) => {
  const { promise, transaction, closed } = await beginTransaction(
    t,
    () => saveChunk({ id: 'a:0' }),
    'a:0',
  );
  const rejected = assert.rejects(promise, /storage full/);
  transaction.error = new Error('storage full');
  transaction.onabort();
  await rejected;
  assert.equal(closed(), true);
});

test('an aborted removal rejects so the queue is not reported synchronized', async (t) => {
  const { promise, transaction } = await beginTransaction(t, () => removeChunk('a:0'));
  const rejected = assert.rejects(promise, /aborted/);
  transaction.onabort();
  await rejected;
});

test('pending recordings are returned after a completed read transaction', async (t) => {
  const chunks = [{ id: 'a:0' }, { id: 'a:1' }];
  const { promise, transaction } = await beginTransaction(t, pendingChunks, chunks);
  transaction.oncomplete();
  assert.deepEqual(await promise, chunks);
});

function memoryStorage(t) {
  const stores = new Map();
  const legacy = new Map();
  const previous = new Map(
    ['indexedDB', 'localStorage'].map((key) => [
      key,
      Object.getOwnPropertyDescriptor(globalThis, key),
    ]),
  );
  t.after(() => {
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  globalThis.localStorage = {
    get length() {
      return legacy.size;
    },
    key: (i) => [...legacy.keys()][i] ?? null,
    getItem: (key) => legacy.get(key) ?? null,
    setItem: (key, value) => legacy.set(key, value),
    removeItem: (key) => legacy.delete(key),
  };
  globalThis.indexedDB = {
    open: () => {
      const opening = {
        result: {
          close() {},
          transaction(name) {
            if (!stores.has(name)) stores.set(name, new Map());
            const values = stores.get(name);
            const transaction = {
              objectStore: () => ({
                put(value) {
                  values.set(value.id, structuredClone(value));
                  return { result: value.id };
                },
                delete(id) {
                  values.delete(id);
                  return {};
                },
                getAll() {
                  const request = { result: [...values.values()].map((v) => structuredClone(v)) };
                  queueMicrotask(() => request.onsuccess?.());
                  return request;
                },
              }),
            };
            setImmediate(() => transaction.oncomplete());
            return transaction;
          },
        },
      };
      queueMicrotask(() => opening.onsuccess());
      return opening;
    },
  };
}

test('latest ten complete read copies survive cache replacement without trimming recovery outboxes', async (t) => {
  memoryStorage(t);
  await saveChunk({ id: 'old-audio', meetingId: 'm0', blob: new Blob(['synthetic']) });
  await savePendingSegment('m0', {
    client_event_id: 'old-event',
    text: 'Unuploaded transcript',
    start_ms: 0,
    end_ms: 1000,
  });
  await savePendingStop('m0');
  for (let i = 0; i < 12; i++)
    await cacheMeeting({
      id: `m${i}`,
      meeting: {
        id: `m${i}`,
        title: `Synthetic ${i}`,
        created_at: `2026-10-${String(i + 1).padStart(2, '0')}T10:00:00Z`,
      },
      segments: [{ client_event_id: 'one', text: `Transcript ${i}` }],
      minutes: { data: { summary: `Minutes ${i}` } },
      jobs: [],
    });
  const cached = await cachedMeetings();
  assert.equal(cached.length, 10);
  assert.equal(cached[0].id, 'm11');
  assert.equal(cached.at(-1).id, 'm2');
  assert.equal(cached[0].segments[0].text, 'Transcript 11');
  assert.equal(cached[0].minutes.data.summary, 'Minutes 11');
  assert.equal((await pendingChunks())[0].meetingId, 'm0');
  assert.equal((await pendingSegments())[0].meetingId, 'm0');
  assert.equal((await pendingStops())[0].id, 'm0');
});

test('legacy transcripts from every meeting migrate to the durable outbox and invalid originals are retained', async (t) => {
  memoryStorage(t);
  for (const id of ['a', 'b'])
    localStorage.setItem(
      localSegmentsKey(id),
      JSON.stringify([
        { client_event_id: 'event', text: `Synthetic ${id}`, start_ms: 0, end_ms: 1000 },
      ]),
    );
  assert.deepEqual((await pendingSegments()).map((item) => item.meetingId).sort(), ['a', 'b']);
  assert.equal(localStorage.length, 0);
  localStorage.setItem(localSegmentsKey('broken'), '{');
  await assert.rejects(pendingSegments());
  assert.equal(localStorage.getItem(localSegmentsKey('broken')), '{');
});
