import type { MachineSignal } from '@lib/types/MachineHealth';
import { expect, it, vi } from 'vitest';
import {
  effectiveTest,
  exerciseRows,
  toggleExerciseSeries,
  visibleExerciseSeries
} from './exerciseValues';
import { buildKpiTiles } from './kpis';
import { resolveParameters } from './resolve';
import type { PerformanceTestAlarm } from './testAlarmRows';
import { testAlarmRows } from './testAlarmRows';

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...values: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '')
}));

it('refuses to hide the last measured line and its dependent test together', () => {
  const series = [
    { key: 'measured' },
    { key: 'test', measuredKey: 'measured' }
  ];
  const hidden = toggleExerciseSeries(series, new Set(), 'measured');
  expect([...hidden]).toEqual([]);
  expect(visibleExerciseSeries(series, hidden)).toEqual(series);
});

it('restores the measured parent when a dependent TEST chip is enabled', () => {
  const series = [
    { key: 'measured' },
    { key: 'other' },
    { key: 'test', measuredKey: 'measured' }
  ];
  const hidden = toggleExerciseSeries(series, new Set(), 'measured');
  expect(visibleExerciseSeries(series, hidden)).toEqual([{ key: 'other' }]);
  const restored = toggleExerciseSeries(series, hidden, 'test');
  expect(visibleExerciseSeries(series, restored)).toEqual(series);
  const testHidden = toggleExerciseSeries(series, restored, 'test');
  expect(
    visibleExerciseSeries(series, testHidden).map((line) => line.key)
  ).toEqual(['measured', 'other']);
});

it('never creates a pre-trigger test reading or mutates measured rows', () => {
  const measured = [
    { t: 0, motor: 40 },
    { t: 10, motor: 41 },
    { t: 20, motor: 42 }
  ];
  const rows = exerciseRows(
    measured,
    [{ key: 'test', value: 159.5, since: 12 }],
    0,
    30
  );
  expect(rows).toEqual([
    { t: 0, motor: 40, test: null },
    { t: 10, motor: 41, test: null },
    { t: 12, motor: null, test: 159.5 },
    { t: 20, motor: 42, test: 159.5 },
    { t: 30, motor: null, test: 159.5 }
  ]);
  expect(measured).toEqual([
    { t: 0, motor: 40 },
    { t: 10, motor: 41 },
    { t: 20, motor: 42 }
  ]);
  expect(exerciseRows(measured, [], 0, 30)).toEqual(measured);
});

it('does not create a test trace in a historical window preceding the exercise', () => {
  expect(
    exerciseRows(
      [{ t: 10, value: 40 }],
      [{ key: 'test', value: 159.5, since: 40 }],
      0,
      30
    )
  ).toEqual([{ t: 10, value: 40, test: null }]);
});

it('withholds incompatible unit overlays without dropping the visible anomaly', () => {
  const mismatch = {
    test_value: 159.5,
    unitMismatch: true
  } as PerformanceTestAlarm;
  expect(effectiveTest([mismatch])).toBeUndefined();
  const compatible = { ...mismatch, unitMismatch: false };
  expect(effectiveTest([mismatch, compatible])).toBe(compatible);
});

it('accepts canonical unit aliases on a family tile without mixing physical scales', () => {
  const alarm = {
    test_value: 159.5,
    unitMismatch: false,
    measured_unit: '°C'
  } as PerformanceTestAlarm;
  expect(effectiveTest([alarm], 'degC')).toBe(alarm);
  expect(effectiveTest([alarm], 'rpm')).toBeUndefined();
});

it('gives a tested channel on another physical scale its own KPI instead of hiding its value', () => {
  const measured = {
    binding_id: 1,
    display_name: 'Winding 1',
    external_key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED1',
    signal_kind: 'temperature',
    unit: 'degC',
    value: 40,
    quality: 'good',
    stale: false,
    state: 'normal',
    observed_at: '2026-10-24T12:00:00Z',
    limits: {
      normal_min: null,
      normal_max: null,
      warn_min: null,
      warn_max: null,
      critical_min: null,
      critical_max: null
    }
  } as MachineSignal;
  const tested = {
    ...measured,
    binding_id: 3,
    display_name: 'Winding 3',
    external_key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED3',
    unit: 'K',
    value: 314.15,
    test_alarms: [
      {
        anomaly_id: 12,
        status: 'open',
        test_value: 400,
        unit: 'K',
        triggered_at: '2026-10-24T12:00:00Z'
      }
    ]
  } as MachineSignal;
  const tiles = buildKpiTiles(
    resolveParameters([
      measured,
      {
        ...measured,
        binding_id: 2,
        external_key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED2'
      },
      tested
    ]),
    new Map()
  );
  const tile = tiles.find((candidate) => candidate.key === 'test-3');
  expect(tile).toMatchObject({ unit: 'K', bindingIds: [3], value: 314.15 });
  expect(effectiveTest(testAlarmRows([tested]), tile?.unit)?.test_value).toBe(
    400
  );
});
