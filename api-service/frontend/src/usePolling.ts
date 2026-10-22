import { useEffect, useRef } from 'react';

/** Schedule after completion so slow/offline requests never pile up. */
export function usePolling(callback: () => Promise<void>, enabled: boolean, interval = 3000) {
  const latest = useRef(callback);
  latest.current = callback;
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        if (!document.hidden && navigator.onLine) await latest.current();
      } catch {
        // The caller owns visible errors; polling must keep a single bounded timer.
      } finally {
        if (!stopped) timer = setTimeout(tick, interval);
      }
    };
    timer = setTimeout(tick, interval);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [enabled, interval]);
}
