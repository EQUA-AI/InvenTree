import { expect, it } from 'vitest';
import { normalizePresentationData } from './datasets';

const data = () => [
  {
    source_key: 'timeline',
    kind: 'trend_dataset',
    unit: 'records',
    source: 'work_orders',
    source_revision: 'snapshot-1',
    as_of: '2026-10-24T00:00:00Z',
    timezone: 'UTC',
    samples: [
      { observed_at: '2026-01-01', value: 4, quality: 'good' },
      { observed_at: '2026-02-01', value: null, quality: 'missing' },
      { observed_at: '2026-05-01', value: 9, quality: 'uncertain' }
    ]
  }
];
it('preserves missing and uncertain historical values without filling or fetching', () => {
  expect(normalizePresentationData(data())).toEqual(data());
});
it('rejects malformed, unbounded, nonnumeric and unordered snapshots as a whole', () => {
  for (const transform of [
    (value: any) => {
      value[0].samples[0].value = false;
    },
    (value: any) => {
      value[0].samples[0].value = Number.NaN;
    },
    (value: any) => {
      value[0].samples.reverse();
    },
    (value: any) => {
      value[0].samples[1].observed_at = value[0].samples[0].observed_at;
    },
    (value: any) => {
      value[0].samples[0].quality = 'safe';
    },
    (value: any) => {
      value[0].samples = Array(513).fill(value[0].samples[0]);
    },
    (value: any) => {
      value[0].url = 'https://invalid.example';
    }
  ]) {
    const value = data();
    transform(value);
    expect(normalizePresentationData(value)).toEqual([]);
  }
});
