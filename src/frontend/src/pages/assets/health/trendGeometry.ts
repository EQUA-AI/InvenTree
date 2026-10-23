import type { SignalTrend, TrendSample } from '@lib/types/MachineHealth';
import { type PreparedTrend, isDrawn, prepareTrend } from './trendSamples';

/** Use the same source times, quality boundaries and cadence gaps as the full chart. */
export function trendGeometry(
  samples: TrendSample[],
  width = 120,
  height = 28,
  prepared?: PreparedTrend
) {
  const reading = prepared ?? prepareTrend({ samples } as SignalTrend);
  const usable = reading.rows.filter(isDrawn);
  if (!usable.length) return null;
  const min = Math.min(...usable.map((sample) => sample.value));
  const max = Math.max(...usable.map((sample) => sample.value));
  const points: { x: number; y: number }[] = [];
  let path = '';
  let connected = false;
  for (const sample of reading.rows) {
    if (!isDrawn(sample)) {
      connected = false;
      continue;
    }
    const x =
      ((sample.t - reading.start) / (reading.end - reading.start)) * width;
    const y =
      max === min
        ? height / 2
        : height - 2 - ((sample.value - min) / (max - min)) * (height - 4);
    points.push({ x, y });
    path += `${connected ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)} `;
    connected = true;
  }
  return {
    path: path.trim(),
    points,
    min,
    max,
    start: reading.start,
    end: reading.end,
    excluded: samples.length - usable.length,
    uncertain: samples.filter((sample) => sample.quality === 'uncertain')
      .length,
    bad: samples.filter((sample) => sample.quality === 'bad').length
  };
}
