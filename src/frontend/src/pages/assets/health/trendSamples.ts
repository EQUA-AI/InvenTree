import type { SignalTrend, TrendSample } from '@lib/types/MachineHealth';

import { gapThresholdMs } from '../performance/series';

/**
 * The one reading of a Health trend that both the 120 x 28 row graph and the
 * full chart draw from (R6 A05.3, A05.9, A23.2).
 *
 * Every sample keeps its own source time, and time is what places it: two
 * readings a minute apart sit a minute apart on the axis, however many
 * samples the source sent. Nothing is dropped before plotting. A reading
 * that cannot be drawn - not a number, or a number the source itself marked
 * as not good - stays in the record as a null at its time, so the line
 * breaks there. Where the source was silent for well over its cadence, a
 * display-only break is inserted so the line does not join across the
 * silence. Breaks are never counted, shown or kept as samples.
 *
 * Pure: no React, no i18n, so the row graph, the chart and the tests share it.
 */

/** Why a sample that was measured is not drawn. */
export type UndrawnReason = 'not_numeric' | 'quality';

/** One reading the source returned, at its own time. */
export interface MeasuredPoint {
  t: number;
  /** The number drawn; null where the reading cannot be drawn. */
  value: number | null;
  measured: true;
  quality: TrendSample['quality'];
  raw: TrendSample['value'];
  undrawn?: UndrawnReason;
}

/**
 * A display-only break where the source said nothing for longer than its
 * cadence allows. Not a sample: no value, no quality, never evidence.
 */
export interface SilenceBreak {
  t: number;
  value: null;
  measured: false;
  /** The readings either side of the silence. */
  from: number;
  to: number;
}

export type TrendRow = MeasuredPoint | SilenceBreak;

export interface PreparedTrend {
  /** Measured points and silence breaks, in time order. */
  rows: TrendRow[];
  /** Samples placed on the axis (every one with a readable time). */
  measured: number;
  /** Samples drawn as values. */
  drawn: number;
  /** Measured samples that are not numbers. */
  notNumeric: number;
  /** Numeric samples the source marked bad, uncertain or unknown. */
  notGood: number;
  /** Samples whose time could not be read, so they cannot be placed. */
  undated: number;
  /** Silences marked as breaks. */
  silences: number;
  /**
   * The spacing beyond which a silence is a break, from the measured interval or server default; null when the server stated none, and then only unreadable
   * samples break the line.
   */
  gapMs: number | null;
  /** The window the read covered, epoch milliseconds. */
  start: number;
  end: number;
}

/**
 * What to draw for one sample, or null when it cannot be drawn.
 *
 * `plot_value` is the server's number for a reading that is not one itself -
 * a status code - mapped from the plant's own vocabulary. Anything else
 * non-numeric is left undrawn rather than coerced.
 */
