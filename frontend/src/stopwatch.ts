/** Recording time is independent of upload intervals and the wall-clock audio timeline. */
export class Stopwatch {
  private accumulated = 0;
  private runningSince: number | null = null;

  private now: () => number;

  constructor(now: () => number = () => performance.now()) {
    this.now = now;
  }

  read() {
    return this.accumulated + (this.runningSince === null ? 0 : this.now() - this.runningSince);
  }

  resume() {
    if (this.runningSince === null) this.runningSince = this.now();
  }

  pause() {
    this.accumulated = this.read();
    this.runningSince = null;
  }

  reset() {
    this.accumulated = 0;
    this.runningSince = null;
  }
}

export function formatElapsed(milliseconds: number) {
  const seconds = Math.floor(Math.max(0, milliseconds) / 1000);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map((value) => String(value).padStart(2, '0'))
    .join(':');
}
