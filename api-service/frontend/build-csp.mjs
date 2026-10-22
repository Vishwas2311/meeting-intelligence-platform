import { writeFile } from 'node:fs/promises';

const authority = new URL(process.env.VITE_OIDC_AUTHORITY || '');
const region = process.env.VITE_SPEECH_REGION || '';
if (
  authority.protocol !== 'https:' ||
  authority.username ||
  authority.password ||
  !/^[a-z0-9-]+$/.test(region)
)
  throw new Error(
    'Set the approved HTTPS OIDC authority and Speech region before building the production web image.',
  );
const policy = `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ${authority.origin} wss://${region}.stt.speech.microsoft.com; frame-src 'self' ${authority.origin}; worker-src 'self' blob:; media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self' ${authority.origin}`;
await writeFile('csp.conf', `add_header Content-Security-Policy "${policy}" always;\n`);
