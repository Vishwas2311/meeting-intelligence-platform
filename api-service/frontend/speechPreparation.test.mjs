import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('./src/speechPreparation.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { createSpeechPreparation } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);

test('Speech SDK and token prepare concurrently, deduplicate requests and reuse valid credentials', async () => {
  let sdkCalls = 0,
    tokenCalls = 0,
    release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const prepare = createSpeechPreparation(
    async () => {
      sdkCalls++;
      await gate;
      return 'sdk';
    },
    async () => {
      tokenCalls++;
      await gate;
      return { token: 'synthetic', region: 'test', expires_in_seconds: 600 };
    },
  );
  const first = prepare(),
    second = prepare();
  assert.equal(sdkCalls, 1);
  assert.equal(tokenCalls, 1);
  release();
  assert.deepEqual(await first, await second);
  await prepare();
  assert.equal(tokenCalls, 1);
});

test('Speech token refreshes before expiry and a failed refresh can retry', async (t) => {
  let now = 0,
    calls = 0;
  t.mock.method(Date, 'now', () => now);
  const prepare = createSpeechPreparation(
    async () => 'sdk',
    async () => {
      calls++;
      if (calls === 2) throw new Error('offline');
      return { token: `synthetic-${calls}`, region: 'test', expires_in_seconds: 600 };
    },
  );
  await prepare();
  now = 539000;
  await prepare();
  assert.equal(calls, 1);
  now = 540000;
  await assert.rejects(prepare(), /offline/);
  assert.equal((await prepare()).credentials.token, 'synthetic-3');
});

test('Failed SDK loading can retry without losing a valid token', async () => {
  let calls = 0;
  const prepare = createSpeechPreparation(
    async () => {
      if (++calls === 1) throw new Error('load failed');
      return 'sdk';
    },
    async () => ({ token: 'synthetic', region: 'test', expires_in_seconds: 600 }),
  );
  await assert.rejects(prepare(), /load failed/);
  assert.equal((await prepare()).speechsdk, 'sdk');
});
