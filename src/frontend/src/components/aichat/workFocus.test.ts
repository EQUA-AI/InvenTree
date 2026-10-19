import { describe, expect, it } from 'vitest';

import {
  parseWorkFocusAcknowledgement,
  parseWorkFocusIdentity,
  parseWorkFocusState,
  reloadedWorkFocus,
  workFocusReturnPath
} from './workFocus';

const identity = {
  schema_version: 1,
  kind: 'work_order',
  work_order_id: 73,
  lifecycle_version: 4,
  machine_id: 12
};

const acknowledgement = {
  schema_version: 1,
  thread_id: 'thread-a',
  focus: identity,
  receipt: 'signed.receipt',
  expires_in: 300
};

describe('parseWorkFocusIdentity', () => {
  it('accepts the exact public identity', () => {
    expect(parseWorkFocusIdentity(identity)).toEqual(identity);
    expect(parseWorkFocusIdentity({ ...identity, machine_id: null })).toEqual({
      ...identity,
      machine_id: null
    });
  });

  it.each([
    ['nothing', null],
    ['an array', [identity]],
    ['another schema', { ...identity, schema_version: 2 }],
    ['another kind', { ...identity, kind: 'machine' }],
    ['a string id', { ...identity, work_order_id: '73' }],
    ['a zero id', { ...identity, work_order_id: 0 }],
    ['a fractional version', { ...identity, lifecycle_version: 4.5 }],
    ['an unsafe id', { ...identity, work_order_id: 2 ** 53 }],
    ['a negative machine', { ...identity, machine_id: -1 }],
    ['a missing key', { ...identity, machine_id: undefined }],
    ['the server-side boundary hash', { ...identity, boundary_hash: 'b' }]
  ])('rejects %s', (_label, value) => {
    expect(parseWorkFocusIdentity(value)).toBeNull();
  });
});

describe('parseWorkFocusAcknowledgement', () => {
  it('accepts the exact requested thread, Work and version', () => {
    expect(
      parseWorkFocusAcknowledgement(acknowledgement, 'thread-a', 73, 4)
    ).toEqual(acknowledgement);
  });

  it.each([
    ['another thread', 'thread-b', 73, 4],
    ['another Work', 'thread-a', 74, 4],
    ['another version', 'thread-a', 73, 5],
    ['no thread', '', 73, 4]
  ])('rejects a 200 naming %s', (_label, thread, work, version) => {
    expect(
      parseWorkFocusAcknowledgement(acknowledgement, thread, work, version)
    ).toBeNull();
  });

  it.each([
    ['an empty receipt', { receipt: '' }],
    ['an oversized receipt', { receipt: 'r'.repeat(4097) }],
    ['a numeric receipt', { receipt: 7 }],
    ['a longer lifetime', { expires_in: 301 }],
    ['no lifetime', { expires_in: 0 }],
    ['a fractional lifetime', { expires_in: 2.5 }],
    ['another schema', { schema_version: 2 }],
    ['a malformed focus', { focus: { ...identity, kind: 'machine' } }]
  ])('rejects %s', (_label, change) => {
    expect(
      parseWorkFocusAcknowledgement(
        { ...acknowledgement, ...change },
        'thread-a',
        73,
        4
      )
    ).toBeNull();
  });
});

describe('workFocusReturnPath', () => {
  it('keeps the panel the question was asked from', () => {
    expect(
      workFocusReturnPath(73, '/maintenance/work-orders/73?work_panel=parts')
    ).toBe('/maintenance/work-orders/73?work_panel=parts');
    expect(
      workFocusReturnPath(
        73,
        '/maintenance/work-orders/73/?view=x&work_panel=closeout'
      )
    ).toBe('/maintenance/work-orders/73?work_panel=closeout');
  });

  it('drops the default panel and unknown panels', () => {
    expect(workFocusReturnPath(73, '/maintenance/work-orders/73')).toBe(
      '/maintenance/work-orders/73'
    );
    expect(
      workFocusReturnPath(73, '/maintenance/work-orders/73?work_panel=work')
    ).toBe('/maintenance/work-orders/73');
    expect(
      workFocusReturnPath(73, '/maintenance/work-orders/73?work_panel=admin')
    ).toBe('/maintenance/work-orders/73');
  });

  it.each([
    ['another Work', '/maintenance/work-orders/74?work_panel=parts'],
    ['another page', '/machines/machine/12?work_panel=parts'],
    ['an absolute URL', 'https://example.test/maintenance/work-orders/73'],
    ['a protocol-relative URL', '//example.test/maintenance/work-orders/73'],
    ['a nested path', '/maintenance/work-orders/73/../../admin'],
    ['nothing', undefined]
  ])('falls back to the Work route for %s', (_label, launchPath) => {
    expect(workFocusReturnPath(73, launchPath)).toBe(
      '/maintenance/work-orders/73'
    );
  });
});

