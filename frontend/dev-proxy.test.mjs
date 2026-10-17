import assert from 'node:assert/strict';
import test from 'node:test';
import config, { localApiGuard } from './vite.config.mjs';

function check(overrides = {}) {
  const result = { allowed: false, statusCode: 200, body: '' };
  localApiGuard(
    {
      url: '/api/meetings',
      headers: { host: 'localhost:5173' },
      socket: { remoteAddress: '127.0.0.1' },
      ...overrides,
    },
    {
      set statusCode(value) {
        result.statusCode = value;
      },
      setHeader() {},
      end(value) {
        result.body = value;
      },
    },
    () => {
      result.allowed = true;
    },
  );
  return result;
}

function withEnv(values, run) {
  const before = Object.fromEntries(Object.keys(values).map((key) => [key, process.env[key]]));
  Object.assign(process.env, values);
  try {
    return run();
  } finally {
    for (const [key, value] of Object.entries(before)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

test('local API requests are accepted with and without a matching Origin', () => {
  assert.equal(check().allowed, true);
  assert.equal(
    check({ headers: { host: 'localhost:5173', origin: 'http://localhost:5173' } }).allowed,
    true,
  );
  assert.equal(
    check({ headers: { host: '127.0.0.1:5173' }, socket: { remoteAddress: '::ffff:127.0.0.1' } })
      .allowed,
    true,
  );
});

test('cross-origin, invalid-host, and remote requests are rejected', () => {
  for (const overrides of [
    { headers: { host: 'localhost:5173', origin: 'https://untrusted.example' } },
    { headers: { host: 'untrusted.example:5173' } },
    { socket: { remoteAddress: '192.168.1.10' } },
  ]) {
    const result = check(overrides);
    assert.equal(result.allowed, false);
    assert.equal(result.statusCode, 403);
  }
});

test('non-API asset requests are unaffected', () => {
  assert.equal(check({ url: '/src/main.tsx', headers: {} }).allowed, true);
});

test('development credentials stay in the server proxy configuration', () =>
  withEnv({ APP_API_TOKEN: 'proxy-test-token', VITE_API_URL: 'http://127.0.0.1:8000' }, () => {
    const settings = config({ command: 'serve', mode: 'test', isPreview: false });
    assert.equal(settings.server.host, '127.0.0.1');
    assert.equal(settings.server.cors, false);
    assert.equal(settings.server.proxy['/api'].headers.Authorization, 'Bearer proxy-test-token');
    assert.equal(settings.define, undefined);
  }));

test('missing credentials or a remote API target fail closed', () => {
  withEnv({ APP_API_TOKEN: '' }, () =>
    assert.throws(() => config({ command: 'serve', mode: 'test' }), /Set APP_API_TOKEN/),
  );
  withEnv({ APP_API_TOKEN: 'proxy-test-token', VITE_API_URL: 'https://untrusted.example' }, () =>
    assert.throws(() => config({ command: 'serve', mode: 'test' }), /localhost API origin/),
  );
});

test('production builds and previews have no automatic credential proxy', () => {
  assert.deepEqual(config({ command: 'build', mode: 'production' }), {});
  assert.deepEqual(config({ command: 'serve', mode: 'production', isPreview: true }), {});
});
