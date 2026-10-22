import React, { useEffect, useState } from 'react';
import { identityManager, resumeIdentity, signOut } from './auth';

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [name, setName] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [closing, setClosing] = useState(false);
  useEffect(() => {
    if (import.meta.env.DEV) return;
    let active = true;
    resumeIdentity()
      .then((value) => {
        if (active) setName(value);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : 'Sign-in failed.');
      })
      .finally(() => {
        if (active) setReady(true);
      });
    return () => {
      active = false;
    };
  }, []);
  if (import.meta.env.DEV) return <>{children}</>;
  if (!ready)
    return (
      <main>
        <p>Checking your company session…</p>
      </main>
    );
  if (!name)
    return (
      <main style={{ maxWidth: 480, margin: '10vh auto', padding: 24 }}>
        <h1>Meeting Intelligence</h1>
        <p>Sign in with your company account to access your meetings.</p>
        {error && <p role="alert">{error}</p>}
        <button
          onClick={async () => {
            try {
              await identityManager().signinRedirect();
            } catch {
              setError('Company sign-in is unavailable.');
            }
          }}
        >
          Sign in
        </button>
        <p>Saved recordings stay locked until you sign in online.</p>
      </main>
    );
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12, padding: 8 }}>
        <span>{name}</span>
        <button
          disabled={closing}
          onClick={async () => {
            setClosing(true);
            try {
              await signOut(() => setName(null));
            } catch {
              setError(
                'Sign-out could not finish. Retain this page and export unsaved audio, then retry.',
              );
              setClosing(false);
            }
          }}
        >
          {closing ? 'Saving recording…' : 'Sign out'}
        </button>
        {error && <span role="alert">{error}</span>}
      </div>
      {children}
    </>
  );
}
