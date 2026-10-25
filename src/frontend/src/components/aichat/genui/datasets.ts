import type { PresentationTrendV1 } from '@lib/types/AimmsWire.generated';

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const time = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^\d{4}-\d\d-\d\d(?:T.*)?$/.test(value) &&
  Number.isFinite(Date.parse(value));
/** Whole-snapshot validation: no coercion, zero-fill or silent truncation. */
export function normalizePresentationData(raw: unknown): PresentationTrendV1[] {
  try {
    if (
      !Array.isArray(raw) ||
      raw.length > 4 ||
      new TextEncoder().encode(JSON.stringify(raw)).length > 65536
    )
      return [];
    const seen = new Set<string>();
    for (const data of raw) {
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
    return structuredClone(raw) as PresentationTrendV1[];
  } catch {
    return [];
  }
}
