import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('./src/speechOperation.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { speechOperation } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);

test('Speech operation resolves on success and ignores late duplicate callbacks', async () => {
  await speechOperation((success, failure) => {
    success();
    failure('late failure');
  }, 100);
});

test('Speech operation rejects callback errors and synchronous exceptions', async () => {
  await assert.rejects(
    speechOperation((success, failure) => failure('connection failed'), 100),
    /connection failed/,
  );
  await assert.rejects(
    speechOperation(() => {
      throw new Error('device missing');
    }, 100),
    /device missing/,
  );
});

test('Speech operation cannot wait forever for a callback', async () => {
  await assert.rejects(
    speechOperation(() => {}, 10),
    /timed out/,
  );
});
