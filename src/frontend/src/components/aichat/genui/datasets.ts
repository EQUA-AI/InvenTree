import type {
  PresentationChartV2,
  PresentationTrendV1
} from '@lib/types/AimmsWire.generated';
export type PresentationDataset = PresentationTrendV1 | PresentationChartV2;

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const time = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^\d{4}-\d\d-\d\d(?:T.*)?$/.test(value) &&
  Number.isFinite(Date.parse(value));
/** Whole-snapshot validation: no coercion, zero-fill or silent truncation. */
export function normalizePresentationData(raw: unknown): PresentationDataset[] {
  try {
    if (
      !Array.isArray(raw) ||
      raw.length > 4 ||
      new TextEncoder().encode(JSON.stringify(raw)).length > 65536
    )
      return [];
    const seen = new Set<string>();
    for (const data of raw) {
      if (record(data) && data.kind === 'chart_dataset') {
        if (!validChart(data) || seen.has(data.source_key as string)) return [];
        seen.add(data.source_key as string);
        continue;
      }
      if (
        !record(data) ||
        Object.keys(data).sort().join(',') !==
          'as_of,kind,samples,source,source_key,source_revision,timezone,unit' ||
        data.kind !== 'trend_dataset' ||
        !time(data.as_of)
      )
        return [];
      for (const [key, max] of [
        ['source_key', 256],
        ['unit', 32],
        ['source', 80],
        ['source_revision', 128],
        ['timezone', 80]
      ] as const) {
        if (
          typeof data[key] !== 'string' ||
          !data[key].trim() ||
          data[key].length > max
        )
          return [];
      }
      if (
        seen.has(data.source_key as string) ||
        !Array.isArray(data.samples) ||
        !data.samples.length ||
        data.samples.length > 512
      )
        return [];
      seen.add(data.source_key as string);
      let previous = Number.NEGATIVE_INFINITY;
      for (const sample of data.samples) {
        if (
          !record(sample) ||
          Object.keys(sample).sort().join(',') !==
            'observed_at,quality,value' ||
          !time(sample.observed_at) ||
          !['good', 'uncertain', 'bad', 'missing'].includes(
            String(sample.quality)
          ) ||
          (sample.value !== null &&
            (typeof sample.value !== 'number' ||
              !Number.isFinite(sample.value)))
        )
          return [];
        const current = Date.parse(sample.observed_at);
        if (current <= previous) return [];
        previous = current;
      }
    }
    return structuredClone(raw) as PresentationDataset[];
  } catch {
    return [];
  }
}

function validChart(data: Record<string, unknown>): boolean {
  const keys = [
    'kind',
    'source_key',
    'origin',
    'chart_type',
    'title',
    'unit',
    'x_label',
    'y_label',
    'source',
    'source_revision',
    'as_of',
    'scope_label',
    'definition',
    'population_count',
    'complete_population',
    'points'
  ];
  if (
    Object.keys(data).sort().join(',') !== keys.sort().join(',') ||
    !time(data.as_of)
  )
    return false;
  for (const [key, max] of Object.entries({
    source_key: 256,
    title: 120,
    unit: 32,
    x_label: 64,
    y_label: 64,
    source_revision: 128,
    scope_label: 512,
    definition: 512
  })) {
    const value = data[key];
    if (typeof value !== 'string' || !value.trim() || value.length > max)
      return false;
  }
  if (
    !['line', 'bar', 'pie'].includes(String(data.chart_type)) ||
    !Array.isArray(data.points) ||
    data.points.length < 1 ||
    data.points.length > 64
  )
    return false;
  const labels = new Set<string>();
  let sum = 0;
  for (const point of data.points) {
    if (
      !record(point) ||
      Object.keys(point).sort().join(',') !== 'label,value' ||
      typeof point.label !== 'string' ||
      !point.label.trim() ||
      point.label.length > 80 ||
      labels.has(point.label)
    )
      return false;
    labels.add(point.label);
    const value = point.value;
    if (
      value !== null &&
      (typeof value !== 'number' ||
        !Number.isFinite(value) ||
        Math.abs(value) > 1e12)
    )
      return false;
    if (
      data.origin === 'recorded' &&
      (!Number.isSafeInteger(value) || (value as number) < 0)
    )
      return false;
    if (data.chart_type === 'pie' && (value === null || (value as number) < 0))
      return false;
    sum += (value as number | null) ?? 0;
  }
  if (data.chart_type === 'pie' && sum <= 0) return false;
  return data.origin === 'recorded'
    ? data.source === 'work_orders' &&
        data.complete_population === true &&
        Number.isSafeInteger(data.population_count) &&
        (data.population_count as number) >= 0 &&
        sum === data.population_count
    : data.origin === 'user_supplied' &&
        data.source === 'user_supplied' &&
        data.population_count === null &&
        data.complete_population === null;
}
