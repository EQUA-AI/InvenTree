import type { MachineSignal } from '@lib/types/MachineHealth';
import { expect, it } from 'vitest';
import { testAlarmRows } from './testAlarmRows';

const signal = {
  binding_id: 12,
  display_name: 'Winding 5',
  external_key: '/dex/PUMP7_PUMP_MOTOR_WINDING_TEMPERATURED5',
  unit: 'degC',
  value: 43.6,
  state: 'normal',
  quality: 'good',
  stale: false,
  test_alarms: [
    {
      anomaly_id: 9,
      status: 'open',
      test_value: 159.5,
      unit: 'degC',
      triggered_at: '2026-10-24T16:00:00Z'
    }
  ]
} as MachineSignal;

it('retains measured values and adds a separate exact-binding exercise', () => {
  expect(testAlarmRows([signal])).toMatchObject([
    { binding_id: 12, test_value: 159.5, measured_value: 43.6 }
  ]);
  expect(signal.value).toBe(43.6);
  expect(signal.state).toBe('normal');
  expect(testAlarmRows([{ ...signal, test_alarms: [] }])).toEqual([]);
});

it('does not present invalid, mismatched or ended tests as active', () => {
  for (const change of [
    { status: 'resolved' },
    { status: 'suppressed' },
    { test_value: Number.NaN }
  ]) {
    const invalid = {
      ...signal,
      test_alarms: [{ ...signal.test_alarms![0], ...change }]
    } as MachineSignal;
    expect(testAlarmRows([invalid])).toEqual([]);
  }
});

it('includes only properly bounded resolved exercises in history', () => {
  const resolved = {
    ...signal,
    test_alarms: [
      {
        ...signal.test_alarms![0],
        status: 'resolved',
        resolved_at: '2026-10-24T16:30:00Z'
      }
    ]
  } as MachineSignal;
  expect(testAlarmRows([resolved])).toEqual([]);
  expect(testAlarmRows([resolved], undefined, true)).toMatchObject([
    { status: 'resolved', resolved_at: '2026-10-24T16:30:00Z' }
  ]);
  for (const resolvedAt of [null, 'invalid', '2026-10-24T15:30:00Z']) {
    expect(
      testAlarmRows(
        [
          {
            ...resolved,
            test_alarms: [
              { ...resolved.test_alarms![0], resolved_at: resolvedAt }
            ]
          }
        ],
        undefined,
        true
      )
    ).toEqual([]);
  }
});

it('connects the chart label to the source channel name', () => {
  expect(
    testAlarmRows(
      [
        {
          ...signal,
          external_key: '/dex/PUMP7_MOTOR_CORE_RTD4_PROCESS_VALUE',
          display_name: 'Motor Core RTD 4'
        }
      ],
      new Map([[12, 'Stator core 4']])
    )[0].label
  ).toBe('Stator core 4 (Motor Core RTD 4)');
});

it('retains unit mismatches as visible notices while distinguishing equivalent unit labels', () => {
  const equivalent = { ...signal, unit: '°C' };
  expect(testAlarmRows([equivalent])[0].unitMismatch).toBe(false);
  const mismatch = { ...signal, unit: 'rpm' };
  expect(testAlarmRows([mismatch])[0]).toMatchObject({
    unitMismatch: true,
    unit: 'degC',
    measured_unit: 'rpm'
  });
});

it('carries stale and bad-quality provenance without changing the reading', () => {
  expect(
    testAlarmRows([
      { ...signal, value: 3276.7, stale: true, quality: 'bad' }
    ])[0]
  ).toMatchObject({
    measured_value: 3276.7,
    measured_stale: true,
    measured_quality: 'bad'
  });
});
