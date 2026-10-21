import { describe, expect, it } from 'vitest';

import { readReturnPath, returnState } from './returnPath';

describe('return path', () => {
  it('round-trips an in-app path and label', () => {
    expect(
      readReturnPath(
        returnState('/machines/machine/18/parts/?slot=1', 'Pump 01 · Parts')
      )
    ).toEqual({
      to: '/machines/machine/18/parts/?slot=1',
      label: 'Pump 01 · Parts'
    });
  });

  it('ignores missing or malformed state', () => {
    for (const state of [
      null,
      undefined,
      {},
      { returnTo: null },
      { returnTo: { to: 3, label: 'x' } },
      { returnTo: { to: '/a' } },
      { returnTo: { to: '/a', label: '   ' } }
    ])
      expect(readReturnPath(state)).toBeNull();
  });

  it('never leads off the app', () => {
    for (const to of [
      'https://example.com/',
      '//example.com/x',
      'javascript:alert(1)',
      'part/1/',
      '/\\example.com'
    ])
      expect(readReturnPath({ returnTo: { to, label: 'Back' } })).toBeNull();
  });
});
