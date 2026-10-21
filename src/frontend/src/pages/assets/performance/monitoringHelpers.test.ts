import type { SeriesEntry } from '@lib/types/MachineHealth';
import { describe, expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));
import { matchesChannel } from './channelNavigation';
import { unitLabel } from './format';
import { limitLines } from './limitLines';
import { performanceSparkline } from './sparkline';
import { splitLimits } from './thresholdBands';
describe('lossless monitoring units', () => {
  it.each([
    ['degC', '°C'],
    ['degF', '°F'],
    ['m3/h', 'm³/h'],
    ['mH2O', 'mH₂O'],
    ['percent', '%'],
    ['m3/s', 'm³/s'],
    ['cusec', 'cusec'],
    ['raw-word', 'raw-word'],
    ['', 'unit not recorded'],
    [null, 'unit not recorded']
  ])('displays %s', (raw, label) => expect(unitLabel(raw)).toBe(label));
});
it('separates actual drawn and off-scale boundaries, including scale edges', () => {
  const lines = limitLines({ warn_max: 125, critical_max: 145 }, 'degC');
  expect(lines.map((line) => line.label)).toEqual([
    'Warning above 125 °C',
    'Critical above 145 °C'
  ]);
  expect(splitLimits(lines, 76, 84)).toEqual({ drawn: [], offScale: lines });
  expect(splitLimits(lines, 70, 145)).toEqual({ drawn: lines, offScale: [] });
});
const entry = (samples: SeriesEntry['samples']) =>
  ({ available: true, samples }) as SeriesEntry;
it('uses actual time spacing and two subpaths across a measured silence', () => {
  const path = performanceSparkline(
    entry([0, 5, 60, 65].map((s, i) => ({ t: s * 1000, v: i + 1, q: 'good' }))),
    { start: 0, end: 65000, resolutionSeconds: 5 }
  );
  expect(path?.match(/M/g)).toHaveLength(2);
  expect(path).toContain('M88.6,');
  expect(path).toContain('L7.4,');
});
it('also breaks on null and bad-quality readings instead of dropping them', () => {
  const path = performanceSparkline(
    entry([
      { t: 0, v: 1, q: 'good' },
      { t: 5000, v: 2, q: 'good' },
      { t: 10000, v: null, q: 'good' },
      { t: 15000, v: 4, q: 'good' },
      { t: 20000, v: 5, q: 'bad' },
      { t: 25000, v: 6, q: 'good' }
    ]),
    { start: 0, end: 25000, resolutionSeconds: 5 }
  );
  expect(path?.match(/M/g)).toHaveLength(3);
});

it('finds both the displayed and recorded unit without changing comparison identities', () => {
  const channel = {
    label: 'Synthetic winding',
    signal: { display_name: 'RTD', external_key: '/fixture/rtd', unit: 'degC' }
  } as any;
  expect(matchesChannel(channel, '°C')).toBe(true);
  expect(matchesChannel(channel, 'degC')).toBe(true);
  expect(channel.signal.unit).toBe('degC');
});
