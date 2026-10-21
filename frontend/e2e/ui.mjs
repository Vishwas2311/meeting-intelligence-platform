import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = path.join(root, 'output/playwright/negative');
await mkdir(output, { recursive: true });
const python = spawn(
  path.join(root, 'backend/.venv/Scripts/python.exe'),
  ['-u', 'tests/ui_bridge.py'],
  {
    cwd: path.join(root, 'backend'),
    stdio: ['pipe', 'pipe', 'pipe'],
  },
);
let nextId = 0;
const pending = new Map();
let ready;
let startupFailed;
const startup = new Promise((resolve, reject) => {
  ready = resolve;
  startupFailed = reject;
});
let stderr = '';
python.stderr.on('data', (data) => (stderr += data));
createInterface({ input: python.stdout }).on('line', (line) => {
  const value = JSON.parse(line);
  if (value.ready) return ready();
  pending.get(value.id)?.resolve(value);
  pending.delete(value.id);
});
python.on('exit', (code) => {
  startupFailed(Error(`Test backend exited (${code}): ${stderr}`));
  for (const item of pending.values())
    item.reject(Error(`Test backend exited (${code}): ${stderr}`));
});
async function api(pathname, method = 'GET', value) {
  const id = ++nextId;
  const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  python.stdin.write(
    JSON.stringify({
      id,
      path: pathname,
      method,
      ...(value === undefined
        ? {}
        : {
            headers: { 'content-type': 'application/json' },
            body: Buffer.from(JSON.stringify(value)).toString('base64'),
          }),
    }) + '\n',
  );
  const response = await result;
  return { ...response, data: JSON.parse(Buffer.from(response.body, 'base64').toString()) };
}
const startupTimer = setTimeout(() => {
  python.stdin.end();
  startupFailed(Error(`Test backend startup timed out: ${stderr}`));
}, 60000);
try {
  await startup;
} finally {
  clearTimeout(startupTimer);
}
await api('/__test__/seed', 'POST');
const browser = await chromium.launch({
  channel: 'msedge',
  headless: true,
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
});
const results = [];
const speechModule = `
export const ResultReason={RecognizedSpeech:3};
export const PropertyId={SpeechServiceResponse_DiarizeIntermediateResults:1001};
export const SpeechConfig={fromAuthorizationToken(){return {setProperty(){}};}};
export const AudioConfig={fromStreamInput(stream){window.__speechInput=stream;return {};}};
export class ConversationTranscriber {
  constructor(){window.__recognizer=this;}
  startTranscribingAsync(ok){
    if(window.__fault==='speech-start-hang')return;
    if(window.__fault==='speech-start-delayed'){window.__releaseSpeechStart=()=>{this.sessionStarted?.();ok();};return;}
    setTimeout(()=>{this.sessionStarted?.();ok();},10);
  }
  stopTranscribingAsync(ok){
    if(window.__fault==='speech-stop-hang')return;
    setTimeout(()=>{this.sessionStopped?.();ok();},10);
  }
  close(){this.closed=true;}
}
export default {ResultReason,SpeechConfig,AudioConfig,ConversationTranscriber,PropertyId};
`;
async function setup() {
  await api('/__test__/mode', 'POST', { value: 'success' });
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(30000);
  const state = { outage: false, uploadFailure: false, creates: 0, errors: [] };
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.route(/microsoft-cognitiveservices-speech-sdk.*\.js/, (route) =>
    route.fulfill({ contentType: 'text/javascript', body: speechModule }),
  );
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (state.outage || (state.uploadFailure && pathname.endsWith('/audio-chunks')))
      return route.fulfill({ status: 503, json: { detail: 'Synthetic backend outage' } });
    if (pathname === '/api/meetings' && request.method() === 'POST') state.creates++;
    if (state.patchBarrier && request.method() === 'PATCH') await state.patchBarrier;
    const id = ++nextId;
    const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    python.stdin.write(
      JSON.stringify({
        id,
        path: pathname + new URL(request.url()).search,
        method: request.method(),
        headers: { 'content-type': request.headers()['content-type'] || '' },
        body: request.postDataBuffer()?.toString('base64') || '',
      }) + '\n',
    );
    const reply = await response;
    await route.fulfill({
      status: reply.status,
      contentType: reply.contentType,
      body: Buffer.from(reply.body, 'base64'),
    });
  });
  await page.addInitScript(() => {
    window.__streams = [];
    window.__nativeRecorder = window.MediaRecorder;
    window.__nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    const stream = async (frequency = 440) => {
      const audio = new AudioContext();
      const oscillator = audio.createOscillator();
      oscillator.frequency.value = frequency;
      const destination = audio.createMediaStreamDestination();
      oscillator.connect(destination);
      oscillator.start();
      await audio.resume();
      window.__streams.push(destination.stream);
      return destination.stream;
    };
    navigator.mediaDevices.getUserMedia = async () => {
      if (window.__fault === 'microphone-denied')
        throw new DOMException('Permission denied', 'NotAllowedError');
      return stream();
    };
    navigator.mediaDevices.getDisplayMedia = async () => {
      if (window.__fault === 'tab-no-audio') {
        const video = document.createElement('canvas').captureStream();
        window.__streams.push(video);
        return video;
      }
      const shared = await stream(880);
      shared.addTrack(document.createElement('canvas').captureStream().getVideoTracks()[0]);
      return shared;
    };
    class Recorder extends EventTarget {
      static isTypeSupported() {
        return window.__fault !== 'unsupported-recorder';
      }
      state = 'inactive';
      constructor(stream) {
        super();
        window.__recordingInput = stream;
        window.__recorder = this;
      }
      start() {
        this.state = 'recording';
      }
      pause() {
        this.state = 'paused';
      }
      resume() {
        this.state = 'recording';
      }
      requestData() {
        this.ondataavailable?.(
          new BlobEvent('dataavailable', {
            data: new Blob(['SYNTHETIC AUDIO'], { type: 'audio/webm;codecs=opus' }),
          }),
        );
      }
      stop() {
        this.state = 'inactive';
        if (window.__fault === 'recorder-stop-hang') return;
        queueMicrotask(() => {
          this.requestData();
          this.onstop?.();
          this.dispatchEvent(new Event('stop'));
        });
      }
    }
    window.MediaRecorder = Recorder;
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (...args) {
      if (this.name === 'chunks' && window.__fault === 'storage-quota')
        throw new DOMException('Synthetic quota exhausted', 'QuotaExceededError');
      return put.apply(this, args);
    };
  });
  await page.goto('http://localhost:5173');
  await page.getByRole('heading', { name: 'Live Meeting Studio' }).waitFor();
  await page.waitForLoadState('networkidle');
  assert.equal(
    await page.getByLabel('Audio source', { exact: true }).inputValue(),
    'meeting-audio',
  );
  // Existing scenarios exercise in-person capture; combined capture has its own cases below.
  await page.getByLabel('Audio source', { exact: true }).selectOption('microphone');
  return { page, context, state };
}
async function begin(page, title = 'Synthetic UI recording') {
  await page.getByLabel('Meeting title', { exact: true }).fill(title);
  await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
  await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
  await page.getByRole('button', { name: 'Pause', exact: true }).waitFor({ timeout: 20000 });
  await page.waitForFunction(
    () =>
      !!window.__recognizer &&
      [...document.querySelectorAll('.health-list b')].some(
        (element) => element.textContent.trim() === 'connected',
      ),
  );
}
async function emit(page, text = 'We agreed to review a synthetic pilot.') {
  await page.evaluate(
    (text) =>
      window.__recognizer.transcribed?.(null, {
        result: { reason: 3, text, offset: 1000000, duration: 10000000 },
      }),
    text,
  );
  await page.getByText(text, { exact: true }).waitFor();
}
async function stop(page) {
  await page.getByRole('button', { name: /^(Stop Meeting|Stop)$/ }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('button')].some(
      (button) => button.textContent.trim() === 'Generate MoM' && !button.disabled,
    ),
  );
}
async function chooseRow(page, row) {
  const title = await row.locator('b').innerText();
  await row.click();
  await page.getByRole('heading', { name: /^(Live Meeting Studio|Meeting Minutes)$/ }).waitFor();
  // Capture scenarios explicitly return to Workspace after opening the saved record.
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByRole('heading', { name: 'Live Meeting Studio', exact: true }).waitFor();
  await page.waitForFunction(
    (title) => document.querySelector('.session-top h2')?.textContent === title,
    title,
  );
}
async function longMinutes(page) {
  await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
  await chooseRow(
    page,
    page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first(),
  );
  const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
  const source = (await api(`/api/meetings/${mid}/transcript`)).data.segments[0].id;
  const detail =
    'The team reviewed the pilot scope, customer feedback, delivery dependencies and the evidence needed before the next release. ';
  const minutes = {
    status: 'draft',
    data: {
      title: 'Customer pilot and delivery planning review',
      summary: detail.repeat(12),
      discussion_points: Array.from(
        { length: 30 },
        (_, i) => `Topic ${i + 1}. ${detail.repeat(2)}`,
      ),
      decisions: Array.from({ length: 6 }, (_, i) => ({
        text: `Decision ${i + 1}. ${detail}`,
        source_segment_ids: [source],
      })),
      action_items: Array.from({ length: 12 }, (_, i) => ({
        text: `Action ${i + 1}. ${detail.repeat(2)}`,
        owner: i % 2 ? null : 'Pilot delivery team',
        due_date: null,
        source_segment_ids: [source],
      })),
      risks: [detail, detail],
      unanswered_questions: [
        'Which dependencies need further review?',
        'When will the pilot team confirm readiness?',
      ],
      transcript_gaps: [],
      requires_review: true,
    },
  };
  const previous = structuredClone(minutes.data);
  previous.summary = 'An earlier approved version of the synthetic meeting.';
  const writes = [];
  await page.route(`**/api/meetings/${mid}/mom`, (route) => {
    if (route.request().method() === 'PATCH') {
      minutes.data = route.request().postDataJSON();
      writes.push('save');
    }
    return route.fulfill({ json: minutes });
  });
  await page.route(`**/api/meetings/${mid}/mom/approve`, (route) => {
    minutes.status = 'approved';
    writes.push('approve');
    return route.fulfill({ json: { status: 'approved' } });
  });
  await page.route(`**/api/meetings/${mid}/mom/revisions`, (route) =>
    route.fulfill({
      json: [
        {
          id: 'previous',
          status: 'approved',
          at: '2026-01-01T10:00:00Z',
          data: previous,
          provenance: {},
        },
      ],
    }),
  );
  await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#mom-editor')?.value.length > 1000);
  return { minutes, writes };
}
async function check(name, callback) {
  if (process.env.UI_CASE_FILTER && !name.includes(process.env.UI_CASE_FILTER)) return;
  const test = await setup();
  try {
    await callback(test);
    assert.deepEqual(test.state.errors, [], 'No unhandled browser errors');
    results.push({ name, passed: true });
    console.log(`PASS ${name}`);
  } catch (error) {
    results.push({ name, passed: false, error: error.message });
    await test.page
      .screenshot({ path: path.join(output, `${results.length}-failure.png`) })
      .catch(() => {});
    console.log(`FAIL ${name}: ${error.message.split('\n')[0]}`);
  } finally {
    test.state.releasePatch?.();
    await test.context.close();
  }
}
try {
  await check('speakers: an older refresh cannot undo approval', async ({ page, state }) => {
    await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
    await page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first().click();
    await page.locator('#mom-editor').waitFor();
    const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
    const original = (await api(`/api/meetings/${mid}/mom`)).data;
    let held;
    const entered = new Promise((resolve) => (held = resolve));
    let release;
    const barrier = new Promise((resolve) => (release = resolve));
    state.releasePatch = release;
    await page.route(`**/api/meetings/${mid}/mom`, async (route) => {
      held();
      await barrier;
      await route.fulfill({ json: original });
    });
    await page.route(`**/api/meetings/${mid}/mom/approve`, (route) =>
      route.fulfill({ json: { status: 'approved' } }),
    );
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await entered;
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await page.getByText('MoM approved.', { exact: true }).waitFor();
    release();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('.minutes-toolbar-label .pill').innerText(), 'Approved');
    assert(await page.getByRole('button', { name: 'Save Draft', exact: true }).isDisabled());
  });
  await check(
    'speakers: a failed rename retains the old label and works on a phone',
    async ({ page }) => {
      await begin(page, 'Speaker rename failure');
      await page.evaluate(() =>
        window.__recognizer.transcribed(null, {
          result: {
            reason: 3,
            speakerId: 'Guest-1',
            text: 'A short speaker test.',
            offset: 0,
            duration: 10000000,
          },
        }),
      );
      await page.getByRole('button', { name: 'Rename Speaker 1', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Rename speaker' });
      await dialog.getByLabel('Name', { exact: true }).fill('Raj');
      await page.route('**/api/meetings/*/speakers', (route) =>
        route.fulfill({ status: 503, json: { detail: 'Synthetic save failure' } }),
      );
      await dialog.getByRole('button', { name: 'Save name', exact: true }).click();
      await dialog.getByRole('alert').filter({ hasText: 'Synthetic save failure' }).waitFor();
      assert.equal(await page.locator('.transcript-entry strong').innerText(), 'Speaker 1');
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
      await page.unroute('**/api/meetings/*/speakers');
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole('button', { name: 'Rename Speaker 1', exact: true }).click();
      const name = 'A long participant name that should wrap correctly on a phone';
      await dialog.getByLabel('Name', { exact: true }).fill(name);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: path.join(output, 'speaker-rename-phone.png') });
      await dialog.getByRole('button', { name: 'Save name', exact: true }).click();
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(await page.locator('.transcript-entry strong').innerText(), name);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await stop(page);
    },
  );
  await check(
    'speakers: three voices can be renamed across live, saved and offline transcripts',
    async ({ page, state }) => {
      await begin(page, 'Speaker naming test');
      const emitVoice = async (speakerId, text) => {
        await page.evaluate(
          ({ speakerId, text }) =>
            window.__recognizer.transcribed(null, {
              result: { reason: 3, speakerId, text, offset: 1000000, duration: 10000000 },
            }),
          { speakerId, text },
        );
        await page.locator('.transcript-entry p').filter({ hasText: text }).waitFor();
      };
      await emitVoice('Guest-1', 'First voice opening.');
      await emitVoice('Guest-2', 'Second voice reply.');
      await emitVoice('Guest-1', 'First voice again.');
      await emitVoice('Guest-3', 'Third voice joins.');
      assert.deepEqual(await page.locator('.transcript-entry strong').allTextContents(), [
        'Speaker 1',
        'Speaker 2',
        'Speaker 1',
        'Speaker 3',
      ]);
      await page.getByRole('button', { name: 'Rename Speaker 1', exact: true }).first().click();
      const dialog = page.getByRole('dialog', { name: 'Rename speaker' });
      await dialog.getByLabel('Name', { exact: true }).fill('Raj');
      await dialog.getByRole('button', { name: 'Save name', exact: true }).click();
      await dialog.waitFor({ state: 'hidden' });
      await emitVoice('Guest-1', 'First voice after naming.');
      assert.equal(
        await page.locator('.transcript-entry strong').filter({ hasText: /^Raj$/ }).count(),
        3,
      );
      await page.evaluate(() =>
        window.__recognizer.transcribing(null, {
          result: { text: 'Still identifying.', speakerId: 'Unknown', offset: 0 },
        }),
      );
      await page.locator('.interim').waitFor();
      assert.equal(await page.locator('.interim strong').innerText(), 'Unidentified speaker');
      await emitVoice('Unknown', 'Unidentified short response.');
      await stop(page);
      const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
      const saved = (await api(`/api/meetings/${mid}/transcript`)).data;
      assert.equal(
        new Set(saved.segments.filter((s) => s.speaker_id).map((s) => s.speaker_id)).size,
        3,
      );
      assert.equal(saved.segments.at(-1).speaker_id, null);
      assert.deepEqual(Object.values(saved.speaker_names), ['Raj']);
      await page.reload();
      await page.locator('.transcript-entry strong').filter({ hasText: /^Raj$/ }).first().waitFor();
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 15000 });
      await page
        .getByRole('button', { name: 'View supporting transcript', exact: true })
        .first()
        .click();
      const evidence = page.getByRole('dialog', { name: 'Supporting transcript' });
      assert.equal(await evidence.locator('.transcript-entry strong').innerText(), 'Raj');
      await evidence.getByRole('button', { name: 'Close transcript', exact: true }).click();
      await page
        .getByRole('navigation', { name: 'Minutes sections' })
        .getByRole('button', { name: /^Transcript/ })
        .click();
      await page
        .locator('.transcript-card .transcript-entry strong')
        .filter({ hasText: /^Raj$/ })
        .first()
        .waitFor();
      await page.getByRole('button', { name: 'Rename Speaker 2', exact: true }).click();
      await dialog.getByLabel('Name', { exact: true }).fill('Priya');
      await dialog.getByRole('button', { name: 'Save name', exact: true }).click();
      await dialog.waitFor({ state: 'hidden' });
      await page
        .locator('.transcript-entry strong')
        .filter({ hasText: /^Priya$/ })
        .waitFor();
      await page.screenshot({ path: path.join(output, 'speaker-names-saved.png') });
      state.outage = true;
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await page.getByText('Showing offline copies.', { exact: false }).waitFor();
      await page.locator('.meeting-row').filter({ hasText: 'Speaker naming test' }).click();
      await page
        .getByRole('navigation', { name: 'Minutes sections' })
        .getByRole('button', { name: /^Transcript/ })
        .click();
      await page
        .locator('.transcript-entry strong')
        .filter({ hasText: /^Priya$/ })
        .waitFor();
      assert.equal(await page.getByRole('button', { name: /^Rename / }).count(), 0);
    },
  );
  await check(
    'speakers: reconnecting does not assign an earlier name to a new guest ID',
    async ({ page }) => {
      await begin(page, 'Speaker reconnect test');
      await page.evaluate(() =>
        window.__recognizer.transcribed(null, {
          result: {
            reason: 3,
            speakerId: 'Guest-1',
            text: 'Before reconnect.',
            offset: 0,
            duration: 10000000,
          },
        }),
      );
      await page.getByRole('button', { name: 'Rename Speaker 1', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Rename speaker' });
      await dialog.getByLabel('Name', { exact: true }).fill('Raj');
      await dialog.getByRole('button', { name: 'Save name', exact: true }).click();
      await dialog.waitFor({ state: 'hidden' });
      await page.evaluate(() => {
        window.__oldRecognizer = window.__recognizer;
        window.__recognizer.canceled(null, { reason: 'Synthetic disconnect' });
      });
      await page.waitForFunction(
        () =>
          window.__recognizer !== window.__oldRecognizer &&
          [...document.querySelectorAll('.health-list b')].some(
            (e) => e.textContent.trim() === 'connected',
          ),
        { timeout: 20000 },
      );
      await page.evaluate(() =>
        window.__recognizer.transcribed(null, {
          result: {
            reason: 3,
            speakerId: 'Guest-1',
            text: 'After reconnect.',
            offset: 0,
            duration: 10000000,
          },
        }),
      );
      await page.locator('.transcript-entry p').filter({ hasText: 'After reconnect.' }).waitFor();
      assert.deepEqual(await page.locator('.transcript-entry strong').allTextContents(), [
        'Raj',
        'Speaker 2',
      ]);
      await stop(page);
    },
  );
  await check(
    'timestamps: version history displays UTC records in local time',
    async ({ page, context }) => {
      const cdp = await context.newCDPSession(page);
      await cdp.send('Emulation.setTimezoneOverride', { timezoneId: 'Asia/Kolkata' });
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first().click();
      await page.locator('#mom-editor').waitFor();
      const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
      const current = await api(`/api/meetings/${mid}/mom`);
      assert.equal((await api(`/api/meetings/${mid}/mom`, 'PATCH', current.data.data)).status, 200);
      const revisions = (await api(`/api/meetings/${mid}/mom/revisions`)).data;
      assert.match(revisions[0].at, /(?:Z|\+00:00)$/);
      const expected = await page.evaluate((at) => {
        const date = new Date(at);
        if (date.getTimezoneOffset() !== -330) throw Error('Expected India timezone');
        return date.toLocaleString();
      }, revisions[0].at);
      await page.getByRole('button', { name: 'Version history', exact: true }).click();
      await page.locator('.revision-strip button').first().waitFor();
      assert(
        (await page.locator('.revision-strip button').first().innerText()).startsWith(expected),
      );
    },
  );
  await check(
    'saved meetings: history opens existing minutes and full transcript without generation',
    async ({ page, state }) => {
      const generations = [];
      page.on('request', (request) => {
        if (request.method() === 'POST' && request.url().endsWith('/mom/generate'))
          generations.push(request.url());
      });
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      const row = page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first();
      const title = await row.locator('b').innerText();
      await row.click();
      await page.getByRole('heading', { name: 'Meeting Minutes', exact: true }).waitFor();
      const editor = page.locator('#mom-editor');
      await editor.waitFor();
      const saved = await editor.inputValue();
      assert.equal(await page.locator('.minutes-jobs').count(), 0);
      const outline = page.getByRole('navigation', { name: 'Minutes sections' });
      await editor.fill('Keep this unsaved review while reading the transcript.');
      await outline.getByRole('button', { name: /^Transcript/ }).click();
      await page.locator('.transcript-card .transcript-entry').first().waitFor();
      assert.equal(
        await page.locator('.transcript-card .transcript-entry time').first().innerText(),
        '00:00:00',
      );
      assert.equal(
        await page.locator('.transcript-card .transcript-entry p').first().innerText(),
        'Synthetic pilot discussion.',
      );
      await outline.getByRole('button', { name: /^Summary/ }).click();
      assert.equal(
        await editor.inputValue(),
        'Keep this unsaved review while reading the transcript.',
      );
      await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
      assert.equal(await editor.inputValue(), saved);
      for (let attempt = 0; attempt < 2; attempt++) {
        await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
        await page.getByRole('textbox', { name: 'Search meetings' }).fill(title);
        await page.getByRole('button', { name: 'Search', exact: true }).click();
        await page.locator('.meeting-row').filter({ hasText: title }).first().click();
        await editor.waitFor();
        assert.equal(await editor.inputValue(), saved);
      }
      await page.screenshot({ path: path.join(output, 'saved-meeting-minutes.png') });
      await page.getByRole('button', { name: 'Workspace', exact: true }).click();
      assert.equal(
        await page.getByRole('button', { name: 'Generate MoM', exact: true }).count(),
        0,
      );
      await page.getByRole('button', { name: 'View saved minutes', exact: true }).click();
      await editor.waitFor();
      for (const width of [1366, 390]) {
        await page.setViewportSize({ width, height: width === 1366 ? 768 : 844 });
        await outline.getByRole('button', { name: /^Transcript/ }).click();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: path.join(output, `saved-meeting-transcript-${width}.png`) });
      }
      await page.setViewportSize({ width: 1366, height: 768 });
      state.outage = true;
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await page.getByText('Showing offline copies.', { exact: false }).waitFor();
      await page.locator('.meeting-row').filter({ hasText: title }).first().click();
      await editor.waitFor();
      assert.equal(await editor.inputValue(), saved);
      assert(await page.getByRole('button', { name: 'Approve', exact: true }).isDisabled());
      await outline.getByRole('button', { name: /^Transcript/ }).click();
      await page.locator('.transcript-card .transcript-entry').first().waitFor();
      assert.equal(generations.length, 0);
    },
  );
  await check(
    'saved meetings: first generation is saved and reopening does not create another job',
    async ({ page }) => {
      await begin(page, 'Generate once and reuse');
      await emit(page);
      await stop(page);
      const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      const editor = page.locator('#mom-editor');
      await editor.waitFor({ timeout: 15000 });
      const saved = await editor.inputValue();
      const before = (await api(`/api/meetings/${mid}/jobs`)).data;
      for (let attempt = 0; attempt < 2; attempt++) {
        await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
        await page.locator('.meeting-row').filter({ hasText: 'Generate once and reuse' }).click();
        await editor.waitFor();
        assert.equal(await editor.inputValue(), saved);
      }
      const after = (await api(`/api/meetings/${mid}/jobs`)).data;
      assert.deepEqual(
        after.map((job) => job.id),
        before.map((job) => job.id),
      );
      assert.equal(after.length, 1);
      assert.equal(await page.locator('.minutes-jobs').count(), 0);
    },
  );
  await check(
    'saved meetings: stale generation button loads minutes already saved by another request',
    async ({ page }) => {
      await begin(page, 'Saved by another request');
      await emit(page);
      await stop(page);
      const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
      const queued = await api(`/api/meetings/${mid}/mom/generate`, 'POST');
      assert.equal(queued.status, 202, JSON.stringify(queued.data));
      let existing;
      for (let attempt = 0; attempt < 75; attempt++) {
        existing = await api(`/api/meetings/${mid}/mom`);
        if (existing.status === 200) break;
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      assert.equal(
        existing.status,
        200,
        'Minutes must already be persisted before testing a stale screen',
      );
      const generations = [];
      const reads = [];
      page.on('response', (response) => {
        if (response.url().endsWith('/mom')) reads.push(response.status());
      });
      page.on('request', (request) => {
        if (request.method() === 'POST' && request.url().endsWith('/mom/generate'))
          generations.push(request.url());
      });
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor();
      assert.equal(
        generations.length,
        0,
        `Existing status: ${existing.data.status}; minutes GET responses: ${JSON.stringify(reads)}`,
      );
      assert.equal((await api(`/api/meetings/${mid}/jobs`)).data.length, 1);
    },
  );
  await check(
    'transcript format: named turns, timestamps, saved copies and evidence use the same layout',
    async ({ page, state }) => {
      const meeting = (
        await api('/api/meetings', 'POST', {
          title: 'Project planning transcript',
          consent_confirmed: true,
          source: 'teams-transcript',
        })
      ).data;
      await api(`/api/meetings/${meeting.id}/start`, 'POST');
      const segments = [
        {
          client_event_id: 'named-1',
          text: 'Let’s review the project progress.',
          speaker_id: 'Vishal',
          start_ms: 12000,
          end_ms: 18500,
          source: 'imported',
        },
        {
          client_event_id: 'named-2',
          text: 'The backend integration is complete.\nTesting can begin today.',
          speaker_id: 'Priya',
          start_ms: 19000,
          end_ms: 26500,
          source: 'imported',
        },
        {
          client_event_id: 'unknown',
          text: '<img src=x onerror="window.__xss=true">',
          start_ms: 27000,
          end_ms: 33000,
          source: 'imported',
        },
        {
          client_event_id: 'long-name',
          text: 'A long name should wrap without hiding the timestamp.',
          speaker_id: 'A-very-long-participant-name-that-needs-to-wrap-on-a-small-phone-screen',
          start_ms: 34000,
          end_ms: 38000,
          source: 'imported',
        },
      ];
      assert.equal(
        (await api(`/api/meetings/${meeting.id}/segments`, 'POST', { segments })).status,
        200,
      );
      await api(`/api/meetings/${meeting.id}/stop`, 'POST');
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await chooseRow(page, page.locator('.meeting-row').filter({ hasText: meeting.title }));
      const entries = page.locator('.transcript .transcript-entry');
      assert.equal(await entries.count(), 4);
      assert.deepEqual(await entries.locator('header strong').allTextContents(), [
        'Vishal',
        'Priya',
        'Unidentified speaker',
        segments[3].speaker_id,
      ]);
      assert.deepEqual(await entries.locator('time').allTextContents(), [
        '00:00:12',
        '00:00:19',
        '00:00:27',
        '00:00:34',
      ]);
      assert.equal(
        await entries.first().locator('time').getAttribute('title'),
        '00:00:12 – 00:00:18',
      );
      assert.equal(await entries.nth(1).locator('p').innerText(), segments[1].text);
      assert.equal(await page.locator('.transcript img').count(), 0);
      assert.equal(await page.evaluate(() => window.__xss), undefined);
      for (const width of [1366, 390, 360]) {
        await page.setViewportSize({ width, height: width === 1366 ? 768 : 844 });
        await page.locator('.transcript').evaluate((pane) => (pane.scrollTop = 0));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        const bounds = await entries.evaluateAll((rows) =>
          rows.every((row) => {
            const time = row.querySelector('time').getBoundingClientRect();
            const name = row.querySelector('strong').getBoundingClientRect();
            const box = row.getBoundingClientRect();
            return (
              time.right <= box.right + 1 && name.right <= box.right + 1 && time.left >= box.left
            );
          }),
        );
        assert(bounds, 'Names and timestamps must fit the transcript column');
        await page.screenshot({
          path: path.join(output, `transcript-format-${width}.png`),
          fullPage: true,
        });
      }
      await page.setViewportSize({ width: 1366, height: 768 });
      await page.waitForFunction(async (mid) => {
        const { cachedMeetings } = await import('/src/offline.ts');
        return (await cachedMeetings()).some(
          (saved) => saved.id === mid && saved.segments.length === 4,
        );
      }, meeting.id);
      state.outage = true;
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await page.getByText('Showing offline copies.', { exact: false }).waitFor();
      await chooseRow(page, page.locator('.meeting-row').filter({ hasText: meeting.title }));
      assert.equal(await entries.first().locator('header strong').innerText(), 'Vishal');
      assert.equal(await entries.first().locator('time').innerText(), '00:00:12');
      state.outage = false;
      await api(`/api/meetings/${meeting.id}/mom/generate`, 'POST');
      await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 15000 });
      await page
        .getByRole('button', { name: 'View supporting transcript', exact: true })
        .first()
        .click();
      const dialog = page.getByRole('dialog', { name: 'Supporting transcript' });
      await dialog.waitFor();
      assert.equal(await dialog.locator('.transcript-entry strong').innerText(), 'Vishal');
      assert.equal(await dialog.locator('.transcript-entry time').innerText(), '00:00:12');
      await page.screenshot({ path: path.join(output, 'transcript-format-evidence.png') });
      await page.keyboard.press('Escape');
    },
  );
  await check(
    'transcript format: live text has a timestamp and pending status until finalized',
    async ({ page }) => {
      await begin(page);
      await page.evaluate(() =>
        window.__recognizer.transcribing(null, {
          result: { text: 'An unfinished spoken sentence', offset: 125000000 },
        }),
      );
      const pending = page.locator('.transcript-entry.interim');
      await pending.waitFor();
      assert.equal(await pending.locator('strong').innerText(), 'Unidentified speaker');
      assert.equal(await pending.locator('.transcript-pending').innerText(), 'Recognizing…');
      assert.match(await pending.locator('time').innerText(), /^00:00:\d{2}$/);
      const offset = Number((await pending.locator('time').getAttribute('datetime')).slice(2, -1));
      assert(offset >= 12.5 && offset < 16);
      await emit(page);
      assert.equal(await pending.count(), 0);
      await page.evaluate(() =>
        window.__recognizer.transcribing(null, {
          result: { text: 'Pending at shutdown', offset: 0 },
        }),
      );
      await pending.waitFor();
      await stop(page);
      assert.equal(await pending.count(), 0);
    },
  );
  await check('required fields and recording consent', async ({ page, state }) => {
    const title = page.getByLabel('Meeting title', { exact: true });
    await title.fill(' ');
    assert(await page.getByRole('button', { name: 'Start Meeting', exact: true }).isDisabled());
    await title.fill('Synthetic validation');
    await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Confirm recording authorization' }).waitFor();
    assert.equal(state.creates, 0);
  });
  await check(
    'microphone denial creates no meeting and keeps setup usable',
    async ({ page, state }) => {
      await page.evaluate(() => (window.__fault = 'microphone-denied'));
      await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'Permission denied' }).waitFor();
      assert.equal(state.creates, 0);
      assert(await page.getByRole('button', { name: 'Start Meeting', exact: true }).isEnabled());
    },
  );
  await check('shared tab with no audio releases the captured video', async ({ page, state }) => {
    await page.evaluate(() => (window.__fault = 'tab-no-audio'));
    await page.locator('select').selectOption('browser-tab');
    await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
    await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'No meeting audio was shared' }).waitFor();
    assert.equal(state.creates, 0);
    assert(
      await page.evaluate(() =>
        window.__streams.flatMap((s) => s.getTracks()).every((t) => t.readyState === 'ended'),
      ),
    );
  });
  await check(
    'meeting audio: microphone and remote audio reach recording and Speech together',
    async ({ page }) => {
      await page.getByLabel('Audio source', { exact: true }).selectOption('meeting-audio');
      await begin(page, 'Synthetic two-sided meeting');
      const audio = await page.evaluate(async () => {
        if (window.__recordingInput !== window.__speechInput)
          throw new Error('Recorder and Speech received different audio streams');
        const recording = new window.__nativeRecorder(window.__recordingInput, {
          mimeType: 'audio/webm',
        });
        const chunks = [];
        recording.ondataavailable = (event) => chunks.push(event.data);
        recording.start();
        await new Promise((resolve) => setTimeout(resolve, 500));
        const stopped = new Promise((resolve) =>
          recording.addEventListener('stop', resolve, { once: true }),
        );
        recording.stop();
        await stopped;
        const decoder = new AudioContext();
        try {
          const buffer = await decoder.decodeAudioData(await new Blob(chunks).arrayBuffer());
          const samples = buffer.getChannelData(0);
          const start = Math.floor(buffer.sampleRate * 0.1);
          const length = Math.min(samples.length - start, Math.floor(buffer.sampleRate * 0.2));
          function amplitude(frequency) {
            let real = 0,
              imaginary = 0;
            for (let i = 0; i < length; i++) {
              const phase = (2 * Math.PI * frequency * i) / buffer.sampleRate;
              real += samples[start + i] * Math.cos(phase);
              imaginary += samples[start + i] * Math.sin(phase);
            }
            return (2 * Math.hypot(real, imaginary)) / length;
          }
          return {
            microphone: amplitude(440),
            remote: amplitude(880),
            video: window.__recordingInput.getVideoTracks().length,
          };
        } finally {
          await decoder.close();
        }
      });
      assert(
        audio.microphone > 0.1 && audio.remote > 0.1,
        `Both voices must survive WebM encoding: ${JSON.stringify(audio)}`,
      );
      assert.equal(audio.video, 0);
      const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
      assert.equal((await api(`/api/meetings/${mid}`)).data.source, 'meeting-audio');
      await emit(page);
      await stop(page);
      assert(
        await page.evaluate(() =>
          window.__streams.every((s) => s.getTracks().every((t) => t.readyState === 'ended')),
        ),
      );
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 15000 });
    },
  );
  await check(
    'meeting audio: missing shared audio never falls back to microphone only',
    async ({ page, state }) => {
      await page.getByLabel('Audio source', { exact: true }).selectOption('meeting-audio');
      await page.evaluate(() => (window.__fault = 'tab-no-audio'));
      await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'No meeting audio was shared' }).waitFor();
      assert.equal(state.creates, 0);
      assert(
        await page.evaluate(() =>
          window.__streams.every((s) => s.getTracks().every((t) => t.readyState === 'ended')),
        ),
      );
    },
  );
  await check('meeting audio: microphone denial releases shared audio', async ({ page, state }) => {
    await page.getByLabel('Audio source', { exact: true }).selectOption('meeting-audio');
    await page.evaluate(() => (window.__fault = 'microphone-denied'));
    await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
    await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Permission denied' }).waitFor();
    assert.equal(state.creates, 0);
    assert(
      await page.evaluate(() =>
        window.__streams.every((s) => s.getTracks().every((t) => t.readyState === 'ended')),
      ),
    );
  });
  for (const [name, index] of [
    ['shared audio', 0],
    ['microphone', 1],
  ]) {
    await check(
      `meeting audio: losing ${name} stops both sources and blocks minutes`,
      async ({ page }) => {
        await page.getByLabel('Audio source', { exact: true }).selectOption('meeting-audio');
        await begin(page);
        await emit(page);
        await page.evaluate((index) => {
          const track = window.__streams[index].getAudioTracks()[0];
          track.stop();
          track.dispatchEvent(new Event('ended'));
        }, index);
        await page
          .getByRole('alert')
          .filter({ hasText: 'Audio capture ended unexpectedly' })
          .waitFor();
        assert(
          await page.evaluate(() =>
            window.__streams.every((s) => s.getTracks().every((t) => t.readyState === 'ended')),
          ),
        );
        await stop(page);
        await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
        await page
          .getByRole('alert')
          .filter({ hasText: /incomplete|transcription/ })
          .waitFor();
      },
    );
  }
  await check('unsupported recorder cleans up and permits a new meeting', async ({ page }) => {
    await page.evaluate(() => (window.__fault = 'unsupported-recorder'));
    await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
    await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'does not support audio/webm' }).waitFor();
    assert(
      await page.evaluate(() =>
        window.__streams.flatMap((s) => s.getTracks()).every((t) => t.readyState === 'ended'),
      ),
    );
    await page.getByRole('button', { name: 'New Meeting', exact: true }).waitFor();
  });
  await check(
    'record pause resume stop, transcript, worker, edit and approval',
    async ({ page }) => {
      await begin(page, 'Synthetic full lifecycle');
      await emit(page);
      await page.getByRole('button', { name: 'Pause', exact: true }).click();
      await page.getByRole('button', { name: 'Resume', exact: true }).waitFor();
      // Let the 100 ms display tick show the already-paused clock before sampling it.
      await page.waitForTimeout(150);
      const timer = await page.getByRole('timer').innerText();
      await page.waitForTimeout(250);
      assert.equal(await page.getByRole('timer').innerText(), timer);
      await page.getByRole('button', { name: 'Resume', exact: true }).click();
      await page.getByRole('button', { name: 'Pause', exact: true }).waitFor();
      await stop(page);
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 15000 });
      const editor = page.locator('#mom-editor');
      await editor.fill('');
      await page.getByRole('button', { name: 'Save Draft', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'Cannot save MoM' }).waitFor();
      assert.equal(await editor.inputValue(), '');
      await editor.fill('A reviewed synthetic summary.');
      assert(await page.getByRole('button', { name: 'Approve', exact: true }).isDisabled());
      await page.getByRole('button', { name: 'Save Draft', exact: true }).click();
      await page.getByText('MoM draft saved.', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Approve', exact: true }).click();
      await page.getByText('MoM approved.', { exact: true }).waitFor();
      assert.equal(await editor.getAttribute('readonly'), '');
    },
  );
  await check('opening another session while recording is blocked', async ({ page }) => {
    await begin(page);
    await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
    await page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first().click();
    await page.getByRole('alert').filter({ hasText: 'Stop the current recording' }).waitFor();
    await page.getByRole('button', { name: 'Workspace', exact: true }).click();
    await stop(page);
  });
  await check('empty transcript cannot generate fabricated minutes', async ({ page }) => {
    await begin(page);
    await stop(page);
    await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'No synchronized transcript' }).waitFor();
  });
  await check('unsaved minutes survive transcript refresh', async ({ page }) => {
    await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
    await chooseRow(
      page,
      page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first(),
    );
    await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
    const editor = page.locator('#mom-editor');
    const draft = 'UNSAVED CONTENT';
    await editor.fill(draft);
    await page.getByRole('button', { name: 'Workspace', exact: true }).click();
    await page.getByRole('button', { name: 'Refresh transcript', exact: true }).click();
    await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
    assert.equal(await editor.inputValue(), draft);
  });
  await check(
    'unsaved minutes cannot be silently discarded by selecting another meeting',
    async ({ page }) => {
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await chooseRow(
        page,
        page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first(),
      );
      await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
      const editor = page.locator('#mom-editor');
      await editor.fill('{unsaved draft');
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).nth(1).click();
      await page
        .getByRole('alert')
        .filter({ hasText: /Save or discard/ })
        .waitFor();
      await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
      assert.equal(await editor.inputValue(), '{unsaved draft');
    },
  );
  await check(
    'audio outage preserves chunks and stop across reload, then retries',
    async ({ page, state }) => {
      await begin(page);
      await emit(page);
      state.uploadFailure = true;
      await stop(page);
      const queued = await page.evaluate(async () => {
        const store = await import('/src/offline.ts');
        return {
          chunks: (await store.pendingChunks()).length,
          stops: (await store.pendingStops()).length,
        };
      });
      assert(queued.chunks > 0 && queued.stops === 1);
      await page.reload();
      state.uploadFailure = false;
      await page.waitForFunction(
        async () => {
          const store = await import('/src/offline.ts');
          return !(await store.pendingChunks()).length && !(await store.pendingStops()).length;
        },
        null,
        { timeout: 15000 },
      );
    },
  );
  await check(
    'storage quota failure marks recording incomplete and blocks generation',
    async ({ page }) => {
      await begin(page);
      await emit(page);
      await page.evaluate(() => {
        window.__fault = 'storage-quota';
        window.__recorder.requestData();
      });
      await page
        .getByRole('alert')
        .filter({ hasText: 'Audio could not be saved locally' })
        .waitFor();
      await stop(page);
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: /storage failed|incomplete/ })
        .waitFor();
    },
  );
  await check('Speech disconnect cannot silently approve partial minutes', async ({ page }) => {
    await begin(page);
    await emit(page);
    await page.evaluate(() =>
      window.__recognizer.canceled?.(null, { reason: 'Synthetic network failure' }),
    );
    await page.getByRole('alert').filter({ hasText: 'Azure recognition interrupted' }).waitFor();
    await stop(page);
    await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
    await page
      .getByRole('alert')
      .filter({ hasText: /incomplete|transcription|coverage/ })
      .waitFor();
  });
  await check(
    'rate limited provider retries and minutes appear automatically',
    async ({ page }) => {
      await begin(page);
      await emit(page);
      await stop(page);
      await api('/__test__/mode', 'POST', { value: 'rate-limit' });
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 16000 });
      assert((await page.locator('#mom-editor').inputValue()).includes('synthetic'));
    },
  );
  await check('non-retryable LLM authentication error becomes a failed job', async ({ page }) => {
    await begin(page);
    await emit(page);
    await stop(page);
    await api('/__test__/mode', 'POST', { value: 'llm-auth' });
    await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
    await page.getByText('Generation failed', { exact: true }).waitFor({ timeout: 15000 });
    await page
      .getByText('Synthetic LLM authentication rejected (HTTP 401).', { exact: false })
      .waitFor();
  });
  await check(
    'offline history retains ten snapshots and stays read only',
    async ({ page, state }) => {
      await api('/__test__/seed', 'POST');
      let held = false;
      const barrier = new Promise((resolve) => {
        state.releasePatch = resolve;
      });
      await page.route('**/api/meetings/*/snapshot', async (route) => {
        if (!held) {
          held = true;
          await barrier;
        }
        return route.fallback();
      });
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      for (let attempt = 0; attempt < 50 && !held; attempt++) await page.waitForTimeout(100);
      assert(held, 'An earlier snapshot update is held in flight');
      await api('/__test__/seed', 'POST');
      const expected = (await api('/api/meetings')).data.slice(0, 10).map((meeting) => meeting.id);
      await page.getByRole('button', { name: 'Reload', exact: true }).click();
      state.releasePatch();
      let cached = false;
      for (let attempt = 0; attempt < 100; attempt++) {
        cached = await page.evaluate(async (expected) => {
          const { cachedMeetings } = await import('/src/offline.ts');
          const saved = await cachedMeetings();
          return (
            saved.length === 10 &&
            saved.every((item) => expected.includes(item.id) && !!item.minutes)
          );
        }, expected);
        if (cached) break;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      assert(cached, 'Ten current transcript/minutes snapshots committed before the outage');
      state.outage = true;
      await page.getByRole('button', { name: 'Reload', exact: true }).click();
      await page.getByText('Showing offline copies.', { exact: false }).waitFor();
      assert.equal(await page.locator('.meeting-row').count(), 10);
      await page.locator('.meeting-row').first().click();
      await page.getByRole('heading', { name: 'Meeting Minutes', exact: true }).waitFor();
      await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
      assert(await page.getByRole('button', { name: 'Approve', exact: true }).isDisabled());
    },
  );
  await check(
    'backend 401 is shown instead of pretending the cached session is authenticated',
    async ({ page }) => {
      await page.route(/\/api\/meetings(?:\?.*)?$/, (route) =>
        route.fulfill({ status: 401, json: { detail: 'Authentication required' } }),
      );
      await page.getByRole('button', { name: 'Configuration', exact: true }).click();
      await page.getByRole('button', { name: 'Test Backend', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: 'Local API authentication failed' })
        .waitFor();
    },
  );
  await check('HTML response from a proxy does not crash the UI', async ({ page, state }) => {
    await page.route(/\/api\/meetings(?:\?.*)?$/, (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>Proxy failure</h1>' }),
    );
    await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
    await page.waitForTimeout(300);
    assert.deepEqual(state.errors, []);
    await page.getByRole('alert').waitFor();
    await page.getByRole('heading', { name: 'Meeting History', exact: true }).waitFor();
  });
  await check(
    'extended: malformed JSON meeting list cannot crash the UI',
    async ({ page, state }) => {
      await page.route(/\/api\/meetings(?:\?.*)?$/, (route) =>
        route.fulfill({ status: 200, json: [null] }),
      );
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await page.getByRole('alert').waitFor();
      assert.deepEqual(state.errors, []);
      await page.getByRole('heading', { name: 'Meeting History', exact: true }).waitFor();
    },
  );
  await check(
    'extended: native recorder encodes synthetic audio and uploads it',
    async ({ page }) => {
      await page.evaluate(() => {
        window.MediaRecorder = window.__nativeRecorder;
      });
      await begin(page, 'Synthetic native encoding');
      await emit(page);
      await page.waitForTimeout(700);
      await stop(page);
      const records = (await api('/api/meetings')).data;
      const chunks = (
        await api(
          `/api/meetings/${records.find((m) => m.title === 'Synthetic native encoding').id}/audio-chunks`,
        )
      ).data;
      assert(
        chunks.length > 0 &&
          chunks.every((chunk) => chunk.size_bytes > 0 && Number.isInteger(chunk.end_ms)),
      );
    },
  );
  await check(
    'extended: rapid start clicks create one recorder and one meeting',
    async ({ page, state }) => {
      await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).evaluate((button) => {
        button.click();
        button.click();
      });
      await page.waitForFunction(() => !!window.__recognizer);
      assert.equal(state.creates, 1);
      assert.equal(await page.evaluate(() => window.__streams.length), 1);
      await stop(page);
    },
  );
  await check(
    'extended: late recognition from a previous meeting cannot pollute a new one',
    async ({ page }) => {
      await begin(page, 'Synthetic old session');
      await page.evaluate(() => {
        window.__oldRecognizer = window.__recognizer;
      });
      await stop(page);
      await page.getByRole('button', { name: 'New Meeting', exact: true }).click();
      await begin(page, 'Synthetic new session');
      await page.evaluate(() =>
        window.__oldRecognizer.transcribed?.(null, {
          result: { reason: 3, text: 'STALE OLD TRANSCRIPT', offset: 0, duration: 10000 },
        }),
      );
      assert.equal(await page.getByText('STALE OLD TRANSCRIPT', { exact: true }).count(), 0);
      await page.evaluate(() => {
        window.__oldRecognizer.transcribing?.(null, { result: { text: 'STALE PARTIAL' } });
        window.__oldRecognizer.canceled?.(null, { reason: 'STALE CANCEL' });
      });
      assert.equal(await page.getByText('STALE PARTIAL', { exact: true }).count(), 0);
      assert(
        await page
          .locator('.health-list b')
          .filter({ hasText: /^connected$/ })
          .count(),
      );
      await stop(page);
    },
  );
  await check(
    'extended: disappearing audio device stops capture and blocks incomplete minutes',
    async ({ page }) => {
      await begin(page);
      await emit(page);
      await page.evaluate(() => {
        const track = window.__streams[0].getAudioTracks()[0];
        track.stop();
        track.dispatchEvent(new Event('ended'));
      });
      await page
        .getByRole('alert')
        .filter({ hasText: 'Audio capture ended unexpectedly' })
        .waitFor();
      assert.equal(await page.evaluate(() => window.__recorder.state), 'inactive');
      await stop(page);
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: /storage failed|incomplete/ })
        .waitFor();
    },
  );
  await check(
    'speech startup: slow token never locks Stop or starts recognition after stop',
    async ({ page }) => {
      let release,
        tokenRequests = 0;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      await page.route('**/api/speech/token', async (route) => {
        tokenRequests++;
        await gate;
        await route.fulfill({
          json: { token: 'synthetic', region: 'test', expires_in_seconds: 600 },
        });
      });
      await page.evaluate(() => {
        const original = Date.now;
        Date.now = () => original() + 660000;
      });
      await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
      await page.waitForFunction(() => window.__recorder?.state === 'recording');
      await page.waitForFunction(
        () =>
          ![...document.querySelectorAll('button')].find(
            (button) => button.textContent.trim() === 'Stop Meeting',
          )?.disabled,
      );
      assert(await page.getByRole('button', { name: 'Pause', exact: true }).isEnabled());
      assert.equal(await page.locator('.alerts, .alert').count(), 0);
      await stop(page);
      release();
      await page.waitForLoadState('networkidle');
      assert.equal(tokenRequests, 1);
      assert.equal(await page.evaluate(() => !!window.__recognizer), false);
      assert.equal(await page.locator('.alerts, .alert').count(), 0);
    },
  );
  await check(
    'speech startup: late connection callback cannot revive a stopped meeting',
    async ({ page }) => {
      await page.evaluate(() => {
        window.__fault = 'speech-start-delayed';
      });
      await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
      await page.waitForFunction(() => !!window.__releaseSpeechStart);
      assert(await page.getByRole('button', { name: 'Stop Meeting', exact: true }).isEnabled());
      await stop(page);
      await page.evaluate(() => window.__releaseSpeechStart());
      assert.equal(await page.evaluate(() => window.__recognizer.closed), true);
      assert.equal(await page.locator('.health-list b.healthy').count(), 0);
    },
  );
  await check('speech startup: transient disconnect reconnects automatically', async ({ page }) => {
    await begin(page);
    await page.evaluate(() => {
      window.__oldRecognizer = window.__recognizer;
      window.__recognizer.canceled(null, { reason: 'Synthetic outage' });
    });
    await page.waitForFunction(
      () =>
        window.__recognizer !== window.__oldRecognizer &&
        [...document.querySelectorAll('.health-list b')].some(
          (el) => el.textContent.trim() === 'connected',
        ),
    );
    assert.equal(await page.evaluate(() => window.__oldRecognizer.closed), true);
    await emit(page, 'Synthetic speech after automatic reconnection.');
    await stop(page);
    assert.equal(await page.locator('.alerts, .alert').count(), 0);
  });
  await check(
    'speech startup: validation feedback stays in the footer without banners',
    async ({ page }) => {
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: 'Confirm recording authorization' })
        .waitFor();
      assert.equal(await page.locator('.workspace-main [role="alert"]').count(), 0);
      assert.equal(await page.locator('.alerts, .alert').count(), 0);
      for (const width of [1366, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 768 });
        assert(await page.getByRole('alert').isVisible());
        const feedback = await page.getByRole('alert').boundingBox();
        assert(feedback.y + feedback.height <= page.viewportSize().height);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: path.join(output, `compact-feedback-${width}.png`) });
      }
    },
  );
  await check(
    'extended: Speech start callback timeout releases recording controls',
    async ({ page }) => {
      await page.evaluate(() => {
        window.__fault = 'speech-start-hang';
      });
      await page.getByRole('checkbox', { name: /I confirm recording/ }).check();
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: 'Speech operation timed out' })
        .waitFor({ timeout: 24000 });
      assert(await page.getByRole('button', { name: 'Stop Meeting', exact: true }).isEnabled());
      await stop(page);
    },
  );
  await check(
    'extended: Speech shutdown timeout still stops audio and flags incomplete capture',
    async ({ page }) => {
      await begin(page);
      await emit(page);
      await page.evaluate(() => {
        window.__fault = 'speech-stop-hang';
      });
      await page.getByRole('button', { name: 'Stop Meeting', exact: true }).click();
      await page.waitForTimeout(100);
      assert(
        await page.evaluate(() =>
          window.__streams
            .flatMap((stream) => stream.getTracks())
            .every((track) => track.readyState === 'ended'),
        ),
      );
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.__recorder.state), 'inactive');
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: /incomplete|storage failed/ })
        .waitFor();
    },
  );
  await check(
    'extended: failed pause keeps local controls usable after backend recovery',
    async ({ page, state }) => {
      await begin(page);
      await page.route('**/api/meetings/*/pause', (route) =>
        route.fulfill({ status: 503, json: { detail: 'Synthetic pause outage' } }),
      );
      await page.getByRole('button', { name: 'Pause', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'Synthetic pause outage' }).waitFor();
      await page.getByRole('button', { name: 'Resume', exact: true }).waitFor();
      await page.unroute('**/api/meetings/*/pause');
      await page.getByRole('button', { name: 'Resume', exact: true }).click();
      await page.getByRole('button', { name: 'Pause', exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.__recorder.state), 'recording');
      await stop(page);
    },
  );
  await check(
    'extended: save response arriving after selection cannot replace another draft',
    async ({ page, state }) => {
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await chooseRow(
        page,
        page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first(),
      );
      await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
      state.patchBarrier = new Promise((resolve) => {
        state.releasePatch = resolve;
      });
      await page.getByRole('button', { name: 'Save Draft', exact: true }).click();
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      const target = page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).nth(1);
      const title = await target.locator('b').innerText();
      await chooseRow(page, target);
      state.releasePatch();
      await page.waitForLoadState('networkidle');
      await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
      assert.equal(await page.locator('#mom-title').inputValue(), title);
    },
  );
  await check(
    'extended: hostile transcript is displayed as text, not executable HTML',
    async ({ page }) => {
      await begin(page);
      await emit(page, '<img src=x onerror="window.__xss=true">');
      assert.equal(await page.evaluate(() => window.__xss), undefined);
      assert.equal(await page.locator('.transcript img').count(), 0);
      await stop(page);
    },
  );
  await check(
    'extended: mobile controls and history have no horizontal overflow',
    async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({ path: path.join(output, 'mobile-history.png') });
    },
  );
  await check('extended: late audio stays attached to its original meeting', async ({ page }) => {
    await begin(page, 'Synthetic old audio owner');
    await page.evaluate(() => {
      window.__oldRecorder = window.__recorder;
    });
    await stop(page);
    await page.getByRole('button', { name: 'New Meeting', exact: true }).click();
    await begin(page, 'Synthetic new audio owner');
    await page.evaluate(() => window.__oldRecorder.requestData());
    await page.waitForLoadState('networkidle');
    const records = (await api('/api/meetings')).data;
    const old = records.find((m) => m.title === 'Synthetic old audio owner');
    const current = records.find((m) => m.title === 'Synthetic new audio owner');
    for (let attempt = 0; attempt < 30; attempt++) {
      if ((await api(`/api/meetings/${old.id}/audio-chunks`)).data.length === 2) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    assert.equal((await api(`/api/meetings/${old.id}/audio-chunks`)).data.length, 2);
    assert.equal((await api(`/api/meetings/${current.id}/audio-chunks`)).data.length, 0);
    await stop(page);
  });
  await check(
    'extended: recorder shutdown timeout cannot trap controls or claim completeness',
    async ({ page }) => {
      await begin(page);
      await emit(page);
      await page.evaluate(() => {
        window.__fault = 'recorder-stop-hang';
      });
      await stop(page);
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'incomplete' }).waitFor();
    },
  );
  await check(
    'extended: reloaded session cannot pretend this tab still owns a recorder',
    async ({ page }) => {
      await begin(page);
      await page.evaluate(() => window.__recorder.requestData());
      page.on('dialog', (dialog) => dialog.accept());
      await page.reload();
      await page
        .getByRole('alert')
        .filter({ hasText: 'This tab has no active recorder' })
        .waitFor();
      assert(await page.getByRole('button', { name: 'Pause', exact: true }).isDisabled());
      await stop(page);
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'incomplete' }).waitFor();
    },
  );
  await check(
    'extended: invalid acknowledgements never discard pending audio text or stop',
    async ({ page }) => {
      const patterns = [
        '**/api/meetings/*/audio-chunks',
        '**/api/meetings/*/segments',
        '**/api/meetings/*/stop',
      ];
      for (const pattern of patterns)
        await page.route(pattern, (route) =>
          route.fulfill({ status: 200, json: { detail: 'Synthetic missing acknowledgement' } }),
        );
      await begin(page, 'Synthetic acknowledgement recovery');
      await emit(page);
      await stop(page);
      const queued = await page.evaluate(async () => {
        const store = await import('/src/offline.ts');
        return {
          audio: (await store.pendingChunks()).length,
          text: (await store.pendingSegments()).length,
          stops: (await store.pendingStops()).length,
        };
      });
      assert(queued.audio > 0 && queued.text > 0 && queued.stops === 1);
      for (const pattern of patterns) await page.unroute(pattern);
      for (let attempt = 0; attempt < 100; attempt++) {
        const remaining = await page.evaluate(async () => {
          const store = await import('/src/offline.ts');
          return (
            (await store.pendingChunks()).length +
            (await store.pendingSegments()).length +
            (await store.pendingStops()).length
          );
        });
        if (!remaining) break;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      const remaining = await page.evaluate(async () => {
        const store = await import('/src/offline.ts');
        return (
          (await store.pendingChunks()).length +
          (await store.pendingSegments()).length +
          (await store.pendingStops()).length
        );
      });
      assert.equal(remaining, 0);
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 15000 });
    },
  );
  await check('regression: completed job is read before its minutes', async ({ page }) => {
    await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
    await chooseRow(
      page,
      page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first(),
    );
    const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
    const minutes = (await api(`/api/meetings/${mid}/mom`)).data;
    minutes.data.summary = 'COMPLETED RESULT READ AFTER JOB';
    let jobsRead = false;
    await page.route(`**/api/meetings/${mid}/jobs`, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 100));
      jobsRead = true;
      await route.fulfill({
        json: [{ id: 'race-job', kind: 'generate_mom', status: 'completed', attempts: 1 }],
      });
    });
    await page.route(`**/api/meetings/${mid}/mom`, (route) =>
      route.fulfill(jobsRead ? { json: minutes } : { status: 404, json: { detail: 'Not ready' } }),
    );
    await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
    await page.waitForFunction(() =>
      document.querySelector('#mom-editor')?.value.includes('COMPLETED RESULT READ AFTER JOB'),
    );
  });
  await check('regression: dirty draft keeps polling regeneration status', async ({ page }) => {
    await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
    await chooseRow(
      page,
      page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first(),
    );
    const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
    let edited = false;
    await page.route(`**/api/meetings/${mid}/jobs`, async (route) => {
      const jobs = (await api(`/api/meetings/${mid}/jobs`)).data;
      await route.fulfill({
        json: edited ? jobs : jobs.map((job) => ({ ...job, status: 'running' })),
      });
    });
    await api('/__test__/mode', 'POST', { value: 'rate-limit' });
    await api(`/api/meetings/${mid}/mom/generate`, 'POST');
    await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
    const editor = page.locator('#mom-editor');
    await editor.waitFor();
    const draft = 'KEEP THIS UNSAVED DRAFT WHILE POLLING';
    await page.waitForFunction(() =>
      [...document.querySelectorAll('button')].some(
        (button) => button.textContent.trim() === 'Save Draft' && button.disabled,
      ),
    );
    await editor.fill(draft);
    edited = true;
    await page.waitForFunction(
      () =>
        [...document.querySelectorAll('button')].some(
          (button) => button.textContent.trim() === 'Save Draft' && !button.disabled,
        ),
      undefined,
      { timeout: 16000 },
    );
    assert.equal(await editor.inputValue(), draft);
    assert(await page.getByRole('button', { name: 'Approve', exact: true }).isDisabled());
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
    assert.deepEqual(
      await editor.inputValue(),
      (await api(`/api/meetings/${mid}/mom`)).data.data.summary,
    );
  });
  await check(
    'architecture: pause stops capture immediately when Speech shutdown hangs',
    async ({ page }) => {
      await begin(page);
      await page.evaluate(() => {
        window.__fault = 'speech-stop-hang';
      });
      await page.getByRole('button', { name: 'Pause', exact: true }).click();
      await page.waitForTimeout(250);
      assert.equal(await page.evaluate(() => window.__recorder.state), 'paused');
      await page
        .getByRole('alert')
        .filter({ hasText: /timed out/ })
        .waitFor({ timeout: 10000 });
      assert.equal(await page.evaluate(() => window.__recorder.state), 'paused');
      await page.evaluate(() => {
        window.__fault = '';
      });
      await stop(page);
    },
  );
  await check(
    'architecture: offline transcription loss survives stop and reload',
    async ({ page, state }) => {
      await begin(page);
      await emit(page);
      const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
      state.outage = true;
      await page.evaluate(() =>
        window.__recognizer.canceled(null, { reason: 'Synthetic speech outage' }),
      );
      await page.getByRole('alert').filter({ hasText: 'Azure recognition interrupted' }).waitFor();
      await stop(page);
      state.outage = false;
      await page.reload();
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).waitFor();
      await page.waitForTimeout(1500);
      const events = (await api(`/api/meetings/${mid}/audit`)).data;
      assert(events.some((event) => event.action === 'transcript.loss'));
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: /incomplete|transcription/ })
        .waitFor();
      assert.equal(await page.locator('#mom-editor').count(), 0);
    },
  );
  await check(
    'architecture: first minutes refresh failure recovers automatically',
    async ({ page }) => {
      await begin(page);
      await emit(page);
      await stop(page);
      const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
      let fail = true;
      let queued = false;
      page.on('request', (request) => {
        if (request.url().endsWith('/mom/generate') && request.method() === 'POST') queued = true;
      });
      await page.route(`**/api/meetings/${mid}/mom`, async (route) => {
        if (queued && fail && route.request().method() === 'GET') {
          fail = false;
          return route.fulfill({ status: 503, json: { detail: 'Synthetic transient error' } });
        }
        return route.fallback();
      });
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 16000 });
      assert((await page.locator('#mom-editor').inputValue()).includes('synthetic'));
    },
  );
  await check(
    'architecture: late capture response cannot select an earlier recording',
    async ({ page }) => {
      let held = false,
        release;
      const barrier = new Promise((resolve) => {
        release = resolve;
      });
      await page.route('**/api/meetings/*/capture-status', async (route) => {
        const body = route.request().postDataJSON();
        if (!held && body.stt_status === 'connected') {
          held = true;
          const response = await api(new URL(route.request().url()).pathname, 'POST', body);
          await barrier;
          return route.fulfill({ status: response.status, json: response.data });
        }
        return route.fallback();
      });
      try {
        await begin(page, 'Earlier recording');
        await stop(page);
        await page.getByRole('button', { name: 'New Meeting', exact: true }).click();
        await begin(page, 'Current recording');
        const mid = await page.evaluate(() => localStorage.getItem('lastMeetingId'));
        release();
        await page.waitForTimeout(300);
        assert.equal(await page.locator('.session-top h2').innerText(), 'Current recording');
        assert.equal(await page.evaluate(() => localStorage.getItem('lastMeetingId')), mid);
        assert.equal(await page.evaluate(() => window.__recorder.state), 'recording');
        await stop(page);
      } finally {
        release();
      }
    },
  );
  await check(
    'minutes: long content stays readable at desktop, tablet and phone sizes',
    async ({ page }) => {
      await longMinutes(page);
      for (const size of [
        { width: 1920, height: 900 },
        { width: 1366, height: 768 },
        { width: 1280, height: 720 },
        { width: 820, height: 700 },
        { width: 390, height: 844 },
        { width: 360, height: 740 },
      ]) {
        await page.setViewportSize(size);
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        const fit = await page.evaluate(() => {
          const pane = document.querySelector('.minutes-scroll');
          const footer = document.querySelector('.minutes-actions').getBoundingClientRect();
          return {
            width: document.documentElement.scrollWidth,
            height: document.documentElement.scrollHeight,
            paneHeight: pane.clientHeight,
            paneBottom: pane.getBoundingClientRect().bottom,
            footerTop: footer.top,
            footerBottom: footer.bottom,
            clippedFields: [...document.querySelectorAll('.minutes-document textarea')]
              .filter((field) => field.scrollHeight > field.clientHeight + 2)
              .map((field) => field.getAttribute('aria-label')),
          };
        });
        assert(fit.width <= size.width, `Horizontal overflow: ${JSON.stringify({ size, fit })}`);
        assert(fit.height <= size.height, `Page overflow: ${JSON.stringify({ size, fit })}`);
        assert(fit.paneHeight >= 200, `Reading area shrank: ${JSON.stringify({ size, fit })}`);
        assert(
          fit.footerTop >= fit.paneBottom - 1 && fit.footerBottom <= size.height,
          'Approval controls overlap the document',
        );
        assert.deepEqual(
          fit.clippedFields,
          [],
          'Long text must expand instead of scrolling inside a field',
        );
        for (const title of [
          'Discussion',
          'Decisions',
          'Action items',
          'Open questions',
          'Summary',
        ]) {
          await page
            .getByRole('navigation', { name: 'Minutes sections' })
            .getByRole('button', { name: new RegExp(`^${title}`) })
            .click();
          const selector = {
            Discussion: '.discussion-card',
            Decisions: '.decisions-card',
            'Action items': '.actions-card',
            'Open questions': '.questions-card',
            Summary: '.summary-card',
          }[title];
          const heading = await page.locator(`${selector} h3`).boundingBox();
          const pane = await page.locator('.minutes-scroll').boundingBox();
          assert(
            heading.y >= pane.y - 1 && heading.y + heading.height <= pane.y + pane.height,
            `Section inaccessible: ${title}`,
          );
        }
        await page.screenshot({ path: path.join(output, `minutes-long-${size.width}.png`) });
      }
      await page.setViewportSize({ width: 740, height: 400 });
      await page.getByRole('button', { name: 'Save Draft', exact: true }).scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    },
  );
  await check(
    'minutes: edits, evidence, revision preview and approval remain safe',
    async ({ page }) => {
      const { minutes, writes } = await longMinutes(page);
      assert.equal(await page.locator('.minutes-workspace .alert.info').count(), 0);
      assert.equal(await page.locator('.minutes-jobs').count(), 0);
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      assert.equal(await page.locator('.minutes-workspace .alert.info').count(), 0);
      const summary = page.locator('#mom-editor');
      const revised = 'A reviewed summary with long content. '.repeat(30);
      await summary.fill(revised);
      assert(await page.getByRole('button', { name: 'Approve', exact: true }).isDisabled());
      await page
        .getByRole('navigation', { name: 'Minutes sections' })
        .getByRole('button', { name: /^Discussion/ })
        .click();
      await page
        .locator('.discussion-card')
        .getByRole('button', { name: '+ Add point', exact: true })
        .click();
      await page
        .getByRole('textbox', { name: 'Discussion point 31', exact: true })
        .fill(
          'Additional point with a preserved line break.\nA second line that must remain readable.',
        );
      await page.getByRole('button', { name: 'Remove discussion point 31', exact: true }).click();
      assert.equal(
        await page.getByRole('textbox', { name: 'Discussion point 31', exact: true }).count(),
        0,
      );
      await page.getByRole('button', { name: 'Version history', exact: true }).click();
      await page.locator('.revision-strip button').first().click();
      assert(await summary.evaluate((field) => field.readOnly));
      assert(await page.getByRole('button', { name: 'Save Draft', exact: true }).isDisabled());
      await page.getByRole('button', { name: 'Back to current draft', exact: true }).click();
      assert.equal(await summary.inputValue(), revised);
      assert.equal(
        await page.getByRole('textbox', { name: 'Discussion point 1', exact: true }).count(),
        1,
      );
      await page
        .getByRole('navigation', { name: 'Minutes sections' })
        .getByRole('button', { name: /^Decisions/ })
        .click();
      await page
        .locator('.decisions-card')
        .getByRole('button', { name: 'View supporting transcript', exact: true })
        .first()
        .click();
      await page.getByRole('dialog', { name: 'Supporting transcript' }).waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await page.getByRole('dialog').count(), 0);
      await page.getByRole('button', { name: 'Save Draft', exact: true }).click();
      await page.getByText('MoM draft saved.', { exact: true }).waitFor();
      assert.equal(
        await page
          .locator('.minutes-actions')
          .getByText('MoM draft saved.', { exact: true })
          .count(),
        1,
      );
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await page
        .locator('.minutes-actions')
        .getByText('MoM draft saved.', { exact: true })
        .waitFor();
      assert.equal(minutes.data.summary, revised);
      assert.equal(minutes.data.discussion_points.length, 30);
      assert.equal(minutes.data.action_items.length, 12);
      await page.getByRole('button', { name: 'Approve', exact: true }).click();
      await page.getByText('MoM approved.', { exact: true }).waitFor();
      await page.waitForFunction(() =>
        [...document.querySelectorAll('.minutes-document textarea')].every(
          (field) => field.readOnly,
        ),
      );
      assert.deepEqual(writes, ['save', 'approve']);
    },
  );
  await check(
    'architecture: minutes document scrolls without covering approval controls',
    async ({ page }) => {
      await page.getByRole('button', { name: 'Meeting History', exact: true }).click();
      await chooseRow(
        page,
        page.locator('.meeting-row').filter({ hasText: 'Synthetic history' }).first(),
      );
      await page.getByRole('button', { name: 'Meeting Minutes', exact: true }).click();
      await page.locator('#mom-editor').waitFor();
      for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 768 });
        assert(await page.locator('.summary-card').isVisible());
        assert(await page.locator('.decisions-card').isVisible());
        assert(await page.locator('.actions-card').isVisible());
        assert(await page.locator('.discussion-card').isVisible());
        await page
          .getByRole('navigation', { name: 'Minutes sections' })
          .getByRole('button', { name: /Action items/ })
          .click();
        const bounds = await page.locator('.minutes-actions').boundingBox();
        const document = await page.locator('.minutes-scroll').boundingBox();
        assert(bounds.y >= document.y + document.height - 1);
        assert(bounds.y + bounds.height <= 768);
        assert(
          await page.evaluate(
            () =>
              document.documentElement.scrollHeight <= innerHeight &&
              document.documentElement.scrollWidth <= innerWidth,
          ),
        );
      }
      await page.screenshot({ path: path.join(output, 'minutes-document.png') });
    },
  );
  await check(
    'architecture: verified recovery after Speech shutdown failure enables fresh minutes',
    async ({ page }) => {
      await page.evaluate(() => {
        window.MediaRecorder = window.__nativeRecorder;
        // A native synthetic microphone keeps its audio clock independent of Web Audio rendering.
        navigator.mediaDevices.getUserMedia = window.__nativeGetUserMedia;
      });
      await begin(page, 'Native recovery regression');
      await emit(page);
      await page.waitForTimeout(700);
      await page.evaluate(() => {
        window.__fault = 'speech-stop-hang';
      });
      await stop(page);
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page
        .getByRole('alert')
        .filter({ hasText: /incomplete|transcription/ })
        .waitFor();
      await page.getByRole('button', { name: 'Recover transcript', exact: true }).click();
      await page.getByText('Transcript recovered', { exact: true }).waitFor({ timeout: 60000 });
      await page.getByRole('button', { name: 'Generate MoM', exact: true }).click();
      await page.locator('#mom-editor').waitFor({ timeout: 15000 });
      assert((await page.locator('#mom-editor').inputValue()).includes('synthetic'));
    },
  );
} finally {
  await browser.close();
  python.stdin.end();
  await writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
}
console.log(
  JSON.stringify({
    passed: results.filter((r) => r.passed).length,
    failed: results.filter((r) => !r.passed).length,
  }),
);
if (results.some((r) => !r.passed)) process.exitCode = 1;
