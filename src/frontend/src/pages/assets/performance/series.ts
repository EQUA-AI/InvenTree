import type {
  MachineSignalLimits,
  SeriesEntry,
  SeriesResponse,
  SeriesSample
} from '@lib/types/MachineHealth';

/**
 * The transformation layer between the series API and the charts.
 *
 * Everything here is a pure function of the response. Components call these
 * inside `useMemo` and never reshape samples themselves, so the rules below
 * hold everywhere on the page:
 *
 * - **A gap stays a gap.** Rows are aligned on the sample instant, and a
 *   missing sample is `null`, which the charts draw as a break in the line.
 *   Nothing is carried forward or interpolated.
 * - **Zero is a value; null is no value.** A pump that reads 0 MW is stopped;
 *   a pump with no reading is unknown. The two are never conflated.
 * - **Derived numbers say they are derived.** A ΔT or a spread is computed
 *   only where both inputs exist at the same instant, and is labelled as
 *   calculated wherever it is shown.
 */

export interface SeriesStats {
  min: number;
  max: number;
  mean: number;
  first: number;
  last: number;
  lastAt: number;
  minAt: number;
  maxAt: number;
  count: number;
}

export type Trend = 'up' | 'down' | 'flat';

/** One row of a multi-series chart: the instant plus one value per series. */
export type ChartRow = { t: number } & Record<string, number | null>;

export function indexSeries(
  response: SeriesResponse | undefined
): Map<number, SeriesEntry> {
  const map = new Map<number, SeriesEntry>();
  for (const entry of response?.series ?? []) {
    map.set(entry.binding_id, entry);
  }
  return map;
}

/**
 * Whether a sample is a value the page may use.
 *
 * Only a good-quality number is. A pegged over-range marker arrives as a
 * number with quality `bad`; drawing it would put 3276.7 degC on a winding
 * chart and make it the "highest sensor". The same rule the Health blade and
 * the mimic apply: unusable is as disqualifying as absent, so it is a gap.
 */
export function usable(sample: SeriesSample): boolean {
  return sample.v !== null && Number.isFinite(sample.v) && sample.q === 'good';
}

function numeric(samples: readonly SeriesSample[]): SeriesSample[] {
  return samples.filter(usable);
}

export function seriesStats(
  entry: SeriesEntry | undefined | null
): SeriesStats | null {
  if (!entry?.available) {
    return null;
  }
  const points = numeric(entry.samples);
  if (points.length === 0) {
    return null;
  }
  let min = points[0];
  let max = points[0];
  let sum = 0;
  for (const point of points) {
    const v = point.v as number;
    sum += v;
    if (v < (min.v as number)) min = point;
    if (v > (max.v as number)) max = point;
  }
  const last = points[points.length - 1];
  return {
    min: min.v as number,
    max: max.v as number,
    mean: sum / points.length,
    first: points[0].v as number,
    last: last.v as number,
    lastAt: last.t,
    minAt: min.t,
    maxAt: max.t,
    count: points.length
  };
}

/**
 * Whether a series moved over the window, judged by thirds.
 *
 * The mean of the last third against the mean of the first third, relative to
 * the series' own range: a change smaller than a tenth of the range is noise
 * on a line this coarse, and a series with no range at all is flat by
 * definition. Not a slope, and not a forecast - a direction.
 */
export function trendOf(entry: SeriesEntry | undefined | null): Trend | null {
  if (!entry?.available) {
    return null;
  }
  const points = numeric(entry.samples);
  if (points.length < 6) {
    return null;
  }
  const third = Math.floor(points.length / 3);
  const mean = (slice: SeriesSample[]) =>
    slice.reduce((acc, s) => acc + (s.v as number), 0) / slice.length;
  const early = mean(points.slice(0, third));
  const late = mean(points.slice(points.length - third));
  const values = points.map((s) => s.v as number);
  const range = Math.max(...values) - Math.min(...values);
  if (range === 0) {
    return 'flat';
  }
  const change = (late - early) / range;
  if (change > 0.1) return 'up';
  if (change < -0.1) return 'down';
  return 'flat';
}

