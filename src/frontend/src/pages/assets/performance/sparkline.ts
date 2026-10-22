import type { SeriesEntry } from '@lib/types/MachineHealth';
import { usable } from './series';

/** The actual usable endpoints in the drawn interval; no interpolation or forecast. */
export function endpointDelta(
  entry: SeriesEntry,
  window?: { start: number; end: number }
): { change: number; percent: boolean; seconds: number } | null {
  if (!entry.available) return null;
  const samples = entry.samples
    .filter(
      (sample) =>
        usable(sample) &&
        Number.isFinite(sample.t) &&
        (!window || (sample.t >= window.start && sample.t <= window.end))
    )
    .sort((a, b) => a.t - b.t);
  const first = samples[0];
  const last = samples.at(-1);
  if (!first || !last || first.t >= last.t) return null;
  const difference = (last.v as number) - (first.v as number);
  return {
    change:
      first.v === 0
        ? difference
        : (difference / Math.abs(first.v as number)) * 100,
    percent: first.v !== 0,
    seconds: (last.t - first.t) / 1000
  };
}
/** Samples stay at their actual times; unreadable points and long gaps break the path. */
export function performanceSparkline(
  entry: SeriesEntry,
  window?: { start: number; end: number; resolutionSeconds?: number },
  width = 96,
  height = 24
): string | null {
  const samples = [...entry.samples]
    .filter(
      (s) =>
        Number.isFinite(s.t) &&
        (!window || (s.t >= window.start && s.t <= window.end))
    )
    .sort((a, b) => a.t - b.t);
  const values = samples.filter(usable).map((s) => s.v as number);
  if (values.length < 2) return null;
  const start = window?.start ?? samples[0].t;
  const end = window?.end ?? samples.at(-1)!.t;
  if (!(end > start)) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const intervals = samples
    .slice(1)
    .map((s, i) => s.t - samples[i].t)
    .filter((n) => n > 0)
    .sort((a, b) => a - b);
  const cadence =
    (entry.read_resolution_seconds ?? window?.resolutionSeconds) != null
      ? (entry.read_resolution_seconds ?? window!.resolutionSeconds!) * 1000
      : intervals[Math.floor(intervals.length / 2)];
  let previous: number | null = null;
  let path = '';
  for (const sample of samples) {
    if (!usable(sample)) {
      previous = null;
      continue;
    }
    const x = ((sample.t - start) / (end - start)) * width;
    const y =
      max === min
        ? height / 2
        : height -
          1 -
          (((sample.v as number) - min) / (max - min)) * (height - 2);
    const move =
      previous == null || (cadence > 0 && sample.t - previous > cadence * 3);
    path += `${move ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)} `;
    previous = sample.t;
  }
  return path.trim();
}
