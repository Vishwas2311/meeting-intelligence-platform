import {
  ErrorResponse,
  InMemoryWebStorage,
  UserManager,
  WebStorageStateStore,
} from 'oidc-client-ts';
import { setToken, setTokenProvider } from './api';
import { lockVault, unlockVault } from './localVault';
import { closeLocalSession } from './sessionLifecycle';

let manager: UserManager | undefined;
let signedInSubject = '';
export function identityManager() {
  if (manager) return manager;
  const authority = import.meta.env.VITE_OIDC_AUTHORITY || '';
  const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || '';
  if (!authority.startsWith('https://') || !clientId)
    throw new Error('Company sign-in is not configured. Contact your administrator.');
  manager = new UserManager({
    authority,
    client_id: clientId,
    response_type: 'code',
    scope: import.meta.env.VITE_OIDC_SCOPE || 'openid profile Meeting.Access',
    redirect_uri: import.meta.env.VITE_OIDC_REDIRECT_URI || `${location.origin}/auth/callback`,
    silent_redirect_uri:
      import.meta.env.VITE_OIDC_REDIRECT_URI || `${location.origin}/auth/callback`,
    post_logout_redirect_uri: import.meta.env.VITE_OIDC_POST_LOGOUT_URI || location.origin,
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
    stateStore: new WebStorageStateStore({ store: sessionStorage }),
  });
  const expired = async () => {
    try {
      await closeLocalSession();
    } finally {
      setToken('');
      lockVault();
      location.replace('/');
    }
  };
  manager.events.addUserUnloaded(() => {
    void expired().catch(() => {});
  });
  manager.events.addSilentRenewError((error) => {
    // A network outage must not discard an already unlocked offline recording.
    if (
      error instanceof ErrorResponse &&
      ['login_required', 'interaction_required', 'invalid_grant'].includes(error.error || '')
    )
      void expired().catch(() => {});
  });
  manager.events.addUserLoaded((user) => {
    if (signedInSubject && user.profile.sub !== signedInSubject) void expired().catch(() => {});
  });
  setTokenProvider(async () => {
    let user = await manager!.getUser();
    if (!user || user.expired) user = await manager!.signinSilent();
    if (!user?.access_token) throw new Error('Your session expired. Sign in again.');
    if (signedInSubject && user.profile.sub !== signedInSubject) {
      await expired();
      throw new Error('Account changed. Sign in again before accessing saved recordings.');
    }
    setToken(user.access_token);
    return user.access_token;
  });
  return manager;
}
let resuming: Promise<string | null> | undefined;
export function resumeIdentity() {
  // React StrictMode mounts twice; a callback state may only be consumed once.
  return (resuming ??= restoreIdentity());
}
async function restoreIdentity() {
  const auth = identityManager();
  let user;
  if (location.pathname === '/auth/callback') {
    user = await auth.signinCallback();
    if (!user) return null; // The SDK dispatches silent iframe callbacks to their parent.
    history.replaceState(null, '', '/');
  } else {
    user = await auth.getUser();
    if (!user) {
      try {
        user = await auth.signinSilent();
      } catch {
        return null;
      }
    }
  }
  if (!user || user.expired) return null;
  setToken(user.access_token);
  const response = await fetch('/api/local-cache/key', {
    headers: { Authorization: `Bearer ${user.access_token}` },
    cache: 'no-store',
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok)
    throw new Error('Your recovery storage could not be unlocked. Contact your administrator.');
  await unlockVault(await response.json());
  signedInSubject = user.profile.sub;
  return user.profile.name || user.profile.sub;
}
export async function signOut(onLocked: () => void) {
  // Encrypted unsent records remain for the rightful owner's next sign-in.
  await closeLocalSession();
  setToken('');
  lockVault();
  onLocked();
  await identityManager().signoutRedirect();
}
