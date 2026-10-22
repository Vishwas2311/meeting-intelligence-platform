// Real browser IndexedDB/Crypto regression; all identities and media are synthetic.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import ts from 'typescript';

const compile = async (name, dependency) => {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  let js = ts.transpileModule(source.replaceAll('import.meta.env', '{PROD:true,DEV:false}'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  if (dependency) js = js.replaceAll("'./localVault'", JSON.stringify(dependency));
  return 'data:text/javascript;base64,' + Buffer.from(js).toString('base64');
};
const vaultUrl = await compile('localVault');
const offlineUrl = await compile('offline', vaultUrl);
const recapUrl = await compile('recapDraft', vaultUrl);
const browser = await chromium.launch({
  ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
  headless: true,
});
try {
  const page = await browser.newPage();
  await page.route('http://127.0.0.1:5179/**', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<h1>Synthetic recovery privacy test</h1>' }),
  );
  await page.goto('http://127.0.0.1:5179/privacy');
  const result = await page.evaluate(
    async ({ vaultUrl, offlineUrl, recapUrl }) => {
      const v = await import(vaultUrl),
        o = await import(offlineUrl),
        r = await import(recapUrl);
      const alice = {
        actor: 'alice',
        current_generation: 'one',
        keys: [{ generation: 'one', key: btoa('a'.repeat(32)) }],
      };
      const bob = {
        actor: 'bob',
        current_generation: 'two',
        keys: [{ generation: 'two', key: btoa('b'.repeat(32)) }],
      };
      await v.unlockVault(alice);
      for (let i = 0; i < 25; i++)
        await o.saveChunk({
          id: `meeting:${String(i).padStart(3, '0')}`,
          meetingId: 'meeting',
          sequence: i,
          startMs: i * 1000,
          endMs: (i + 1) * 1000,
          blob: new Blob(['private synthetic audio']),
        });
      await o.savePendingSegment('meeting', {
        client_event_id: 'turn',
        text: 'Private synthetic text',
        start_ms: 0,
        end_ms: 1000,
      });
      await o.saveManifest('meeting', {
        expected_chunks: 25,
        recorded_through_ms: 25000,
        finalized: true,
        pauses: [],
      });
      const manifest = (await o.recordingManifests())[0];
      await o.acknowledgeManifest(manifest);
      if (!(await o.recordingManifests())[0].synced) throw Error('Manifest acknowledgement lost');
      await r.saveRecapDraft({
        id: 'recap',
        notes: 'Private synthetic recap',
        parts: [new Blob(['private synthetic voice'])],
      });
      const raw = await new Promise((resolve, reject) => {
        const opening = indexedDB.open(v.databaseName('meeting-intelligence-recovery'));
        opening.onerror = () => reject(opening.error);
        opening.onsuccess = () => {
          const db = opening.result;
          const tx = db.transaction('chunks');
          const read = tx.objectStore('chunks').getAll();
          tx.oncomplete = () => {
            db.close();
            resolve(read.result);
          };
        };
      });
      if (JSON.stringify(raw).includes('private synthetic')) throw Error('Plaintext found at rest');
      const first = await o.pendingChunks();
      if (first.length !== 20 || (await o.pendingRecoveryIds('chunks')).length !== 25)
        throw Error('Page/count bounds failed');
      await v.unlockVault(bob);
      if ((await o.pendingChunks()).length || (await r.listRecapDrafts()).length)
        throw Error('Cross-owner records exposed');
      await v.unlockVault(alice);
      if ((await (await o.pendingChunks())[0].blob.text()) !== 'private synthetic audio')
        throw Error('Owner recovery failed');
      if ((await r.readRecapDraft('recap')).notes !== 'Private synthetic recap')
        throw Error('Recap recovery failed');
      v.lockVault();
      let locked = false;
      try {
        await o.pendingChunks();
      } catch {
        locked = true;
      }
      if (!locked) throw Error('Logout did not lock storage');
      return {
        ciphertextRecords: raw.length,
        pageSize: first.length,
        ownerIsolation: true,
        recapRecovery: true,
        logoutLocked: true,
      };
    },
    { vaultUrl, offlineUrl, recapUrl },
  );
  await page.reload();
  const reloadLocked = await page.evaluate(async (vaultUrl) => {
    const v = await import(vaultUrl);
    try {
      v.databaseName('meeting-intelligence-recovery');
      return false;
    } catch {
      return true;
    }
  }, vaultUrl);
  assert(reloadLocked);
  await mkdir('../output/playwright/privacy', { recursive: true });
  await writeFile(
    '../output/playwright/privacy/results.json',
    JSON.stringify({ ...result, reloadLocked }, null, 2),
  );
  console.log(JSON.stringify({ ...result, reloadLocked }));
} finally {
  await browser.close();
}
