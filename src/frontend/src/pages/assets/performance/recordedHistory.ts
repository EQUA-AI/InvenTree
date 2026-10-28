import type { WindowSpec } from './usePerformanceData';

/** An explicit source is opt-in; malformed and unsafe IDs never reach the API. */
export function recordedHistorySource(
  value: string | null
): number | undefined {
  if (!value || !/^[1-9]\d*$/.test(value)) return undefined;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : undefined;
}

/** Default keys remain stable, and recorded sources cannot share current data. */
export function historySourceQueryKey(
  key: readonly unknown[],
  sourceId: number | undefined
) {
  return sourceId === undefined ? key : [...key, 'history-source', sourceId];
}

/** Historical presets follow the source clock, never today's browser clock. */
export function recordedHistoryWindow(
  window: WindowSpec,
  sourceTo: string | undefined
): WindowSpec | undefined {
  if (window.kind === 'absolute') return window;
  const to = sourceTo ? Date.parse(sourceTo) : Number.NaN;
  return Number.isFinite(to)
    ? { kind: 'absolute', from: to - window.seconds * 1000, to }
    : undefined;
}
