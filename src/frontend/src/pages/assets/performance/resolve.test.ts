import type { MachineSignal } from '@lib/types/MachineHealth';
import { expect, it } from 'vitest';
import { healthCoverage } from '../health/healthCoverage';
import { resolveParameters } from './resolve';

const signal = (id: number, active?: boolean): MachineSignal => ({
  binding_id: id,
  source_id: 1,
  source_name: 'Synthetic source',
  source_type: 'manual',
  external_key: `/custom/${id}`,
  display_name: `Custom ${id}`,
  signal_kind: 'other',
  unit: '',
  value: null,
  observed_at: null,
  received_at: null,
  quality: 'unknown',
  stale: true,
  freshness_threshold_seconds: 300,
  state: 'unknown',
  limits: {
    normal_min: null,
    normal_max: null,
    warn_min: null,
    warn_max: null,
    critical_min: null,
    critical_max: null
  },
  ...(active === undefined ? {} : { active })
});

it('retains retired critical coverage without restoring it as a current display channel', () => {
  const retired = {
    ...signal(3, false),
    critical_designation: {
      critical: true,
      version: 1,
      reason: 'Engineer review',
      recorded_by: 'Synthetic engineer',
      recorded_at: '2026-10-20T08:00:00Z'
    }
  };
  const rows = [signal(1, true), signal(2), retired];
  expect(healthCoverage(rows).criticalCoverage).toEqual({
    designated: 1,
    usable: 0,
    evaluated: 0
  });
  expect(resolveParameters(rows).map((row) => row.signal.binding_id)).toEqual([
    1, 2
  ]);
});
