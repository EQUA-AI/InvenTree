import { t } from '@lingui/core/macro';
import dayjs from 'dayjs';

/**
 * Formatting for the Performance tab. Numbers keep the precision the catalogue
 * asks for; times are as short as the window allows and never shorter than
 * unambiguous.
 */

/** The most decimals a value is ever printed with; past this the digits are noise. */
export const MAX_DECIMALS = 5;

/**
 * How many decimals a number needs so the reader sees what the chart shows.
 *
 * The catalogue's `decimals` is a floor, not a ceiling: two decimals suit a
 * discharge pressure of 12.4 mH2O but print a stopped pump's -0.0012 as "0",
 * and zero decimals suit 745 rpm but print a line wandering between 745.1 and
 * 745.4 as one unchanging "745". So precision is raised in two cases: a small
 * value keeps three significant figures, and a value from a series whose
 * whole range is narrower than the floor's resolution gets enough decimals to
 * tell its ends apart. Capped at five, where the digits stop meaning anything.
 */
export function precisionFor(
  base: number,
  range?: number | null,
  value?: number | null
): number {
  let needed = base;
  if (
    value !== null &&
    value !== undefined &&
    value !== 0 &&
    Math.abs(value) < 1
  ) {
    needed = Math.max(needed, Math.ceil(-Math.log10(Math.abs(value))) + 2);
  }
  if (
    range !== null &&
    range !== undefined &&
    range > 0 &&
    Number.isFinite(range)
  ) {
    needed = Math.max(needed, Math.ceil(-Math.log10(range)) + 1);
  }
  return Math.min(MAX_DECIMALS, Math.max(base, needed));
}

export function formatValue(
  value: number | null | undefined,
  decimals: number,
  unit?: string,
  range?: number | null
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '—';
  }
  const shown = precisionFor(decimals, range, value);
  // Round first so a reading of -0.0012 mH2O prints as 0, not as "-0".
  const factor = 10 ** shown;
  const rounded = Math.round(value * factor) / factor;
  const text = (rounded === 0 ? 0 : rounded).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: shown
  });
  return unit ? `${text} ${unit}` : text;
}

/** A clock time, with seconds only when the window is short enough to need them. */
export function formatTick(ms: number, windowSeconds: number): string {
  const moment = dayjs(ms);
  if (windowSeconds > 12 * 3600) {
    // Over half a day the date matters, and minutes are noise.
    return moment.format('D MMM HH:mm');
  }
  if (windowSeconds > 30 * 60) {
    return moment.format('HH:mm');
  }
  return moment.format('HH:mm:ss');
}

/** The full instant, for a tooltip or a caption. */
export function formatInstant(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return '—';
  }
  return dayjs(ms).format('D MMM YYYY HH:mm:ss');
}

export function formatClock(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return '—';
  }
  return dayjs(ms).format('HH:mm:ss');
}

/** "5 s", "6 min", "1.5 h" - a duration short enough for a badge. */
export function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return t`${Math.round(seconds)} s`;
  }
  if (seconds < 3600) {
    const minutes = seconds / 60;
    return t`${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} min`;
  }
  if (seconds < 48 * 3600) {
    const hours = seconds / 3600;
    return t`${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
  }
  return t`${Math.round(seconds / 86400)} d`;
}

/** How long ago, in words, for a caption. */
export function formatAge(ms: number | null | undefined, now: number): string {
  if (ms === null || ms === undefined) {
    return t`no reading`;
  }
  const seconds = Math.max(0, Math.round((now - ms) / 1000));
  if (seconds < 60) {
    return t`${seconds} s ago`;
  }
  if (seconds < 3600) {
    return t`${Math.round(seconds / 60)} min ago`;
  }
  if (seconds < 86400) {
    return t`${(seconds / 3600).toFixed(1)} h ago`;
  }
  return t`${Math.round(seconds / 86400)} d ago`;
}
