import { describe, expect, it } from 'vitest';
import {
  recordedCertificateAvailable,
  savedCalibrationRevision
} from './calibrationCommand';

describe('calibration saved response boundary', () => {
  const row = {
    id: 7,
    work_order: 73,
    reading: 701,
    revision: 2,
    recorded_by: 11,
    recorded_at: '2026-10-20T12:00:00Z'
  };
  it('accepts the exact attributed next revision', () => {
    expect(savedCalibrationRevision({ traceability: row }, 73, 701, 1)).toBe(
      true
    );
  });
  it.each([
    { work_order: 74 },
    { reading: 702 },
    { revision: 1 },
    { revision: 3 },
    { id: null },
    { recorded_by: undefined },
    { recorded_by: 0 },
    { recorded_by: -1 },
    { recorded_at: 'invalid' }
  ])(
    'rejects foreign, stale, skipped or unattributed receipts: %j',
    (change) => {
      expect(
        savedCalibrationRevision(
          { traceability: { ...row, ...change } },
          73,
          701,
          1
        )
      ).toBe(false);
    }
  );
  it.each([null, {}, { traceability: null }, { accepted: true }])(
    'does not turn an incomplete response into success: %j',
    (value) => {
      expect(savedCalibrationRevision(value, 73, 701, 1)).toBe(false);
    }
  );
});

describe('recorded certificate byte identity', () => {
  const file = {
    certificate_available: true,
    certificate_url: '/media/synthetic-certificate.pdf',
    certificate_integrity_state: 'unchanged' as const
  };
  it('offers only a server-verified unchanged certificate', () => {
    expect(recordedCertificateAvailable(file)).toBe(true);
  });
  it.each([
    undefined,
    { ...file, certificate_available: false },
    { ...file, certificate_url: null },
    { ...file, certificate_integrity_state: undefined },
    { ...file, certificate_integrity_state: 'changed' as const },
    { ...file, certificate_integrity_state: 'unavailable' as const },
    { ...file, certificate_integrity_state: 'identity_not_recorded' as const }
  ])(
    'does not offer or preselect an unavailable/unverified certificate: %j',
    (row) => {
      expect(recordedCertificateAvailable(row)).toBe(false);
    }
  );
});
