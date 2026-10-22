import type { SignalTrend } from '@lib/types/MachineHealth';
import { expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (args[index] ?? ''), '')
}));
import { trendStatusFact } from './trendPresentation';

const trend = (extra: Partial<SignalTrend> = {}): SignalTrend => ({
  binding_id: 1,
  display_name: 'Flow',
  unit: 'cusec',
  signal_kind: 'flow',
  source_id: 1,
  source_name: 'Plant historian',
  source_type: 'scada',
  window_start: '2026-10-22T09:00:00Z',
  window_end: '2026-10-22T09:10:00Z',
  max_samples: 120,
  limits: { max_window_seconds: 600, max_samples: 120 },
  available: true,
  samples: [],
  ...extra
});
const sample = (
  value: number | null,
  second = 0,
  quality: 'good' | 'bad' = 'good'
) => ({
  observed_at: `2026-10-22T09:00:${String(second).padStart(2, '0')}Z`,
  value,
  quality
});

it('keeps pending, omitted and failed reads distinct, with one shared reason for the cell and row control', () => {
  expect(trendStatusFact(null, { pending: true })).toBeNull();
  expect(trendStatusFact(null)).toEqual({
    failed: false,
    label: 'Not returned',
    detail:
      'The completed trend read did not return this signal. Its history is unknown; the current reading is unaffected.'
  });
  expect(trendStatusFact(null, { failed: true })).toEqual({
    failed: true,
    label: 'Read failed',
    detail: 'The trend read failed. The current reading is unaffected.'
  });
});
it.each(['SOURCE_TIMEOUT', 'SOURCE_UNAVAILABLE'])(
  'preserves a real %s failure and the supplied explanation',
  (reason) => {
    expect(
      trendStatusFact(
        trend({
          available: false,
          reason,
          detail: 'The historian did not answer.'
        })
      )
    ).toEqual({
      failed: true,
      label: 'Read failed',
      detail: 'The historian did not answer.'
    });
  }
);
it.each([
  ['NO_CONNECTOR', 'History unavailable'],
  ['HISTORY_UNSUPPORTED', 'No history']
])(
  'keeps %s as a missing history capability, not a failed read',
  (reason, label) => {
    expect(trendStatusFact(trend({ available: false, reason }))).toEqual({
      failed: false,
      label,
      detail: 'The source could not serve history for this signal.'
    });
  }
);
it('keeps an empty answered window distinct from an omitted binding', () => {
  expect(trendStatusFact(trend())).toEqual({
    failed: false,
    label: 'No samples in window',
    detail:
      'Plant historian answered for this window but held no readings for this signal.'
  });
});
it('shares the exact unavailable drawing counts, including undated and bad-quality samples', () => {
  expect(trendStatusFact(trend({ samples: [sample(2)] }))).toEqual({
    failed: false,
    label: 'Only one reading',
    detail:
      '1 of 1 timed readings in this window can be drawn; 0 readings have no readable time.'
  });
  expect(
    trendStatusFact(
      trend({
        samples: [
          sample(2),
          sample(3, 5, 'bad'),
          { ...sample(4), observed_at: 'invalid' }
        ]
      })
    )
  ).toEqual({
    failed: false,
    label: '1 of 2 drawable',
    detail:
      '1 of 2 timed readings in this window can be drawn; 1 readings have no readable time.'
  });
  expect(trendStatusFact(trend({ samples: [sample(null)] }))?.label).toBe(
    'No drawable readings'
  );
});
it('does not manufacture an absent state for a drawable trend or mutate its samples', () => {
  const supplied = trend({ samples: [sample(0), sample(2, 5)] });
  const original = structuredClone(supplied);
  expect(trendStatusFact(supplied)).toBeNull();
  expect(supplied).toEqual(original);
});
