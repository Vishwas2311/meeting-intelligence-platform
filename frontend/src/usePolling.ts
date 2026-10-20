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
        await latest.current();
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
