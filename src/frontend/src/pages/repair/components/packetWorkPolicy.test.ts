import { describe, expect, it } from 'vitest';
import {
  type LifecycleAvailability,
  lifecycleActionDisabled
} from './packetWorkPolicy';

const available: LifecycleAvailability = {
  permitted: true,
  ready: true,
  workVersion: 3,
  readinessVersion: 3,
  workError: false,
  readinessError: false,
  workFetching: false,
  readinessFetching: false,
  storageFailed: false,
  busy: false
};

describe('packet Work action availability', () => {
  it('enables only an authorized, current and ready action', () => {
    expect(lifecycleActionDisabled(available)).toBe(false);
  });
  it.each([
    { permitted: false },
    { ready: false },
    { workVersion: undefined },
    { readinessVersion: undefined },
    { readinessVersion: 2 },
    { workError: true },
    { readinessError: true },
    { workFetching: true },
    { readinessFetching: true },
    { storageFailed: true },
    { busy: true }
  ])('fails closed for %j', (change) => {
    expect(lifecycleActionDisabled({ ...available, ...change })).toBe(true);
  });
});
