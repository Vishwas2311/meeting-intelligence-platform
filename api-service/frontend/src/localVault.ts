// Persistent recovery stays encrypted after logout; unlocking requires the owner online.
const production = import.meta.env.PROD;
let actor = '';
let current = '';
const keys = new Map<string, CryptoKey>();
export function lockVault() {
  actor = '';
  current = '';
  keys.clear();
}
export async function unlockVault(value: {
  actor: string;
  current_generation: string;
  keys: { generation: string; key: string }[];
}) {
  lockVault();
  for (const entry of value.keys) {
    const bytes = Uint8Array.from(atob(entry.key), (c) => c.charCodeAt(0));
    keys.set(
      entry.generation,
      await crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt']),
    );
  }
  if (!keys.has(value.current_generation)) throw new Error('Recovery key is unavailable.');
  actor = value.actor;
  current = value.current_generation;
}
export function databaseName(base: string) {
  if (!production) return base;
  if (!actor) throw new Error('Sign in online to unlock your saved recordings.');
  return `${base}-owner-${actor}`;
}
export function ownerKey(base: string) {
  return production ? databaseName(base) : base;
}
function base64(bytes: Uint8Array): string {
  let value = '';
  for (let i = 0; i < bytes.length; i += 32768)
    value += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(value);
}
async function pack(value: unknown): Promise<unknown> {
  if (value instanceof Blob)
    return {
      __recovery_blob: true,
      mime: value.type,
      bytes: base64(new Uint8Array(await value.arrayBuffer())),
    };
  if (Array.isArray(value)) return Promise.all(value.map(pack));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      await Promise.all(Object.entries(value).map(async ([k, v]) => [k, await pack(v)])),
    );
  }
  return value;
}
function unpack(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(unpack);
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if (record.__recovery_blob === true && typeof record.bytes === 'string') {
      return new Blob([Uint8Array.from(atob(record.bytes), (c) => c.charCodeAt(0))], {
        type: String(record.mime),
      });
    }
    return Object.fromEntries(Object.entries(record).map(([k, v]) => [k, unpack(v)]));
  }
  return value;
}
export async function seal<T>(value: T, store: string): Promise<T> {
  if (!production) return value;
  const key = keys.get(current);
  if (!actor || !key) throw new Error('Recovery storage is locked. Sign in again.');
  const capturedActor = actor;
  const capturedGeneration = current;
  const record = value as Record<string, unknown>;
  const id = String(record.id ?? 'record');
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const bytes = new TextEncoder().encode(JSON.stringify(await pack(value)));
  const aad = new TextEncoder().encode(`${capturedActor}\0${store}\0${id}\0${capturedGeneration}`);
  const encrypted = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: nonce, additionalData: aad },
    key,
    bytes,
  );
  if (capturedActor !== actor) throw new Error('Account changed; recovery write cancelled.');
  return {
    id: record.id,
    version: record.version,
    synced: record.synced,
    owner: actor,
    generation: capturedGeneration,
    nonce: base64(nonce),
    ciphertext: base64(new Uint8Array(encrypted)),
  } as T;
}
export async function unseal<T>(value: T, store: string): Promise<T> {
  if (!production || value === undefined || value === null || typeof value !== 'object')
    return value;
  const record = value as Record<string, unknown>;
  if (typeof record.ciphertext !== 'string' || record.owner !== actor)
    throw new Error('Saved data belongs to another account or needs legacy recovery.');
  const key = keys.get(String(record.generation));
  if (!key) throw new Error('An older recovery key is unavailable; keep this browser profile.');
  const capturedActor = actor;
  const aad = new TextEncoder().encode(
    `${actor}\0${store}\0${String(record.id ?? 'record')}\0${record.generation}`,
  );
  const bytes = await crypto.subtle.decrypt(
    {
      name: 'AES-GCM',
      iv: Uint8Array.from(atob(String(record.nonce)), (c) => c.charCodeAt(0)),
      additionalData: aad,
    },
    key,
    Uint8Array.from(atob(record.ciphertext), (c) => c.charCodeAt(0)),
  );
  if (capturedActor !== actor) throw new Error('Account changed; recovery read cancelled.');
  const result = unpack(JSON.parse(new TextDecoder().decode(bytes))) as T;
  if ('synced' in record && record.synced !== undefined)
    Object.assign(result as object, { synced: record.synced });
  return result;
}
export async function unsealResult<T>(value: T, store: string): Promise<T> {
  return (
    Array.isArray(value)
      ? await Promise.all(value.map((v) => unseal(v, store)))
      : await unseal(value, store)
  ) as T;
}