/**
 * Align several series into chart rows on their shared instants.
 *
 * Every key in one snapshot carries the same instant, so series from one read
 * line up exactly and no resampling is needed. Where a series has no sample at
 * an instant another has, its cell is `null`. A gap wider than `gapMs` between
 * two consecutive instants gets an explicit all-null row in between, so a line
 * chart with `connectNulls` off breaks there instead of drawing across it.
 */
export function alignRows(
  entries: readonly { key: string; entry: SeriesEntry | undefined }[],
  gapMs: number
): ChartRow[] {
  const byInstant = new Map<number, ChartRow>();
  const keys = entries.map((e) => e.key);
  for (const { key, entry } of entries) {
    if (!entry?.available) continue;
    for (const sample of entry.samples) {
      let row = byInstant.get(sample.t);
      if (!row) {
        row = { t: sample.t } as ChartRow;
        for (const k of keys) row[k] = null;
        byInstant.set(sample.t, row);
      }
      row[key] = usable(sample) ? sample.v : null;
    }
  }
  const rows = [...byInstant.values()].sort((a, b) => a.t - b.t);
  if (gapMs <= 0) {
    return rows;
  }
  const withBreaks: ChartRow[] = [];
  for (let i = 0; i < rows.length; i++) {
    if (i > 0 && rows[i].t - rows[i - 1].t > gapMs) {
      const filler = { t: rows[i - 1].t + 1 } as ChartRow;
      for (const k of keys) filler[k] = null;
      withBreaks.push(filler);
    }
    withBreaks.push(rows[i]);
  }
  return withBreaks;
}

/** The break threshold for a read: comfortably more than one missed slot. */
export function gapThresholdMs(resolutionSeconds: number): number {
  return Math.max(resolutionSeconds * 2.5, 15) * 1000;
}

/**
 * `a − b` wherever both exist at the same instant. A calculated series: the
 * caller labels it so.
 */
export function differenceSeries(
  a: SeriesEntry | undefined,
  b: SeriesEntry | undefined
): SeriesSample[] {
  if (!a?.available || !b?.available) {
    return [];
  }
  const bAt = new Map<number, number>();
  for (const s of b.samples) {
    if (usable(s)) bAt.set(s.t, s.v as number);
  }
  const out: SeriesSample[] = [];
  for (const s of a.samples) {
    const other = bAt.get(s.t);
    if (usable(s) && other !== undefined) {
      out.push({ t: s.t, v: (s.v as number) - other, q: s.q });
    }
  }
  return out;
}

/** Wrap a computed sample list as an entry-like object for the helpers above. */
export function syntheticEntry(
  label: string,
  unit: string,
  samples: SeriesSample[]
): SeriesEntry {
  return {
    binding_id: -1,
    machine_id: -1,
    external_key: '',
    display_name: label,
    unit,
    signal_kind: 'derived',
    source_id: -1,
    source_name: '',
    limits: {
      normal_min: null,
      normal_max: null,
      warn_min: null,
      warn_max: null,
      critical_min: null,
      critical_max: null
    },
    available: true,
    count: samples.length,
    samples
  };
}

export interface RelationPoint {
  x: number;
  y: number;
  t: number;
}

/** Pairs of `(x, y)` at shared instants, oldest first, for a relationship view. */
export function relationPoints(
  x: SeriesEntry | undefined,
  y: SeriesEntry | undefined
): RelationPoint[] {
  if (!x?.available || !y?.available) {
    return [];
  }
  const yAt = new Map<number, number>();
  for (const s of y.samples) {
    if (usable(s)) yAt.set(s.t, s.v as number);
  }
  const out: RelationPoint[] = [];
  for (const s of x.samples) {
    const other = yAt.get(s.t);
    if (usable(s) && other !== undefined) {
      out.push({ x: s.v as number, y: other, t: s.t });
    }
  }
  return out;
}

