const drains = new Set<() => Promise<void>>();
let closing = false;
let pending: Promise<void> | undefined;
export function sessionClosing() {
  return closing;
}
export function registerSessionDrain(drain: () => Promise<void>) {
  drains.add(drain);
  return () => {
    drains.delete(drain);
  };
}
export function closeLocalSession() {
  if (pending) return pending;
  closing = true;
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new Error('Local recovery save timed out. Keep this page and export audio.')),
      15000,
    );
  });
  pending = Promise.race([Promise.all([...drains].map((drain) => drain())), timeout])
    .then(() => {})
    .catch((error) => {
      pending = undefined;
      closing = false;
      throw error;
    })
    .finally(() => clearTimeout(timer));
  return pending;
}
export async function finishRecorder(recorder: MediaRecorder | null) {
  if (!recorder || recorder.state === 'inactive') return;
  await new Promise<void>((resolve, reject) => {
    const finish = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      recorder.removeEventListener('stop', finish);
      reject(new Error('Recording could not finish. Keep this page open and export your audio.'));
    }, 10000);
    recorder.addEventListener('stop', finish, { once: true });
    try {
      recorder.stop();
    } catch (error) {
      clearTimeout(timer);
      reject(error);
    }
  });
}
