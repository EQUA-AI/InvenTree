import dayjs from 'dayjs';
import { machinePanelHref } from '../locations/locationTree';

/** Mantine's local picker value is not an ECMAScript ISO date string. */
export function pickerInstant(value: string | null): number {
  return value ? dayjs(value).valueOf() : Number.NaN;
}

/** Existing single-window series contract. A case span is navigated in explicit
 * slices, never silently expanded into an unbounded source read. */
export const MAX_HISTORY_WINDOW_MS = 24 * 3600_000;
export interface InvestigationWindow {
  from: number;
  to: number;
}
export function validSpan(from: number, to: number) {
  return (
    Number.isFinite(from) &&
    Number.isFinite(to) &&
    Math.abs(from) <= 8.64e15 &&
    Math.abs(to) <= 8.64e15 &&
    to > from
  );
}
export function boundedWindow(
  from: number,
  to: number
): InvestigationWindow | null {
  return validSpan(from, to) && to - from <= MAX_HISTORY_WINDOW_MS
    ? { from, to }
    : null;
}
export function sliceCount(
  span: InvestigationWindow,
  limitMs = MAX_HISTORY_WINDOW_MS
) {
  const limit = Math.min(limitMs, MAX_HISTORY_WINDOW_MS);
  return validSpan(span.from, span.to) && Number.isFinite(limit) && limit > 0
    ? Math.ceil((span.to - span.from) / limit)
    : 0;
}
export function sliceAt(
  span: InvestigationWindow,
  index: number,
  limitMs = MAX_HISTORY_WINDOW_MS
): InvestigationWindow | null {
  const limit = Math.min(limitMs, MAX_HISTORY_WINDOW_MS);
  const count = sliceCount(span, limit);
  if (!Number.isSafeInteger(index) || index < 0 || index >= count) return null;
  const from = span.from + index * limit;
  return { from, to: Math.min(span.to, from + limit) };
}
export function linkedWindow(params: URLSearchParams): {
  present: boolean;
  window: InvestigationWindow | null;
} {
  const present = params.has('history_from') || params.has('history_to');
  const from = params.get('history_from');
  const to = params.get('history_to');
  return {
    present,
    window: from && to ? boundedWindow(Date.parse(from), Date.parse(to)) : null
  };
}
export function investigationHref(
  machineId: number,
  span: InvestigationWindow,
  occurrence: number,
  snapshot?: string,
  clock: 'recorded' | 'display' = 'display',
  origin = new URLSearchParams()
) {
  const params = new URLSearchParams({
    history_from: new Date(span.from).toISOString(),
    history_to: new Date(span.to).toISOString(),
    occurrence: String(occurrence)
  });
  params.set('history_clock', clock);
  if (snapshot) params.set('snapshot', snapshot);
  return machinePanelHref(
    machineId,
    'performance',
    origin,
    Object.fromEntries(params)
  );
}

/** Series reads accept display-clock bounds. Never guess an unknown offset. */
export function displayLinkedWindow(params: URLSearchParams, shift?: number) {
  const linked = linkedWindow(params);
  const clock = params.get('history_clock') ?? 'display';
  const clockUnknown =
    linked.present &&
    !!linked.window &&
    clock === 'recorded' &&
    !Number.isFinite(shift);
  if (!linked.window || !linked.present)
    return { ...linked, clockUnknown: false };
  if (clockUnknown || !['recorded', 'display'].includes(clock))
    return { ...linked, window: null, clockUnknown };
  const delta = clock === 'recorded' ? shift! * 1000 : 0;
  return {
    ...linked,
    window: boundedWindow(linked.window.from + delta, linked.window.to + delta),
    clockUnknown: false
  };
}
