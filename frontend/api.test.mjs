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
