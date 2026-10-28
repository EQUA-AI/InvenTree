import { describe, expect, it } from 'vitest';
import { matchesPacketWorkReceipt } from './packetWorkReceipt';

const command = {
  url: '/api/repair/packets/37/work-transition/',
  body: {
    expected_version: 2,
    idempotency_key: 'native-operation',
    to_status: 'planned'
  }
};
const receipt = {
  work_order_id: 3860,
  command: 'transition',
  event_id: 10,
  idempotency_key: 'native-operation',
  lifecycle_version: 3,
  lifecycle_status: 'planned'
};

describe('packet-owned native command receipts', () => {
  it('accepts the exact canonical intermediate transition receipt', () => {
    expect(matchesPacketWorkReceipt(receipt, command, 37, 3860)).toBe(true);
  });
  it('accepts the additive native start result without trusting the packet alone', () => {
    const start = { ...command, url: '/api/repair/packets/37/start/' };
    const response = {
      pk: 37,
      ok: true,
      work_order_id: 3860,
      result: { ...receipt, lifecycle_status: 'in_progress' }
    };
    expect(matchesPacketWorkReceipt(response, start, 37, 3860)).toBe(true);
    expect(
      matchesPacketWorkReceipt(
        { ...response, result: undefined },
        start,
        37,
        3860
      )
    ).toBe(false);
    expect(
      matchesPacketWorkReceipt({ ...response, pk: 38 }, start, 37, 3860)
    ).toBe(false);
  });
  it.each([
    { work_order_id: 999 },
    { command: 'complete' },
    { event_id: 0 },
    { event_id: '10' },
    { idempotency_key: 'another-operation' },
    { lifecycle_version: 4 },
    { lifecycle_status: 'completed' }
  ])('rejects a changed receipt %j', (change) => {
    expect(
      matchesPacketWorkReceipt({ ...receipt, ...change }, command, 37, 3860)
    ).toBe(false);
  });
  it.each([null, undefined, false, 'accepted', {}])(
    'does not confirm malformed %j',
    (response) => {
      expect(matchesPacketWorkReceipt(response, command, 37, 3860)).toBe(false);
    }
  );
});
