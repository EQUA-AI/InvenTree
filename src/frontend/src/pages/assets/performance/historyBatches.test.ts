import type { SeriesEntry, SeriesResponse } from '@lib/types/MachineHealth';
import { describe, expect, it } from 'vitest';
import { mergeHistoryBatches } from './historyBatches';
import {
  alignRows,
  differenceSeries,
  gapThresholdMs,
  indexSeries
} from './series';
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
  it('rejects a different historical source even when its machine and clock match', () => {
    const first = response(1, {
      history_source: 1,
      source_id: 1,
      telemetry_presentation: 'recorded_history'
    });
    const other = response(2, {
      history_source: 2,
      source_id: 2,
      telemetry_presentation: 'recorded_history'
    });
    const merged = mergeHistoryBatches(
      [ok(first), ok(other)],
      [{ bindings: [1] }, { bindings: [2] }]
    );
    expect(merged.series.map((entry) => entry.binding_id)).toEqual([1]);
    expect(merged.read_batches?.map((batch) => batch.failed)).toEqual([
      false,
      true
    ]);
  });
  const base = Date.parse('2026-10-01T00:00:00Z');
  const cadenceRead = (
    id: number,
    seconds: number,
    offsets: number[],
    bad = -1
  ) =>
    response(id, {
      mode: 'complete',
      slots: null,
      window_end: '2026-10-01T00:05:00Z',
      resolution_seconds: seconds,
      cadence_seconds: seconds,
      series: [
        {
          ...response(id).series[0],
          available: true,
          external_key: `key-${id}`,
          samples: offsets.map((offset, index) => ({
            t: base + offset * 1000,
            v: index,
            q: index === bad ? 'bad' : 'good'
          }))
        }
      ]
    });
  it('retains a bad input instant as an unknown calculated difference', () => {
    const samples = differenceSeries(
      cadenceRead(1, 20, [0, 20, 40], 1).series[0],
      cadenceRead(2, 20, [0, 20, 40]).series[0]
    );
    expect(samples.map((sample) => sample.v)).toEqual([0, null, 0]);
  });
  it('does not join calculated differences across an unmatched input instant', () => {
    const samples = differenceSeries(
      cadenceRead(1, 5, [0, 5, 10]).series[0],
      cadenceRead(2, 5, [0, 10]).series[0]
    );
    expect(samples.map((sample) => sample.v)).toEqual([0, null, 1]);
  });
  it('draws a slower batch at its own returned resolution without adding false silence breaks', () => {
    const merged = mergeHistoryBatches(
      [ok(cadenceRead(1, 5, [0, 5, 10])), ok(cadenceRead(2, 20, [0, 20, 40]))],
      [{ bindings: [1] }, { bindings: [2] }]
    );
    const rows = alignRows(
      [{ key: 'slow', entry: indexSeries(merged).get(2) }],
      gapThresholdMs(merged.resolution_seconds)
    );
    expect(rows.map((row) => row.slow)).toEqual([0, 1, 2]);
  });
  it('keeps real silence and bad-quality breaks with per-batch resolution', () => {
    const merged = mergeHistoryBatches(
      [
        ok(cadenceRead(1, 5, [0, 5, 10])),
        ok(cadenceRead(2, 20, [0, 20, 40, 60, 80, 200], 2))
      ],
      [{ bindings: [1] }, { bindings: [2] }]
    );
    const rows = alignRows(
      [{ key: 'slow', entry: indexSeries(merged).get(2) }],
      gapThresholdMs(merged.resolution_seconds)
    );
    expect(rows.map((row) => row.slow)).toEqual([0, 1, null, 3, 4, null, 5]);
    expect(rows.at(-2)?.t).toBe(base + 80001);
  });
  it('keeps real zero and null samples unchanged through aggregation', () => {
    const first = response(1);
    const second = response(2);
    const merged = mergeHistoryBatches(
      [ok(first), ok(second)],
      [{ bindings: [1] }, { bindings: [2] }]
    );
    expect(merged.series).toEqual([...first.series, ...second.series]);
    expect(merged.read_batches).toEqual([
      expect.objectContaining({ target_count: 1, failed: false }),
      expect.objectContaining({ target_count: 1, failed: false })
    ]);
  });
  it('preserves available siblings and explicitly records partial failures', () => {
    const merged = mergeHistoryBatches(
      [failed, ok(response(2))],
      [{ bindings: [1] }, { bindings: [2], keys: ['context'] }]
    );
    expect(merged.series.map((entry) => entry.binding_id)).toEqual([2]);
    expect(merged.read_batches).toEqual([
      expect.objectContaining({
        target_count: 1,
        failed: true,
        bindings: [1],
        keys: []
      }),
      expect.objectContaining({ target_count: 2, failed: false })
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
    { slots: 42 },
    { display_shift_seconds: 100 },
    { mode: 'complete' as const }
  ])
    it(`marks only incoherent ${Object.keys(extra)[0]} batch failed`, () => {
      const merged = mergeHistoryBatches(
        [ok(response(1)), ok(response(2, extra))],
        [{ bindings: [1] }, { bindings: [2] }]
      );
      expect(merged.series.map((entry) => entry.binding_id)).toEqual([1]);
      expect(merged.read_batches?.[1].failed).toBe(true);
    });
  it('allows one-second request skew and different measured complete cadences', () => {
    const merged = mergeHistoryBatches(
      [
        ok(
          response(1, {
            mode: 'complete',
            resolution_seconds: 5,
            cadence_seconds: 5,
            display_shift_seconds: 31536000
          })
        ),
        ok(
          response(2, {
            mode: 'complete',
            resolution_seconds: 20,
            cadence_seconds: 20,
            display_shift_seconds: 31536001
          })
        )
      ],
      [{ bindings: [1] }, { bindings: [2] }]
    );
    expect(merged.series).toHaveLength(2);
    expect(merged.read_batches?.every((batch) => !batch.failed)).toBe(true);
    expect(
      merged.read_batches?.map((batch) => batch.resolution_seconds)
    ).toEqual([5, 20]);
    expect(merged.read_batches?.map((batch) => batch.cadence_seconds)).toEqual([
      5, 20
    ]);
  });
});
