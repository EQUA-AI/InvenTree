import type { MachineSignal } from '@lib/types/MachineHealth';
import { describe, expect, it } from 'vitest';
import { healthCoverage } from './healthCoverage';

const signal = (overrides: Partial<MachineSignal> = {}): MachineSignal => ({
  binding_id: 1,
  source_id: 1,
  source_name: 'Source',
  source_type: 'scada',
  external_key: '/exact/1',
  display_name: 'Flow',
  signal_kind: 'flow',
  unit: 'm3/h',
  value: 0,
  observed_at: '2026-10-18T09:00:00Z',
  received_at: '2026-10-18T09:00:01Z',
  quality: 'good',
  stale: false,
  freshness_threshold_seconds: 300,
  state: 'normal',
  limits: {
    normal_min: null,
    normal_max: null,
    warn_min: null,
    warn_max: null,
    critical_min: null,
    critical_max: 10
  },
  ...overrides
});

describe('supported Health coverage', () => {
  it('counts a real zero as usable and server-evaluated', () => {
    expect(healthCoverage([signal()])).toMatchObject({
      mapped: 1,
      usable: 1,
      evaluated: 1
    });
  });
  it('does not turn mixed normal and missing evidence into full coverage', () => {
    expect(
      healthCoverage([
        signal(),
        signal({ value: null, observed_at: null, state: 'unknown' })
      ])
    ).toMatchObject({
      mapped: 2,
      usable: 1,
      evaluated: 1,
      criticalCoverage: null
    });
  });
  it('critical thresholds never establish critical-signal coverage', () => {
    expect(
      healthCoverage([signal({ state: 'critical' })]).criticalCoverage
    ).toBeNull();
  });
  it('does not evaluate an unclassified good reading', () => {
    expect(healthCoverage([signal({ state: 'unknown' })])).toMatchObject({
      usable: 1,
      evaluated: 0
    });
  });
  it('does not count last-known or poor-quality data as current coverage', () => {
    const rows = [
      signal({ stale: true }),
      signal({ quality: 'bad' }),
      signal({ quality: 'uncertain' }),
      signal({ quality: 'unknown' })
    ];
    expect(healthCoverage(rows)).toMatchObject({
      mapped: 4,
      usable: 0,
      evaluated: 0,
      stale: 1
    });
  });
  it('unreadable observation times do not support freshness', () => {
    expect(healthCoverage([signal({ observed_at: 'invalid' })]).usable).toBe(0);
  });
  it('a source status can be usable without a numeric assessment', () => {
    expect(
      healthCoverage([signal({ value: 'RUNNING', state: 'unknown' })])
    ).toMatchObject({ usable: 1, evaluated: 0 });
  });
  it('an empty successful mapping is counted, critical coverage stays unknown', () => {
    expect(healthCoverage([])).toEqual({
      mapped: 0,
      usable: 0,
      evaluated: 0,
      stale: 0,
      criticalCoverage: null
    });
  });
});

describe('engineer-designated coverage', () => {
  it('counts only designated signals and leaves unassessed critical data visible', () => {
    const critical = {
      critical: true,
      version: 1,
      reason: 'Engineer review',
      recorded_by: 'Engineer',
      recorded_at: '2026-10-20T08:00:00Z'
    };
    expect(
      healthCoverage([
        signal({ critical_designation: critical }),
        signal({ critical_designation: critical, state: 'unknown' }),
        signal({ state: 'normal' })
      ]).criticalCoverage
    ).toEqual({ designated: 2, usable: 2, evaluated: 1 });
  });
  it('does not count revoked designations or thresholds', () => {
    expect(
      healthCoverage([
        signal({
          critical_designation: {
            critical: false,
            version: 2,
            reason: 'Review revised',
            recorded_by: 'Engineer',
            recorded_at: '2026-10-20T08:00:00Z'
          }
        })
      ]).criticalCoverage
    ).toBeNull();
  });
});

it('keeps retired designated critical signals in missing coverage without counting them as active mapping', () => {
  const result = healthCoverage([
    signal({
      active: false,
      stale: true,
      state: 'unknown',
      value: null,
      critical_designation: {
        critical: true,
        version: 1,
        reason: 'Required signal',
        recorded_by: 'Engineer',
        recorded_at: '2026-10-20T08:00:00Z'
      }
    })
  ]);
  expect(result.mapped).toBe(0);
  expect(result.criticalCoverage).toEqual({
    designated: 1,
    usable: 0,
    evaluated: 0
  });
});

it('keeps a remapped critical decision missing until it is reviewed again', () => {
  const result = healthCoverage([
    signal({
      critical_designation: {
        critical: true,
        version: 1,
        reason: 'Before remap',
        recorded_by: 'Engineer',
        recorded_at: '2026-10-20T08:00:00Z',
        applies_current: false,
        review_required: true
      }
    })
  ]);
  expect(result.criticalCoverage).toEqual({
    designated: 1,
    usable: 0,
    evaluated: 0
  });
});
