import { describe, expect, it, vi } from 'vitest';
import {
  alarmAttention,
  hasDischargeFlow,
  operatingState,
  readingSeverity
} from './mimicVisuals';
import type { MimicAlarm, MimicData, MimicPoint } from './types';

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));

const point = (extra: Partial<MimicPoint> = {}): MimicPoint => ({
  pointer: '/pd/P7/dv',
  label: 'Discharge flow',
  group: 'Process',
  value: 2.4,
  unit: 'cusec',
  quality: 'good',
  observed_at: '2026-10-22T06:32:41Z',
  age_seconds: 0,
  unchanged_for_seconds: null,
  reason: null,
  condition: 'unknown',
  thresholds_configured: false,
  ...extra
});
const data = (extra: Partial<MimicPoint> = {}): MimicData => ({
  station: 17,
  name: 'Synthetic unit source',
  generated_at: '2026-10-22T06:32:41Z',
  enabled: true,
  reporting: true,
  source: { pk: 1, name: 'Unit fixture' },
  last_poll_at: null,
  last_error_code: '',
  layout: {
    version: 1,
    review_status: 'provisional',
    elements: [
      {
        id: 'pump-flow',
        view: 'unit',
        pointer: '/pd/{pump}/dv',
        role: 'value',
        label: 'Discharge flow',
        x: 0,
        y: 0
      }
    ]
  },
  bays: [
    {
      key: 'P7',
      name: 'Pump 7',
      active: true,
      state: 'running',
      points: { '/pd/P7/dv': point(extra) }
    }
  ],
  station_points: {},
  selected_unit: 'P7',
  points: {},
  totals: {},
  alarms: [],
  unconfigured_thresholds: 0
});

describe('source-driven mimic emphasis', () => {
  it('requires running and an actual positive current discharge reading for motion', () => {
    const source = data();
    expect(hasDischargeFlow(source, source.bays[0])).toBe(true);
    for (const state of ['idle', 'fault', 'stale', 'unknown']) {
      expect(hasDischargeFlow(source, { ...source.bays[0], state })).toBe(
        false
      );
    }
    expect(hasDischargeFlow(source, undefined)).toBe(false);
  });
  it.each([
    { value: 0 },
    { value: -1 },
    { value: null },
    { value: Number.NaN },
    { reason: 'stale' },
    { reason: 'not_bound' },
    { quality: 'bad' },
    { quality: 'unknown' }
  ])(
    'never animates absent, stopped, reverse or unreliable flow: %j',
    (extra) => {
      const source = data(extra);
      expect(hasDischargeFlow(source, source.bays[0])).toBe(false);
    }
  );
  it('never leaves a source-disabled or unavailable running state green', () => {
    const source = data();
    for (const extra of [
      { enabled: false },
      { reporting: false },
      { read_failed: true }
    ]) {
      const unavailable = { ...source, ...extra };
      expect(operatingState(unavailable, source.bays[0])).not.toBe('running');
      expect(hasDischargeFlow(unavailable, source.bays[0])).toBe(false);
    }
  });
  it('only uses a configured current verdict to colour a value', () => {
    expect(readingSeverity(point({ condition: 'critical' }))).toBeNull();
    expect(
      readingSeverity(
        point({ thresholds_configured: true, condition: 'warning' })
      )
    ).toBe('warning');
    expect(
      readingSeverity(
        point({
          thresholds_configured: true,
          condition: 'critical',
          quality: 'bad'
        })
      )
    ).toBeNull();
    expect(
      readingSeverity(
        point({
          thresholds_configured: true,
          condition: 'critical',
          reason: 'stale'
        })
      )
    ).toBeNull();
    expect(
      readingSeverity(
        point({ thresholds_configured: true, condition: 'normal' })
      )
    ).toBeNull();
  });
  it('uses actual active alarm acknowledgement and exact scope, never severity alone', () => {
    const alarm: MimicAlarm = {
      ...point(),
      anomaly: 91,
      machine: 7,
      severity: 'critical',
      anomaly_status: 'open'
    };
    expect(alarmAttention([alarm], 7)).toBe('unacknowledged');
    expect(alarmAttention([alarm], 8)).toBeUndefined();
    expect(alarmAttention([alarm], undefined, ['/other'])).toBeUndefined();
    expect(
      alarmAttention([{ ...alarm, anomaly_status: 'acknowledged' }], 7)
    ).toBe('acknowledged');
    expect(
      alarmAttention([{ ...alarm, anomaly_status: 'suppressed' }], 7)
    ).toBeUndefined();
    expect(
      alarmAttention([{ ...alarm, anomaly_status: null }], 7)
    ).toBeUndefined();
    expect(alarmAttention([{ ...alarm, anomaly: null }], 7)).toBeUndefined();
  });
});
