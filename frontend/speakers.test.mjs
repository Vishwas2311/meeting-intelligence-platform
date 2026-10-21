import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('./src/speakers.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { scopedSpeaker, speakerLabels } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);

test('unknown voices stay unidentified and guest IDs are isolated across reconnects', () => {
  for (const unknown of [undefined, null, '', 'Unknown', ' unknown '])
    assert.equal(scopedSpeaker('a', unknown), null);
  const a = scopedSpeaker('a', 'Guest-1');
  const b = scopedSpeaker('b', 'Guest-1');
  assert.notEqual(a, b);
  const labels = speakerLabels(
    [a, a, b].map((speaker_id) => ({ speaker_id })),
    { [a]: 'Raj' },
  );
  assert.equal(labels[a], 'Raj');
  assert.equal(labels[b], 'Speaker 2');
});

test('labels distinguish repeated turns, three speakers, and legacy names', () => {
  const ids = ['Guest-1', 'Guest-2', 'Guest-1', 'Guest-3'].map((id) => scopedSpeaker('a', id));
  const labels = speakerLabels(
    [...ids.map((speaker_id) => ({ speaker_id })), { speaker_id: 'Priya' }, { speaker_id: null }],
    {},
  );
  assert.deepEqual(Object.values(labels), ['Speaker 1', 'Speaker 2', 'Speaker 3', 'Priya']);
});
