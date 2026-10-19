import { describe, expect, it } from 'vitest';

import type { Investigation } from '@lib/types/WorkExecution';

import {
  diagnoseReturnPath,
  isSnapshotId,
  measured,
  readInvestigation
} from './investigationProjection';

function investigation(extra: Partial<Investigation> = {}): Investigation {
  return {
    rule: 'investigation.v1',
    packet: { id: 901, reference: 'RP-901', generation_status: 'succeeded' },
    explanation: {
      likely_cause: 'Drive-end bearing wear',
      failure_mode: null,
      preliminary: true,
      verified_by: null,
      verified_at: null,
      analysis_status: 'available',
      authority: 'derived',
      provider: 'wf7',
      model_or_rule_version: null,
      generated_at: null,
      data_window: { start: null, end: null, snapshot_count: 0 },
      stale_signal_count: null,
      quality_summary: null,
      evidence: [
        { observation: 'a', relation: 'supports', snapshot_id: null },
        { observation: 'b', relation: 'contradicts', snapshot_id: null },
        {
          observation: 'c',
          relation: 'refutes' as 'unknown',
          snapshot_id: null
        }
      ],
      alternatives: [],
      proposed_checks: [],
      amendments: []
    },
    findings: [
      {
        id: 1,
        finding_key: 'F-1',
        category: 'measurement',
        observation: 'x',
        value: 0,
        unit: 'g',
        evidence_source: '',
        snapshot_id: null,
        observed_at: null,
        verification: 'disputed',
        recorded_by: null,
        created_at: '',
        updated_at: ''
      }
    ],
    finding_history: 'overwritten_in_place',
    intervention: null,
    requests: { available: false, reason: 'no_provider' },
    ...extra
  };
}

describe('readInvestigation', () => {
  it('is unsupported without the v1 projection', () => {
    expect(readInvestigation(undefined)).toEqual({ kind: 'unsupported' });
    expect(
      readInvestigation({ ...investigation(), rule: 'investigation.v0' })
    ).toEqual({ kind: 'unsupported' });
  });

  it('reports standalone work as having no saved investigation', () => {
    expect(
      readInvestigation(investigation({ packet: null, explanation: null }))
    ).toEqual({ kind: 'no_packet', requestsAvailable: false });
  });

  it('groups evidence by its recorded relation and never promotes unknown', () => {
    const reading = readInvestigation(investigation());
    expect(reading.kind).toBe('ready');
    if (reading.kind !== 'ready') return;
    expect(reading.qualification).toBe('preliminary');
    expect(reading.evidence.supports.map((item) => item.observation)).toEqual([
      'a'
    ]);
    expect(
      reading.evidence.contradicts.map((item) => item.observation)
    ).toEqual(['b']);
    expect(reading.evidence.unknown.map((item) => item.observation)).toEqual([
      'c'
    ]);
    expect(reading.disputed.map((finding) => finding.finding_key)).toEqual([
      'F-1'
    ]);
    expect(reading.requestsAvailable).toBe(false);
  });

  it('takes verification from the server flag, and an empty cause is none', () => {
    const base = investigation();
    const verified = readInvestigation({
      ...base,
      explanation: { ...base.explanation!, preliminary: false }
    });
    expect(verified.kind === 'ready' && verified.qualification).toBe(
      'verified'
    );
    const empty = readInvestigation({
      ...base,
      explanation: { ...base.explanation!, likely_cause: '' }
    });
    expect(empty.kind === 'ready' && empty.qualification).toBe('none');
  });
});

describe('measured', () => {
  it('keeps zero, joins units and leaves missing values missing', () => {
    expect(measured(0, 'mm/s')).toBe('0 mm/s');
    expect(measured(7.1, '')).toBe('7.1');
    expect(measured(null, 'mm/s')).toBeNull();
    expect(measured(undefined)).toBeNull();
  });
});

describe('diagnoseReturnPath', () => {
  it('returns to Diagnose on the same Work, keeping other query state', () => {
    expect(
      diagnoseReturnPath('/maintenance/work-orders/73/', '?work_panel=work&x=1')
    ).toBe('/maintenance/work-orders/73/?work_panel=diagnose&x=1');
    expect(diagnoseReturnPath('/maintenance/work-orders/73/', '')).toBe(
      '/maintenance/work-orders/73/?work_panel=diagnose'
    );
  });
});

it('distinguishes snapshot UUIDs from complete typed record citations', () => {
  expect(isSnapshotId('6f1c2d4e-0000-4000-8000-000000000001')).toBe(true);
  expect(isSnapshotId('work_order_closeout:41@7')).toBe(false);
  expect(isSnapshotId('machine_signal_state:7@9')).toBe(false);
});