export function numericValue(sample: {
  value: unknown;
  plot_value?: number | null;
}): number | null {
  if (typeof sample.plot_value === 'number') {
    return sample.plot_value;
  }
  const { value } = sample;
  if (value === null || value === undefined || typeof value === 'boolean') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * The cadence the server states for this read, in seconds: measured from the
 * readings when there were enough, else the server's expected interval (a global default, not a source fact).
 */
export function trendResolutionSeconds(
  trend: Pick<SignalTrend, 'cadence_seconds' | 'limits'>
): number | null {
  const measured = trend.cadence_seconds;
  if (typeof measured === 'number' && measured > 0) {
    return measured;
  }
  const expected = trend.limits?.expected_sample_interval_seconds;
  return typeof expected === 'number' && expected > 0 ? expected : null;
}

function epoch(iso: string | undefined | null): number | null {
  if (!iso) {
    return null;
  }
  const at = Date.parse(iso);
  return Number.isFinite(at) ? at : null;
}

/** Prepare one trend for drawing, keeping every reading at its time. */
export function prepareTrend(
  trend: SignalTrend | null | undefined
): PreparedTrend {
  const resolution = trend ? trendResolutionSeconds(trend) : null;
  // The same threshold the Performance charts apply to a complete read at
  // the source's cadence: comfortably more than one missed reading.
  const gapMs = resolution === null ? null : gapThresholdMs(resolution);

  const points: MeasuredPoint[] = [];
  let undated = 0;
  let notNumeric = 0;
  let notGood = 0;
  for (const sample of trend?.samples ?? []) {
    const t = epoch(sample.observed_at);
    if (t === null) {
      undated += 1;
      continue;
    }
    const value = numericValue(sample);
    let undrawn: UndrawnReason | undefined;
    if (value === null) {
      undrawn = 'not_numeric';
      notNumeric += 1;
    } else if (sample.quality !== 'good') {
      // Unusable is as disqualifying as absent, as on the Performance tab
      // and in the current-value rows: a pegged reading is not a value.
      undrawn = 'quality';
      notGood += 1;
    }
    points.push({
      t,
      value: undrawn ? null : value,
      measured: true,
      quality: sample.quality,
      raw: sample.value,
      ...(undrawn ? { undrawn } : {})
    });
  }
  // Time order, whatever order the source sent: the axis is time.
  points.sort((a, b) => a.t - b.t);

  const rows: TrendRow[] = [];
  let silences = 0;
  for (let index = 0; index < points.length; index++) {
    const point = points[index];
    const previous = points[index - 1];
    if (previous && gapMs !== null && point.t - previous.t > gapMs) {
      rows.push({
        t: previous.t + 1,
        value: null,
        measured: false,
        from: previous.t,
        to: point.t
      });
      silences += 1;
    }
    rows.push(point);
  }

  const first = points[0]?.t ?? null;
  const last = points[points.length - 1]?.t ?? null;
  let start = epoch(trend?.window_start) ?? first ?? 0;
  let end = epoch(trend?.window_end) ?? last ?? 0;
  // A window that does not contain its own readings would push them off the
  // axis; widen it to them rather than hide a reading.
  if (first !== null && first < start) start = first;
  if (last !== null && last > end) end = last;
  if (!(end > start)) end = start + 1;

  return {
    rows,
    measured: points.length,
    drawn: points.length - notNumeric - notGood,
    notNumeric,
    notGood,
    undated,
    silences,
    gapMs,
    start,
    end
  };
}

/** Whether a row is a drawn value. */
export function isDrawn(
  row: TrendRow
): row is MeasuredPoint & { value: number } {
  return row.measured && row.value !== null;
}

/**
 * The row graph's path: x by source time across the window, a new segment
 * after every undrawn reading or silence, and a reading alone between two
 * breaks drawn as a dot (a zero-length segment the round cap renders) so it
 * is not lost. Null when fewer than two readings can be drawn.
 */
export function sparklinePath(
  prepared: PreparedTrend,
  width: number,
  height: number
): string | null {
  const values = prepared.rows.filter(isDrawn).map((row) => row.value);
  if (values.length < 2) {
    return null;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const window = prepared.end - prepared.start;
  const x = (t: number) => ((t - prepared.start) / window) * width;
  const y = (value: number) => height - ((value - min) / span) * height;

  const segments: string[][] = [];
  let current: string[] | null = null;
  for (const row of prepared.rows) {
    if (!isDrawn(row)) {
      current = null;
      continue;
    }
    const point = `${x(row.t).toFixed(1)},${y(row.value).toFixed(1)}`;
    if (current === null) {
      current = [point];
      segments.push(current);
    } else {
      current.push(point);
    }
  }
  return segments
    .map((segment) =>
      segment.length === 1
        ? `M${segment[0]} L${segment[0]}`
        : segment
            .map((point, index) => `${index === 0 ? 'M' : 'L'}${point}`)
            .join(' ')
    )
    .join(' ');
}

/**
 * Indices of drawn readings with no drawn neighbour, which an area chart
 * would otherwise leave invisible between two breaks.
 */
export function isolatedIndices(rows: readonly TrendRow[]): Set<number> {
  const isolated = new Set<number>();
  rows.forEach((row, index) => {
    if (!isDrawn(row)) return;
    const before = rows[index - 1];
    const after = rows[index + 1];
    if ((!before || !isDrawn(before)) && (!after || !isDrawn(after))) {
      isolated.add(index);
    }
  });
  return isolated;
}