describe('reloadedWorkFocus', () => {
  it('derives the Work route from a reloaded identity', () => {
    expect(reloadedWorkFocus(identity)).toEqual({
      identity,
      returnPath: '/maintenance/work-orders/73'
    });
  });

  it('ignores a malformed reloaded value', () => {
    expect(reloadedWorkFocus({ work_order_id: 73 })).toBeNull();
    expect(reloadedWorkFocus(null)).toBeNull();
  });
});

describe('reloaded Work-focus state', () => {
  it.each(['current', 'changed', 'unavailable'])(
    'keeps the served %s state with a valid identity',
    (state) => {
      expect(reloadedWorkFocus(identity, state)).toEqual({
        identity,
        returnPath: '/maintenance/work-orders/73',
        state
      });
    }
  );

  it.each([
    ['an unknown state', 'stale'],
    ['a boolean', true],
    ['a capitalised state', 'Changed'],
    ['nothing', undefined],
    ['null', null]
  ])('drops %s rather than guessing', (_label, state) => {
    expect(reloadedWorkFocus(identity, state)).toEqual({
      identity,
      returnPath: '/maintenance/work-orders/73'
    });
    expect(parseWorkFocusState(state)).toBeUndefined();
  });

  it('never attaches a state to a malformed identity', () => {
    expect(reloadedWorkFocus({ work_order_id: 73 }, 'changed')).toBeNull();
  });
});

describe('P41 request focus', () => {
  const identity = {
    schema_version: 2,
    kind: 'work_order',
    work_order_id: 73,
    lifecycle_version: 7,
    machine_id: 18,
    request_id: 401,
    request_version: 3
  };
  const ack = {
    schema_version: 1,
    thread_id: 'request-thread',
    focus: identity,
    receipt: 'signed-request-receipt',
    expires_in: 300
  };
  it('accepts only the exact request and revision under the exact Work', () => {
    expect(
      parseWorkFocusAcknowledgement(ack, 'request-thread', 73, 7, 401, 3)?.focus
    ).toEqual(identity);
    expect(
      parseWorkFocusAcknowledgement(ack, 'request-thread', 73, 7, 402, 3)
    ).toBeNull();
    expect(
      parseWorkFocusAcknowledgement(ack, 'request-thread', 73, 7, 401, 4)
    ).toBeNull();
    expect(
      parseWorkFocusAcknowledgement(ack, 'request-thread', 73, 7)
    ).toBeNull();
    expect(
      parseWorkFocusAcknowledgement(
        { ...ack, focus: { ...identity, schema_version: 1 } },
        'request-thread',
        73,
        7,
        401,
        3
      )
    ).toBeNull();
  });
  it('rejects half-pairs, additional fields and malformed request ids', () => {
    for (const bad of [undefined, null, true, '401', 0, -1]) {
      expect(
        parseWorkFocusIdentity({ ...identity, request_id: bad })
      ).toBeNull();
      expect(
        parseWorkFocusIdentity({ ...identity, request_version: bad })
      ).toBeNull();
    }
    expect(parseWorkFocusIdentity({ ...identity, finding_id: 12 })).toBeNull();
  });
  it('restores the selected request after reload and validates the Work return', () => {
    expect(reloadedWorkFocus(identity)?.returnPath).toBe(
      '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=401'
    );
    expect(
      workFocusReturnPath(
        73,
        '/maintenance/work-orders/999?work_panel=parts',
        401
      )
    ).toBe(
      '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=401'
    );
  });
});
