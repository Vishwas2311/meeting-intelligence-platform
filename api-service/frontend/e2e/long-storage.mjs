// Accelerated production-encrypted buffer volume; no microphone/provider calls.
// Three hours stresses retained buffers; supported meeting capture remains capped at two hours.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import ts from 'typescript';

async function compile(name, dependency) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  let js = ts.transpileModule(source.replaceAll('import.meta.env', '{PROD:true,DEV:false}'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  if (dependency) js = js.replaceAll("'./localVault'", JSON.stringify(dependency));
  return 'data:text/javascript;base64,' + Buffer.from(js).toString('base64');
}
const vaultUrl = await compile('localVault');
const offlineUrl = await compile('offline', vaultUrl);
const results = [];
const browser = await chromium.launch({
  ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
  headless: true,
});
try {
  for (const hours of [1, 2, 3]) {
    const context = await browser.newContext();
    try {
      const page = await context.newPage();
      await page.route('http://127.0.0.1:5179/**', (route) =>
        route.fulfill({
          contentType: 'text/html',
          body: '<h1>Synthetic encrypted recovery volume</h1>',
        }),
      );
      await page.goto('http://127.0.0.1:5179/volume');
      const count = hours * 720;
      const written = await page.evaluate(
        async ({ count, vaultUrl, offlineUrl }) => {
          const vault = await import(vaultUrl),
            store = await import(offlineUrl);
          await vault.unlockVault({
            actor: 'synthetic-volume-owner',
            current_generation: 'v1',
            keys: [{ generation: 'v1', key: btoa('v'.repeat(32)) }],
          });
          const began = performance.now();
          for (let i = 0; i < count; i++) {
            const bytes = new Uint8Array(20000);
            bytes[0] = i % 251;
            await store.saveChunk({
              id: `synthetic-long:${i}`,
              meetingId: 'synthetic-long',
              sequence: i,
              startMs: i * 5000,
              endMs: (i + 1) * 5000,
              blob: new Blob([bytes]),
            });
          }
          const ids = await store.pendingRecoveryIds('chunks');
          if (ids.length !== count || ids.some((row) => row.meetingId !== 'synthetic-long'))
            throw Error('Complete recovery manifest was not retained');
          return { writeSeconds: (performance.now() - began) / 1000, bytes: count * 20000 };
        },
        { count, vaultUrl, offlineUrl },
      );
      await page.reload();
      const restored = await page.evaluate(
        async ({ count, vaultUrl, offlineUrl }) => {
          const vault = await import(vaultUrl),
            store = await import(offlineUrl);
          let locked = false;
          try {
            await store.pendingChunks();
          } catch {
            locked = true;
          }
          if (!locked) throw Error('Reload exposed a locked recovery buffer');
          await vault.unlockVault({
            actor: 'synthetic-volume-owner',
            current_generation: 'v1',
            keys: [{ generation: 'v1', key: btoa('v'.repeat(32)) }],
          });
          const seen = new Set(),
            began = performance.now();
          let pages = 0;
          while ((await store.pendingRecoveryIds('chunks')).length) {
            const chunks = await store.pendingChunks();
            if (!chunks.length || chunks.length > 20)
              throw Error('Recovery page exceeded its bound');
            pages++;
            for (const item of chunks) {
              if (
                seen.has(item.sequence) ||
                item.sequence < 0 ||
                item.sequence >= count ||
                item.startMs !== item.sequence * 5000 ||
                item.endMs !== (item.sequence + 1) * 5000 ||
                item.blob.size !== 20000 ||
                new Uint8Array(await item.blob.arrayBuffer())[0] !== item.sequence % 251
              )
                throw Error('Restored chunk bytes or timeline mismatch');
              seen.add(item.sequence);
              await store.removeChunk(item.id); // Simulated server acknowledgement, after byte verification.
            }
          }
          if (seen.size !== count) throw Error('Recovery lost acknowledged synthetic chunks');
          return {
            chunks: seen.size,
            pages,
            restoreSeconds: (performance.now() - began) / 1000,
            reloadLocked: true,
          };
        },
        { count, vaultUrl, offlineUrl },
      );
      assert.equal(restored.chunks, count);
      const result = {
        hours,
        ...written,
        ...restored,
        productionEncryption: true,
        mode: 'accelerated buffer volume; not recording-duration or cloud qualification',
      };
      results.push(result);
      console.log(JSON.stringify(result));
    } finally {
      await context.close();
    }
  }
  await mkdir('../output/playwright/long-storage', { recursive: true });
  await writeFile(
    '../output/playwright/long-storage/results.json',
    JSON.stringify(results, null, 2),
  );
} finally {
  await browser.close();
}
