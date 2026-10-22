import type { SpeechToken } from './types';

/** Keep short-lived credentials in memory; never persist them in browser storage. */
export function createSpeechPreparation<T>(
  loadSdk: () => Promise<T>,
  loadToken: () => Promise<SpeechToken>,
) {
  let sdk: Promise<T> | null = null;
  let token: SpeechToken | null = null;
  let expiresAt = 0;
  let pending: Promise<SpeechToken> | null = null;
  return async function prepare() {
    if (!sdk)
      sdk = loadSdk().catch((error) => {
        sdk = null;
        throw error;
      });
    if (!token || Date.now() >= expiresAt) {
      if (!pending) {
        const requestedAt = Date.now();
        pending = loadToken()
          .then((value) => {
            token = value;
            expiresAt = requestedAt + Math.max(0, value.expires_in_seconds - 60) * 1000;
            return value;
          })
          .finally(() => {
            pending = null;
          });
      }
    }
    const [speechsdk, credentials] = await Promise.all([sdk, pending ?? Promise.resolve(token!)]);
    return { speechsdk, credentials };
  };
}
