import { describe, expect, it } from 'vitest';
import { matchesWorkPackageReceipt } from './workPackageReceipt';

const request = {
  url: '/api/maintenance/work-packages/create/',
  body: {
    idempotency_key: 'fixture-create',
    machine_id: 102,
    create_repair_packet: true,
    source: { anomaly_id: 501 },
    planning: { assigned_to_id: 27 }
  }
};
const receipt = {
  work_order_id: 73,
  machine_id: 102,
  anomaly_id: 501,
  assigned_to_id: 27,
  repair_packet_id: 28,
  idempotency_key: 'fixture-create'
};

describe('native work-package receipt matching', () => {
  it('accepts only the actual correlated created aggregate', () => {
    expect(matchesWorkPackageReceipt(receipt, request)).toBe(true);
  });
  for (const changed of [
    { machine_id: 103 },
    { anomaly_id: 502 },
    { assigned_to_id: 29 },
    { idempotency_key: 'another' },
    { work_order_id: 0 },
    { work_order_id: '73' },
    { work_order_id: Number.NaN },
    { repair_packet_id: null }
  ]) {
    it(`refuses an unconfirmed identity ${JSON.stringify(changed)}`, () => {
      expect(
        matchesWorkPackageReceipt({ ...receipt, ...changed }, request)
      ).toBe(false);
    });
  }
  it('does not accept an uncorrelated legacy receipt', () => {
    expect(matchesWorkPackageReceipt({ work_order_id: 73 }, request)).toBe(
      false
    );
  });
  it('supports an intentionally packetless manual request', () => {
    expect(
      matchesWorkPackageReceipt(
        {
          ...receipt,
          repair_packet_id: null,
          anomaly_id: null,
          assigned_to_id: null
        },
        {
          ...request,
          body: {
            ...request.body,
            create_repair_packet: false,
            source: {},
            planning: {}
          }
        }
      )
    ).toBe(true);
  });
});
