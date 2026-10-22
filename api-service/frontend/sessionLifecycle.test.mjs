import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

test('logout waits for recorder finalization and committed local writes', async () => {
  const source = await readFile(new URL('./src/sessionLifecycle.ts', import.meta.url), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  const lifecycle = await import(
    'data:text/javascript;base64,' + Buffer.from(js).toString('base64')
  );
  let committed = false,
    release;
  const writing = new Promise((resolve) => {
    release = resolve;
  });
  class Recorder extends EventTarget {
    state = 'recording';
    stop() {
      this.state = 'inactive';
      queueMicrotask(() => this.dispatchEvent(new Event('stop')));
    }
  }
  const recorder = new Recorder();
  lifecycle.registerSessionDrain(async () => {
    await lifecycle.finishRecorder(recorder);
    await writing;
    committed = true;
  });
  const closing = lifecycle.closeLocalSession();
  assert.equal(lifecycle.sessionClosing(), true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(committed, false);
  release();
  await closing;
  assert.equal(recorder.state, 'inactive');
  assert.equal(committed, true);
});
