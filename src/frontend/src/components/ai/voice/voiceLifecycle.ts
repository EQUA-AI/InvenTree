/** Clock-injected idle bounds, shared by browser lifecycle and unit tests. */
export class ForegroundBounds {
  private activity: number;
  private hiddenAt: number | null = null;
  constructor(
    private timeoutMs: number,
    private now = Date.now
  ) {
    this.activity = now();
  }
  touch() {
    if (this.hiddenAt === null) this.activity = this.now();
  }
  hide() {
    this.hiddenAt ??= this.now();
  }
  show() {
    const expired = this.expired;
    this.hiddenAt = null;
    return expired;
  }
  get expired() {
    return this.remainingMs <= 0;
  }
  get remainingMs() {
    return this.timeoutMs - (this.now() - (this.hiddenAt ?? this.activity));
  }
}
export function installVoiceLifecycle(callbacks: {
  hide: () => void;
  show: () => void;
  pagehide: () => void;
  offline: () => void;
  online: () => void;
  tick: () => void;
}) {
  const visibility = () =>
    document.hidden ? callbacks.hide() : callbacks.show();
  document.addEventListener('visibilitychange', visibility);
  window.addEventListener('pagehide', callbacks.pagehide);
  window.addEventListener('offline', callbacks.offline);
  window.addEventListener('online', callbacks.online);
  const timer = window.setInterval(callbacks.tick, 500);
  return () => {
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('pagehide', callbacks.pagehide);
    window.removeEventListener('offline', callbacks.offline);
    window.removeEventListener('online', callbacks.online);
    window.clearInterval(timer);
  };
}
