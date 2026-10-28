import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { monitorMicrophone } from './audioRoute';

let level = 0;
let listening = true;
let disconnect: ReturnType<typeof vi.fn>;
let close: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('window', globalThis);
  level = 0;
  listening = true;
  disconnect = vi.fn();
  close = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal(
    'AudioContext',
    class {
      createMediaStreamSource() {
        return { connect: vi.fn(), disconnect };
      }
      createAnalyser() {
        return {
          fftSize: 256,
          getFloatTimeDomainData(samples: Float32Array) {
            samples.fill(level);
          }
        };
      }
      close = close;
    }
  );
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('clears a silence warning after real input recovers and detects a later quiet interval', () => {
  const warn = vi.fn();
  const recovered = vi.fn();
  const stop = monitorMicrophone(
    {} as MediaStream,
    0.01,
    1500,
    () => listening,
    warn,
    recovered
  );
  vi.advanceTimersByTime(2500);
  expect(warn).toHaveBeenCalledTimes(1);
  level = 0.02;
  vi.advanceTimersByTime(300);
  expect(recovered).toHaveBeenCalledTimes(1);
  level = 0;
  vi.advanceTimersByTime(1600);
  expect(warn).toHaveBeenCalledTimes(2);
  stop();
});

it('resets the silence window while input monitoring is suspended for an answer', () => {
  const warn = vi.fn();
  const recovered = vi.fn();
  const stop = monitorMicrophone(
    {} as MediaStream,
    0.01,
    1500,
    () => listening,
    warn,
    recovered
  );
  vi.advanceTimersByTime(1600);
  expect(warn).toHaveBeenCalledTimes(1);
  listening = false;
  vi.advanceTimersByTime(30_000);
  expect(recovered).toHaveBeenCalledTimes(1);
  expect(warn).toHaveBeenCalledTimes(1);
  listening = true;
  vi.advanceTimersByTime(1500);
  expect(warn).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(100);
  expect(warn).toHaveBeenCalledTimes(2);
  stop();
});

it('releases the sampler and cannot report a stale warning after cleanup', () => {
  const warn = vi.fn();
  const stop = monitorMicrophone(
    {} as MediaStream,
    0.01,
    1500,
    () => listening,
    warn
  );
  vi.advanceTimersByTime(1000);
  stop();
  vi.advanceTimersByTime(10_000);
  expect(warn).not.toHaveBeenCalled();
  expect(disconnect).toHaveBeenCalledTimes(1);
  expect(close).toHaveBeenCalledTimes(1);
});
