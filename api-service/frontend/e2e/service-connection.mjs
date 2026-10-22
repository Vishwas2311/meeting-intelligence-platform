// Read-only browser smoke check against this package's actual running UI/API proxy.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const browser = await chromium.launch({
  ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(process.env.TEST_UI_URL || 'http://127.0.0.1:5174');
  await page.getByRole('heading', { name: 'Live Meeting Studio', exact: true }).waitFor();
  const result = await page.evaluate(async () => {
    const paths = ['/api/auth/me', '/api/meetings', '/api/storage', '/api/speech/languages'];
    const replies = [];
    for (const path of paths) {
      const response = await fetch(path);
      replies.push({ path, status: response.status, data: await response.json() });
    }
    return replies;
  });
  assert(result.every((reply) => reply.status === 200));
  assert.equal(result[0].data.actor, 'local-user');
  assert.equal(result[2].data.database, 'sqlite');
  await page.getByRole('button', { name: 'Meeting Notes', exact: true }).click();
  await page.getByLabel('Notes title').waitFor();
  await page.getByLabel('Live transcript', { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      ui: process.env.TEST_UI_URL || 'http://127.0.0.1:5174',
      api: 'http://127.0.0.1:8100',
      authenticatedReads: result.length,
      notesScreen: 'passed',
      browserErrors: errors.length,
    }),
  );
} finally {
  await browser.close();
}
