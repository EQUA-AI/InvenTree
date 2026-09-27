import { describe, expect, it } from 'vitest';

import { analysisContext, hintIsRedundant } from './scopeContext';

describe('read-only analysis context', () => {
  it('reports an unconfirmed context instead of inventing one', () => {
    expect(analysisContext(null)).toEqual({ kind: 'unconfirmed' });
    expect(analysisContext({ mode: 'legacy_unconfirmed' })).toEqual({
      kind: 'unconfirmed'
    });
    expect(analysisContext({ mode: 'site_group' })).toEqual({
      kind: 'unconfirmed'
    });
  });

  it('names authorized machines for the fleet mode', () => {
    expect(analysisContext({ mode: 'all_authorized_assets' })).toEqual({
      kind: 'fleet'
    });
  });

  it('names the confirmed explicit selection', () => {
    expect(
      analysisContext({ mode: 'explicit_assets', displayLabel: 'Pump A' })
    ).toEqual({ kind: 'explicit', label: 'Pump A', machineCount: null });
    expect(
      analysisContext({
        mode: 'explicit_assets',
        displayLabel: 'Line 3 cells',
        machineCount: 4
      })
    ).toEqual({ kind: 'explicit', label: 'Line 3 cells', machineCount: 4 });
    expect(
      analysisContext({ mode: 'explicit_assets', machineCount: 1 })
    ).toEqual({ kind: 'explicit', label: null, machineCount: 1 });
    expect(
      analysisContext({ mode: 'explicit_assets', machineCount: 3 })
    ).toEqual({ kind: 'explicit', label: null, machineCount: 3 });
    // Nothing to name and nothing to count: do not invent a context.
    expect(analysisContext({ mode: 'explicit_assets' })).toEqual({
      kind: 'unconfirmed'
    });
    expect(
      analysisContext({ mode: 'explicit_assets', displayLabel: '' })
    ).toEqual({ kind: 'unconfirmed' });
  });
});

describe('routing-hint collapse', () => {
  const hint = { machineId: 7, machineName: 'Pump A' };

  it('keeps the hint while the confirmed scope does not cover it', () => {
    expect(hintIsRedundant(null, hint)).toBe(false);
    expect(hintIsRedundant({ mode: 'legacy_unconfirmed' }, hint)).toBe(false);
    expect(hintIsRedundant({ mode: 'all_authorized_assets' }, hint)).toBe(
      false
    );
    expect(
      hintIsRedundant({ mode: 'explicit_assets', displayLabel: 'Pump B' }, hint)
    ).toBe(false);
  });

  it('collapses the hint once the server confirmed the same machine', () => {
    expect(
      hintIsRedundant({ mode: 'explicit_assets', displayLabel: 'Pump A' }, hint)
    ).toBe(true);
  });
});
