import { createContext, useContext } from 'react';

export type DisplayClock = {
  zone: string;
  source: 'plant' | 'browser';
  label: string;
};
export function displayClock(supplied?: string | null): DisplayClock {
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone;
  let zone = browser;
  let invalid = false;
  let source: DisplayClock['source'] = 'browser';
  if (supplied) {
    try {
      new Intl.DateTimeFormat('en', { timeZone: supplied }).format();
      zone = supplied;
      source = 'plant';
    } catch {
      invalid = true;
      /* Invalid configuration is not a declared plant clock. */
    }
  }
  const offset =
    new Intl.DateTimeFormat('en', {
      timeZone: zone,
      timeZoneName: 'longOffset'
    })
      .formatToParts(new Date())
      .find((p) => p.type === 'timeZoneName')
      ?.value.replace('GMT', 'UTC') ?? '';
  return {
    zone,
    source,
    label:
      source === 'plant'
        ? `plant clock (${zone}, ${offset})`
        : `your clock (${zone}, ${offset}) · ${invalid ? 'plant timezone invalid' : 'plant timezone not supplied'}`
  };
}
export const DisplayClockContext = createContext<DisplayClock | null>(null);
export function useDisplayClock(supplied?: string | null): DisplayClock {
  const inherited = useContext(DisplayClockContext);
  return supplied !== undefined
    ? displayClock(supplied)
    : (inherited ?? displayClock());
}
/** One 24-hour date format for operational facts. Invalid input stays unavailable. */
export function formatInstant(
  value: string | number | null | undefined,
  clock: DisplayClock,
  opts: { timeOnly?: boolean } = {}
): string | null {
  if (value === null || value === undefined || value === '') return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: clock.zone,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23'
  }).formatToParts(date);
  const part = (type: string) => parts.find((p) => p.type === type)?.value;
  const time = `${part('hour')}:${part('minute')}:${part('second')}`;
  return opts.timeOnly
    ? time
    : `${part('day')} ${part('month')} ${part('year')} ${time}`;
}
/** Compatibility for callers outside the redesigned panels. */
export function observedTime(value: string, supplied?: string): string | null {
  return formatInstant(value, displayClock(supplied));
}
