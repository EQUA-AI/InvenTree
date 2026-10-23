import type { TrendSample } from '@lib/types/MachineHealth';

/** Keep timestamps, missing observations and quality boundaries in the plot. */
export function trendGeometry(
  samples: TrendSample[],
  width = 120,
  height = 28
) {
  const ordered = samples
    .map((sample) => ({
      ...sample,
      time: Date.parse(sample.observed_at),
      numeric:
        typeof sample.value === 'number' ||
        (typeof sample.value === 'string' && sample.value.trim() !== '')
          ? Number(sample.value)
          : Number.NaN
    }))
    .filter((sample) => Number.isFinite(sample.time))
    .sort((a, b) => a.time - b.time);
  const usable = ordered.filter(
    (sample) => sample.quality === 'good' && Number.isFinite(sample.numeric)
  );
  if (usable.length === 0) return null;

  const min = Math.min(...usable.map((sample) => sample.numeric));
  const max = Math.max(...usable.map((sample) => sample.numeric));
  const start = ordered[0].time;
  const end = ordered[ordered.length - 1].time;
  const points: { x: number; y: number }[] = [];
  let path = '';
  let connected = false;
  for (const sample of ordered) {
    if (sample.quality !== 'good' || !Number.isFinite(sample.numeric)) {
      connected = false;
      continue;
    }
    const x =
      end === start
        ? width / 2
        : ((sample.time - start) / (end - start)) * width;
    const y =
      max === min
        ? height / 2
        : height - 2 - ((sample.numeric - min) / (max - min)) * (height - 4);
    points.push({ x, y });
    path += `${connected ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)} `;
    connected = true;
  }
  return {
    path: path.trim(),
    points,
    min,
    max,
    start,
    end,
    excluded: samples.length - usable.length,
    uncertain: samples.filter((sample) => sample.quality === 'uncertain')
      .length,
    bad: samples.filter((sample) => sample.quality === 'bad').length
  };
}
