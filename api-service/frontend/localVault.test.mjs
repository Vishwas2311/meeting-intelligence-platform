import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('./src/localVault.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source.replaceAll('import.meta.env', '{PROD:true}'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const vault = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));

test('recovery ciphertext is authenticated, owner isolated, locked on logout, and recoverable after restart', async () => {
  const alice = {
    actor: 'alice',
    current_generation: 'one',
    keys: [{ generation: 'one', key: Buffer.alloc(32, 1).toString('base64') }],
  };
  const bob = {
    actor: 'bob',
    current_generation: 'two',
    keys: [{ generation: 'two', key: Buffer.alloc(32, 2).toString('base64') }],
  };
  await vault.unlockVault(alice);
  const value = {
    id: 'record',
    text: 'Private synthetic transcript',
    blob: new Blob(['synthetic audio']),
  };
  const encrypted = await vault.seal(value, 'chunks');
  assert(!JSON.stringify(encrypted).includes('Private synthetic'));
  assert.equal(await (await vault.unseal(encrypted, 'chunks')).blob.text(), 'synthetic audio');
  await assert.rejects(vault.unseal({ ...encrypted, id: 'tampered' }, 'chunks'));
  await assert.rejects(vault.unseal(encrypted, 'different-store'));
  const aliceName = vault.databaseName('recovery');
  vault.lockVault();
  assert.throws(() => vault.databaseName('recovery'), /Sign in online/);
  await assert.rejects(vault.unseal(encrypted, 'chunks'));
  await vault.unlockVault(bob);
  assert.notEqual(vault.databaseName('recovery'), aliceName);
  await assert.rejects(vault.unseal(encrypted, 'chunks'));
  await vault.unlockVault(alice);
  assert.equal((await vault.unseal(encrypted, 'chunks')).text, value.text);
  await assert.rejects(vault.unseal(value, 'chunks'), /legacy recovery/);
});
