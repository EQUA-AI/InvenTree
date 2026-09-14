import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { installVoiceLifecycle } from './voiceLifecycle';

let connection: EventTarget & {
  type?: string;
  effectiveType: string;
  rtt: number;
  downlink: number;
  saveData: boolean;
};
let offline: ReturnType<typeof vi.fn>;
let online: ReturnType<typeof vi.fn>;
let cleanup: () => void;
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal(
    'window',
    Object.assign(new EventTarget(), { setInterval, clearInterval })
  );
  vi.stubGlobal(
    'document',
    Object.assign(new EventTarget(), { hidden: false })
  );
  connection = Object.assign(new EventTarget(), {
    type: 'wifi',
    effectiveType: '4g',
    rtt: 50,
    downlink: 10,
    saveData: false
  });
  vi.stubGlobal('navigator', { connection, onLine: true });
  offline = vi.fn();
  online = vi.fn();
  cleanup = installVoiceLifecycle({
    hide: vi.fn(),
    show: vi.fn(),
    pagehide: vi.fn(),
    tick: vi.fn(),
    offline,
    online
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('does not treat bandwidth, RTT, effective speed or data-saver changes as offline', () => {
  for (const change of [
    { rtt: 150 },
    { downlink: 0.8 },
    { effectiveType: '3g' },
    { saveData: true }
  ]) {
    Object.assign(connection, change);
    connection.dispatchEvent(new Event('change'));
  }
  expect(offline).not.toHaveBeenCalled();
});
it('still pauses on an observed interface change, but not repeated estimates', () => {
  connection.type = 'cellular';
  connection.dispatchEvent(new Event('change'));
  connection.rtt = 300;
  connection.dispatchEvent(new Event('change'));
  expect(offline).toHaveBeenCalledTimes(1);
});
it('uses actual offline/online events when interface type is unavailable', () => {
  delete connection.type;
  connection.dispatchEvent(new Event('change'));
  connection.effectiveType = '2g';
  connection.dispatchEvent(new Event('change'));
  expect(offline).not.toHaveBeenCalled();
  window.dispatchEvent(new Event('offline'));
  window.dispatchEvent(new Event('online'));
  expect(offline).toHaveBeenCalledTimes(1);
  expect(online).toHaveBeenCalledTimes(1);
});
it('retains the offline safety signal and removes every listener', () => {
  Object.defineProperty(navigator, 'onLine', { value: false });
  connection.dispatchEvent(new Event('change'));
  expect(offline).toHaveBeenCalledTimes(1);
  cleanup();
  connection.type = 'cellular';
  connection.dispatchEvent(new Event('change'));
  window.dispatchEvent(new Event('offline'));
  expect(offline).toHaveBeenCalledTimes(1);
});
