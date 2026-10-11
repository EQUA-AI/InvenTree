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
  return formatAt(value, precisionFor(decimals, range, value), unit);
}

/** A number at exactly this many decimals at most - no adaptive raising. */
export function formatAt(
  value: number,
  decimals: number,
  unit?: string
): string {
  // Round first so a reading of -0.0012 mH2O prints as 0, not as "-0".
  const factor = 10 ** decimals;
  const rounded = Math.round(value * factor) / factor;
  const text = (rounded === 0 ? 0 : rounded).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals
  });
  return unit ? `${text} ${unit}` : text;
}

/**
 * "a – b unit" for a window's extremes, both ends at the same precision so
 * they can be compared digit for digit. A negative end is joined with "to"
 * instead of a dash, because "-0.0098–0.021" reads as three numbers.
 */
export function formatRange(
  min: number,
  max: number,
  decimals: number,
  unit?: string
): string {
  const range = max - min;
  const shown = Math.max(
    precisionFor(decimals, range, min),
    precisionFor(decimals, range, max)
  );
  const low = formatAt(min, shown);
  const high = formatAt(max, shown, unit);
  if (low === formatAt(max, shown)) {
    return high;
  }
  const joiner = min < 0 || max < 0 ? ` ${t`to`} ` : ' – ';
  return `${low}${joiner}${high}`;
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

/**
 * A clock time that says the day when it is not the reference's day.
 *
 * A reading's own instant is not always today: a custom window over yesterday
 * printed "newest reading 23:49:00" beside "Updated 22:10:40", which reads as
 * a reading from the future. The date is added only when it differs, because
 * the full instant is noise for the common same-day case.
 */
export function formatClockOn(
  ms: number | null | undefined,
  reference: number
): string {
  if (ms === null || ms === undefined) {
    return '—';
  }
  const moment = dayjs(ms);
  return moment.isSame(dayjs(reference), 'day')
    ? moment.format('HH:mm:ss')
    : moment.format('D MMM HH:mm:ss');
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

/**
 * The ticks Recharts will choose for a numeric axis over `[min, max]`.
 *
 * An axis is as wide as the widest label it prints, and the labels are the
 * ticks - not the data. Measured from the data instead, a width fitting "0.03"
 * wrapped every "-0.5 MW" onto two lines, because Recharts rounds the extremes
 * outwards and appends the unit. Its rule: a step of about a quarter of the
 * span, rounded up to the next 1, 2, 2.5 or 5 times a power of ten, from the
 * multiple below the minimum to the multiple above the maximum.
 */
export function niceTicks(min: number, max: number, count = 4): number[] {
  const raw = (max - min) / count;
  if (!(raw > 0)) return [];
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step =
    magnitude * ([1, 2, 2.5, 5, 10].find((m) => raw <= m * magnitude) ?? 10);
  // Multiples of the step, rounded to the step's own precision: accumulated
  // in floating point, a step of 0.05 prints its third tick as
  // 0.15000000000000002.
  const places = Math.max(0, Math.ceil(-Math.log10(step)) + 1);
  const ticks: number[] = [];
  for (let i = Math.floor(min / step); i <= Math.ceil(max / step); i++) {
    ticks.push(Number((i * step).toFixed(places)));
  }
  return ticks;
}

/** Candidate tick spacings, in minutes; the first that fits `target` ticks wins. */
const TICK_STEPS_MINUTES = [
  1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 720, 1440
];

/**
 * The instants every chart of a window prints on its time axis.
 *
 * Recharts picks ticks per chart from its own width, so two charts side by
 * side print different times and the shared crosshair looks unshared. One
 * tick list from the window - round local-clock multiples of a step that
 * gives about `target` ticks - keeps every axis of the same width identical.
 * The caller sizes `target` to its plot, because a label carrying a date
 * needs roughly twice the room of one carrying a clock time.
 */
export function timeTicks(start: number, end: number, target = 6): number[] {
  const span = end - start;
  if (!(span > 0)) return [];
  const step =
    TICK_STEPS_MINUTES.map((m) => m * 60_000).find((s) => span / s <= target) ??
    TICK_STEPS_MINUTES[TICK_STEPS_MINUTES.length - 1] * 60_000;
  // Align to the local clock, so a step of an hour lands on :00 and not on
  // some UTC offset's :30.
  const offset = new Date(start).getTimezoneOffset() * 60_000;
  const first = Math.ceil((start - offset) / step) * step + offset;
  const ticks: number[] = [];
  for (let at = first; at <= end; at += step) {
    ticks.push(at);
  }
  return ticks;
}