export interface HeatmapColumn {
  start: number;
  end: number;
}

export interface HeatmapRow {
  key: string;
  label: string;
  /** Mean of the readings that fell in each column; null where there were none. */
  cells: (number | null)[];
  /** How many readings each cell's mean rests on. */
  counts: number[];
  last: number | null;
  limits: MachineSignalLimits | null;
}

export interface HeatmapMatrix {
  columns: HeatmapColumn[];
  rows: HeatmapRow[];
  min: number;
  max: number;
}

/**
 * Sensors × time. Each cell is the mean of the real readings that fell inside
 * its column - stated as such in the tooltip - and a column with none is
 * empty. The colour range is the observed range across the whole matrix, so
 * one scale reads across every sensor.
 */
export function heatmapMatrix(
  entries: readonly {
    key: string;
    label: string;
    entry: SeriesEntry | undefined;
  }[],
  windowStart: number,
  windowEnd: number,
  columnCount: number
): HeatmapMatrix | null {
  const span = windowEnd - windowStart;
  if (span <= 0 || columnCount < 1) {
    return null;
  }
  const width = span / columnCount;
  const columns: HeatmapColumn[] = Array.from(
    { length: columnCount },
    (_, i) => ({
      start: windowStart + i * width,
      end: i === columnCount - 1 ? windowEnd : windowStart + (i + 1) * width
    })
  );

  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  const rows: HeatmapRow[] = [];

  for (const { key, label, entry } of entries) {
    if (!entry?.available) continue;
    const sums = new Array<number>(columnCount).fill(0);
    const counts = new Array<number>(columnCount).fill(0);
    let last: number | null = null;
    for (const s of entry.samples) {
      if (!usable(s)) continue;
      const i = Math.min(
        columnCount - 1,
        Math.max(0, Math.floor((s.t - windowStart) / width))
      );
      sums[i] += s.v as number;
      counts[i] += 1;
      last = s.v as number;
    }
    const cells = sums.map((sum, i) => {
      if (counts[i] === 0) return null;
      const mean = sum / counts[i];
      if (mean < min) min = mean;
      if (mean > max) max = mean;
      return mean;
    });
    rows.push({ key, label, cells, counts, last, limits: entry.limits });
  }

  if (rows.length === 0 || !Number.isFinite(min)) {
    return null;
  }
  return { columns, rows, min, max };
}

/**
 * The instant of the newest reading across several series, or null.
 * "Last updated" for a page is the newest thing on it.
 */
export function newestInstant(
  entries: readonly (SeriesEntry | undefined)[]
): number | null {
  let newest: number | null = null;
  for (const entry of entries) {
    if (!entry?.available) continue;
    const last = entry.samples[entry.samples.length - 1];
    if (last && (newest === null || last.t > newest)) {
      newest = last.t;
    }
  }
  return newest;
}

/** Whether any configured limit is crossed by `value`. */
export function limitState(
  value: number | null,
  limits: MachineSignalLimits | null | undefined
): 'critical' | 'warning' | 'normal' | 'unconfigured' {
  if (value === null || !limits) {
    return 'unconfigured';
  }
  const configured =
    limits.critical_min !== null ||
    limits.critical_max !== null ||
    limits.warn_min !== null ||
    limits.warn_max !== null ||
    limits.normal_min !== null ||
    limits.normal_max !== null;
  if (!configured) {
    return 'unconfigured';
  }
  if (
    (limits.critical_max !== null && value >= limits.critical_max) ||
    (limits.critical_min !== null && value <= limits.critical_min)
  ) {
    return 'critical';
  }
  if (
    (limits.warn_max !== null && value >= limits.warn_max) ||
    (limits.warn_min !== null && value <= limits.warn_min) ||
    (limits.normal_max !== null && value > limits.normal_max) ||
    (limits.normal_min !== null && value < limits.normal_min)
  ) {
    return 'warning';
  }
  return 'normal';
}
