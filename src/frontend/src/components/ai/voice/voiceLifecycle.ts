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
  const connection = (
    navigator as Navigator & { connection?: EventTarget & { type?: string } }
  ).connection;
  const interfaceType = () => {
    const type = connection?.type;
    return type &&
      [
        'bluetooth',
        'cellular',
        'ethernet',
        'mixed',
        'none',
        'other',
        'wifi',
        'wimax'
      ].includes(type)
      ? type
      : null;
  };
  let previousType = interfaceType();
  const connectionChanged = () => {
    const currentType = interfaceType();
    const changedInterface =
      previousType !== null &&
      currentType !== null &&
      previousType !== currentType;
    previousType = currentType;
    // NetworkInformation.change also reports routine RTT/downlink estimates
    // and effectiveType changes. They do not prove a disconnected transport.
    // When interface identity is unavailable, actual offline/peer/channel
    // events remain authoritative; never infer a handoff from estimated speed.
    if (!navigator.onLine || currentType === 'none' || changedInterface)
      callbacks.offline();
  };
  connection?.addEventListener('change', connectionChanged);
  const timer = window.setInterval(callbacks.tick, 500);
  return () => {
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('pagehide', callbacks.pagehide);
    window.removeEventListener('offline', callbacks.offline);
    window.removeEventListener('online', callbacks.online);
    connection?.removeEventListener('change', connectionChanged);
    window.clearInterval(timer);
  };
}
