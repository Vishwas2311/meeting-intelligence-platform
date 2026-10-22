import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Use the existing compiler so this test also runs on the supported Node 20 runtime.
const source = await readFile(new URL('./src/stopwatch.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { Stopwatch, formatElapsed } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);

test('elapsed time follows the clock even when display ticks are delayed', () => {
  let now = 100;
  const clock = new Stopwatch(() => now);
  clock.resume();
  now = 1450;
  assert.equal(clock.read(), 1350);
  now = 11900;
  assert.equal(clock.read(), 11800);
});

test('pause freezes time, resume excludes the pause, stop keeps the final value, reset starts fresh', () => {
  let now = 0;
  const clock = new Stopwatch(() => now);
  clock.resume();
  now = 2100;
  clock.pause();
  now = 15200;
  assert.equal(clock.read(), 2100);
  clock.pause();
  clock.resume();
  clock.resume();
  now = 16900;
  clock.pause();
  assert.equal(clock.read(), 3800);
  now = 20000;
  assert.equal(clock.read(), 3800);
  clock.reset();
  assert.equal(clock.read(), 0);
});

test('formatting does not wrap at 24 hours or round up early', () => {
  assert.equal(formatElapsed(999), '00:00:00');
  assert.equal(formatElapsed(1000), '00:00:01');
  assert.equal(formatElapsed(90061000), '25:01:01');
});
