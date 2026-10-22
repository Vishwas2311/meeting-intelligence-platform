// Built production UI must stay locked when the company IdP is unavailable.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('../dist/', import.meta.url));
const browser = await chromium.launch({
  ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
  headless: true,
});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://identity.synthetic.invalid/**', (route) => route.abort());
  await page.route('http://127.0.0.1:5189/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const file = path.resolve(root, pathname === '/' ? 'index.html' : '.' + pathname);
    assert(file.startsWith(root));
    const contentType = file.endsWith('.js')
      ? 'application/javascript'
      : file.endsWith('.css')
        ? 'text/css'
        : 'text/html';
    await route.fulfill({ contentType, body: await readFile(file) });
  });
  await page.goto('http://127.0.0.1:5189/');
  await page.getByRole('button', { name: 'Sign in', exact: true }).waitFor({ timeout: 30000 });
  assert.equal(await page.getByRole('heading', { name: 'Live Meeting Studio' }).count(), 0);
  assert.equal(await page.getByLabel('Backend API token').count(), 0);
  assert.deepEqual(errors, []);
  await mkdir('../output/playwright/auth', { recursive: true });
  await writeFile(
    '../output/playwright/auth/results.json',
    JSON.stringify(
      {
        productionBundle: true,
        unconfiguredOrUnavailableIdpLocksApp: true,
        manualTokenUiAbsent: true,
        browserErrors: errors.length,
      },
      null,
      2,
    ),
  );
  console.log(
    'PASS built production sign-in gate; company IdP protocol qualification remains separate',
  );
} finally {
  await browser.close();
}
