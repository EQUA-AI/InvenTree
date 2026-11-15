import { describe, expect, it } from 'vitest';

import { placementPending } from './placementPending';

const row = {
  pk: 17,
  client: 3,
  placement_version: 2,
  physical_location: { pk: 8 }
};
const pending = {
  schema_version: 'aimms.placement.sync.v1',
  state: 'pending',
  machine: '17',
  client: '3',
  location: '8',
  native_version: '2',
  source_fence: '5'
};

describe('authorized placement metadata', () => {
  it('stays off unless the returned row carries a matching enrolled placement', () => {
    expect(placementPending(row)).toBeNull();
    expect(placementPending({ ...row, placement_sync: pending })).toBe(
      'pending'
    );
  });

  it('requires exact typed source coordinates before displaying pending or current', () => {
    for (const placement_sync of [
      false,
      {},
      { ...pending, schema_version: 'other' },
      { ...pending, state: 'indexing' },
      { ...pending, machine: '18' },
      { ...pending, client: '4' },
      { ...pending, location: null },
      { ...pending, native_version: '1' },
      { ...pending, source_fence: undefined },
      { ...pending, source_fence: true },
      { ...pending, source_fence: '0' },
      { ...pending, source_fence: '05' },
      { ...pending, native_version: '02' }
    ]) {
      expect(placementPending({ ...row, placement_sync })).toBe('unqualified');
    }
    const current = { ...pending, state: 'current' };
    expect(placementPending({ ...row, placement_sync: current })).toBe(
      'current'
    );
    expect(
      placementPending({
        ...row,
        physical_location: null,
        placement_sync: { ...current, location: null }
      })
    ).toBe('current');
    expect(
      placementPending({
        ...row,
        placement_sync: { ...current, source_fence: '9007199254740993' }
      })
    ).toBe('current');
  });
});
