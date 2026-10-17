import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('./src/offline.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { saveChunk, removeChunk, pendingChunks } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);

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
