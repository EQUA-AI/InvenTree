import type { SeriesEntry, SeriesResponse } from '@lib/types/MachineHealth';
import { describe, expect, it } from 'vitest';
import { mergeHistoryBatches } from './historyBatches';
const response = (
  id: number,
  extra: Partial<SeriesResponse> = {}
): SeriesResponse =>
  ({
    machine: 18,
    window_start: '2026-10-01T00:00:00Z',
    window_end: '2026-10-02T00:00:00Z',
    resolution_seconds: 360,
    display_shift_seconds: 0,
    mode: 'sampled',
    series: [
      {
        binding_id: id,
        samples: [
          { t: 1, v: null, q: 'unknown' },
          { t: 2, v: 0, q: 'good' }
        ]
      } as SeriesEntry
    ],
    ...extra
  }) as SeriesResponse;
const ok = (value: SeriesResponse): PromiseFulfilledResult<SeriesResponse> => ({
  status: 'fulfilled',
  value
});
const failed: PromiseRejectedResult = {
  status: 'rejected',
  reason: new Error('offline')
};
describe('multi-batch history provenance', () => {
  it('keeps real zero and null samples unchanged through aggregation', () => {
    const first = response(1);
    const second = response(2);
    const merged = mergeHistoryBatches(
      [ok(first), ok(second)],
      [{ bindings: [1] }, { bindings: [2] }]
    );
    expect(merged.series).toEqual([...first.series, ...second.series]);
    expect(merged.read_batches).toEqual([
      { target_count: 1, failed: false },
      { target_count: 1, failed: false }
    ]);
  });
  it('preserves available siblings and explicitly records partial failures', () => {
    const merged = mergeHistoryBatches(
      [failed, ok(response(2))],
      [{ bindings: [1] }, { bindings: [2], keys: ['context'] }]
    );
    expect(merged.series.map((entry) => entry.binding_id)).toEqual([2]);
    expect(merged.read_batches).toEqual([
      { target_count: 1, failed: true },
      { target_count: 2, failed: false }
    ]);
  });
  it('all failed reads never become a successful empty result', () => {
    expect(() => mergeHistoryBatches([failed], [{ bindings: [1] }])).toThrow(
      'All bounded'
    );
  });
  for (const extra of [
    { machine: 19 },
    { window_end: '2026-10-03T00:00:00Z' },
    { resolution_seconds: 1 },
    { display_shift_seconds: 1 },
    { mode: 'complete' as const }
  ])
    it(`rejects incoherent ${Object.keys(extra)[0]}`, () => {
      expect(() =>
        mergeHistoryBatches(
          [ok(response(1)), ok(response(2, extra))],
          [{ bindings: [1] }, { bindings: [2] }]
        )
      ).toThrow('incompatible');
    });
});
