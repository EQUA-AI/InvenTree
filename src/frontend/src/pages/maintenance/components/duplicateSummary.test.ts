import { describe, expect, it, vi } from 'vitest';
import { duplicateSummary } from './duplicateSummary';

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...values: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '')
}));

describe('duplicate repair summary', () => {
  it('humanizes the source reason and lifecycle status', () => {
    expect(
      duplicateSummary({
        reason: 'OPEN_REPAIR_ON_MACHINE',
        status: 'in_progress',
        repair_packet_id: null
      })
    ).toBe('in progress · No repair packet · Open repair on this machine');
  });

  it('preserves ownership and packet facts without exposing reason codes', () => {
    const summary = duplicateSummary({
      reason: 'ANOMALY_ALREADY_LINKED',
      status: 'in-progress',
      repair_packet_id: 28
    });
    expect(summary).toBe(
      'in progress · Repair packet · Source already has owning work'
    );
    expect(summary).not.toMatch(/[A-Z]+_[A-Z_]+/);
  });

  it('does not invent an open status or a specific reason when either is unknown', () => {
    const summary = duplicateSummary({
      reason: 'FUTURE_DUPLICATE_REASON',
      repair_packet_id: null
    });
    expect(summary).toBe('Not provided · No repair packet · Existing repair');
    expect(summary).not.toMatch(/[A-Z]+_[A-Z_]+/);
  });

  it('treats an empty status as unprovided', () => {
    expect(
      duplicateSummary({
        reason: 'OPEN_REPAIR_ON_MACHINE',
        status: '',
        repair_packet_id: null
      })
    ).toContain('Not provided');
  });
});
