import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('./src/api.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const moduleUrl =
  'data:text/javascript;base64,' +
  Buffer.from(outputText.replaceAll('import.meta.env', '{DEV:true}')).toString('base64');

test('queued fractional chunk timestamps upload as integers without changing the audio', async (t) => {
  const previousFetch = globalThis.fetch;
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    value: { removeItem() {}, getItem: () => '' },
  });
  t.after(() => {
    globalThis.fetch = previousFetch;
    if (previousStorage) Object.defineProperty(globalThis, 'sessionStorage', previousStorage);
    else delete globalThis.sessionStorage;
  });
  const { sendChunk, ApiError, request } = await import(moduleUrl);
  const audio = new Blob(['synthetic-audio'], { type: 'audio/webm;codecs=opus' });
  let form;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/meetings/test-meeting/audio-chunks');
    assert.equal(options.method, 'POST');
    form = options.body;
    return new Response(JSON.stringify({ id: 'synthetic-chunk', duplicate: false }), {
      status: 201,
    });
  };
  await sendChunk('test-meeting', 1, 5039.600000023842, 10080.3999999, audio);
  assert.equal(form.get('start_ms'), '5040');
  assert.equal(form.get('end_ms'), '10080');
  assert.equal(form.get('sequence'), '1');
  assert.equal(await form.get('audio').text(), await audio.text());
  const hash = await crypto.subtle.digest('SHA-256', await audio.arrayBuffer());
  assert.equal(form.get('sha256'), Buffer.from(hash).toString('hex'));

  globalThis.fetch = async () => new Response('PRIVATE RESPONSE BODY', { status: 422 });
  await assert.rejects(sendChunk('test-meeting', 1, 0, 1, audio), (error) => {
    assert(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.match(error.message, /saved locally for retry/);
    assert(!error.message.includes('PRIVATE'));
    return true;
  });
  for (const invalid of [null, {}, { detail: 'Upload was not confirmed' }]) {
    globalThis.fetch = async () => new Response(JSON.stringify(invalid), { status: 200 });
    await assert.rejects(sendChunk('test-meeting', 1, 0, 1, audio), /did not confirm/);
  }

  globalThis.fetch = async () => new Response('<h1>Proxy failure</h1>', { status: 200 });
  await assert.rejects(request('/api/meetings'), /invalid response/);
  for (const invalid of [{ detail: 'Wrong shape' }, [null], [{ id: 'missing-fields' }]]) {
    globalThis.fetch = async () => new Response(JSON.stringify(invalid), { status: 200 });
    await assert.rejects(request('/api/meetings'), /invalid meeting list/);
  }
  for (const path of [
    '/api/meetings/test',
    '/api/meetings/test/transcript',
    '/api/meetings/test/jobs',
    '/api/meetings/test/mom',
  ]) {
    globalThis.fetch = async () => new Response('{}', { status: 200 });
    await assert.rejects(
      request(path),
      (error) => error instanceof ApiError && error.status === 502,
    );
  }
  globalThis.fetch = async () => new Response('Not authorized', { status: 401 });
  await assert.rejects(
    request('/api/meetings'),
    (error) => error instanceof ApiError && error.status === 401,
  );
});

test('conditional reads reuse validated content and logout clears the memory cache', async (t) => {
  const prior = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = prior;
  });
  const { request, setToken } = await import(moduleUrl);
  const value = { status: 'draft', data: { title: 'Synthetic', summary: 'Cached safely' } };
  globalThis.fetch = async () =>
    new Response(JSON.stringify(value), { headers: { ETag: '"one"' } });
  assert.deepEqual(await request('/api/meetings/cache/mom'), value);
  globalThis.fetch = async (_, options) => {
    assert.equal(options.headers['If-None-Match'], '"one"');
    return new Response(null, { status: 304 });
  };
  assert.deepEqual(await request('/api/meetings/cache/mom'), value);
  setToken('');
  globalThis.fetch = async (_, options) => {
    assert.equal(options.headers['If-None-Match'], undefined);
    return new Response(JSON.stringify(value));
  };
  await request('/api/meetings/cache/mom');
});

test('logout rejects delayed response bodies and upload acknowledgements from the old session', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = previousFetch;
  });
  const api = await import(moduleUrl);
  const audio = new Blob(['synthetic audio']);
  const value = {
    status: 'draft',
    data: { title: 'Synthetic', summary: 'Old private content' },
    id: 'synthetic-chunk',
    duplicate: false,
  };
  for (const [read, call] of [
    ['json', () => api.request('/api/meetings/logout-race/mom')],
    ['json', () => api.uploadRecapAudio('synthetic', audio, 'notes.webm')],
    ['json', () => api.sendChunk('synthetic', 0, 0, 1, audio)],
    ['blob', () => api.getRecapAudio('synthetic')],
    ['blob', () => api.downloadBackup()],
  ]) {
    let entered, release;
    const reading = new Promise((resolve) => {
      entered = resolve;
    });
    const body = new Promise((resolve) => {
      release = resolve;
    });
    api.setToken('synthetic-active-session');
    globalThis.fetch = async () => {
      const response = new Response(JSON.stringify(value), { headers: { ETag: '"private"' } });
      response[read] = () => {
        entered();
        return body;
      };
      return response;
    };
    const pending = call();
    await reading;
    api.setToken('');
    release(read === 'blob' ? audio : value);
    await assert.rejects(pending, (error) => error instanceof api.ApiError && error.status === 401);
  }
  globalThis.fetch = async (_, options) => {
    assert.equal(options.headers['If-None-Match'], undefined);
    return new Response(JSON.stringify(value));
  };
  await api.request('/api/meetings/logout-race/mom');
});

test('notes uploads require persistence acknowledgements and preserve ingress overload status', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = previousFetch;
  });
  const { uploadRecapAudio, ApiError } = await import(moduleUrl);
  const audio = new Blob(['synthetic audio']);
  globalThis.fetch = async () => new Response('<h1>Overload</h1>', { status: 429 });
  await assert.rejects(
    uploadRecapAudio('notes', audio, 'notes.webm'),
    (error) => error instanceof ApiError && error.status === 429,
  );
  for (const invalid of [null, {}, { status: 'pending' }, { status: 'saved' }]) {
    globalThis.fetch = async () => new Response(JSON.stringify(invalid));
    await assert.rejects(uploadRecapAudio('notes', audio, 'notes.webm'), /did not confirm/);
  }
  globalThis.fetch = async () => new Response(JSON.stringify({ status: 'saved' }));
  assert.equal((await uploadRecapAudio('notes', audio, 'notes.webm', false)).status, 'saved');
  globalThis.fetch = async () => new Response(JSON.stringify({ status: 'pending', job_id: 'job' }));
  assert.equal((await uploadRecapAudio('notes', audio, 'notes.webm')).job_id, 'job');
});
