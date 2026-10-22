import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const environment = {
  DEV: false,
  PROD: true,
  VITE_OIDC_AUTHORITY: 'https://identity.synthetic.invalid/tenant',
  VITE_OIDC_CLIENT_ID: 'public-synthetic',
  VITE_OIDC_SCOPE: 'openid profile Meeting.Access',
};
const dataUrl = (source) => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
async function compile(name, replacements = {}) {
  let source = await readFile(new URL(`./src/${name}.ts`, import.meta.url), 'utf8');
  source = source.replaceAll('import.meta.env', JSON.stringify(environment));
  let js = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  for (const [from, to] of Object.entries(replacements))
    js = js.replaceAll(`'${from}'`, JSON.stringify(to));
  return dataUrl(js);
}

test('PKCE callback is consumed once, tokens stay in memory, and owner changes block API access', async () => {
  const calls = [];
  globalThis.sessionStorage = {
    removeItem: (key) => calls.push(['remove', key]),
    setItem: () => {
      throw Error('Token persisted');
    },
  };
  globalThis.location = {
    origin: 'https://meetings.synthetic.invalid',
    pathname: '/auth/callback',
    replace: (path) => calls.push(['redirect', path]),
  };
  globalThis.history = {
    replaceState: () => {
      location.pathname = '/';
    },
  };
  globalThis.__identity = {
    profile: { name: 'Alice', sub: 'alice' },
    access_token: 'synthetic-alice',
    expired: false,
  };
  const sdk = dataUrl(`
    export class InMemoryWebStorage {}
    export class WebStorageStateStore {constructor(options){this.options=options;}}
    export class ErrorResponse extends Error {}
    export class UserManager {
      constructor(settings){globalThis.__manager=this;this.settings=settings;this.events={addUserUnloaded:fn=>this.unloaded=fn,addSilentRenewError:fn=>this.renewError=fn,addUserLoaded:fn=>this.loaded=fn};}
      async getUser(){return globalThis.__identity;}
      async signinSilent(){return globalThis.__identity;}
      async signinCallback(){globalThis.__callbacks=(globalThis.__callbacks||0)+1;return globalThis.__identity;}
      async signoutRedirect(){}
    }
  `);
  const apiUrl = await compile('api'),
    vaultUrl = await compile('localVault'),
    lifecycleUrl = await compile('sessionLifecycle');
  const authUrl = await compile('auth', {
    'oidc-client-ts': sdk,
    './api': apiUrl,
    './localVault': vaultUrl,
    './sessionLifecycle': lifecycleUrl,
  });
  const priorFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url) => {
      if (url === '/api/local-cache/key')
        return new Response(
          JSON.stringify({
            actor: 'alice',
            current_generation: 'one',
            keys: [{ generation: 'one', key: Buffer.alloc(32, 1).toString('base64') }],
          }),
        );
      calls.push(['request', url]);
      return new Response(JSON.stringify({ ok: true }));
    };
    const auth = await import(authUrl),
      api = await import(apiUrl),
      vault = await import(vaultUrl);
    assert.deepEqual(await Promise.all([auth.resumeIdentity(), auth.resumeIdentity()]), [
      'Alice',
      'Alice',
    ]);
    assert.equal(globalThis.__callbacks, 1);
    assert.equal(__manager.settings.response_type, 'code');
    assert(
      __manager.settings.userStore.options.store instanceof (await import(sdk)).InMemoryWebStorage,
    );
    assert.equal(api.getToken(), 'synthetic-alice');
    const name = vault.databaseName('recovery');
    __manager.renewError(new Error('Synthetic network outage'));
    assert.equal(vault.databaseName('recovery'), name);
    globalThis.__identity = {
      profile: { name: 'Bob', sub: 'bob' },
      access_token: 'synthetic-bob',
      expired: false,
    };
    await assert.rejects(api.request('/api/test'), /Account changed/);
    assert.equal(calls.filter(([kind]) => kind === 'request').length, 0);
    assert.equal(api.getToken(), '');
    assert.throws(() => vault.databaseName('recovery'));
  } finally {
    globalThis.fetch = priorFetch;
  }
});
