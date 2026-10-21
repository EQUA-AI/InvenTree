import { describe, expect, it } from 'vitest';

import {
  workAttentionReasons,
  workListParams,
  workListPreferenceKey,
  workListRequest,
  workListState,
  workPersonaScope
} from './workListState';

describe('native Work-list state', () => {
  it('takes explicit URL scope before saved choice', () => {
    expect(
      workListState(new URLSearchParams('work_scope=all'), 'mine')?.scope
    ).toBe('all');
    expect(workListState(new URLSearchParams(), 'mine')?.scope).toBe('mine');
  });

  it.each([
    'work_scope=manager',
    'priority=critical',
    'lifecycle_status=running',
    'work_order_type=unknown'
  ])('does not silently broaden invalid filters: %s', (query) => {
    expect(workListState(new URLSearchParams(query), 'mine')).toBeNull();
  });

  it.each(['0', '-1', '1.5', 'Infinity', '9007199254740992'])(
    'normalizes invalid page %s',
    (page) => {
      expect(
        workListState(new URLSearchParams({ work_page: page }), 'all')?.page
      ).toBe(1);
    }
  );

  it('round-trips exact supported filters without replacing unrelated parameters', () => {
    const params = new URLSearchParams(
      'work_scope=mine&search=pump+%26+seal&priority=high&lifecycle_status=on_hold&work_order_type=corrective&work_page=2'
    );
    const state = workListState(params, 'all')!;
    const restored = workListParams(
      new URLSearchParams('other=retained'),
      state
    );
    expect(workListState(restored, 'all')).toEqual(state);
    expect(restored.get('other')).toBe('retained');
    expect(
      workListParams(params, {
        ...state,
        page: 1,
        search: '',
        priority: '',
        lifecycle: '',
        workType: ''
      }).toString()
    ).toBe('work_scope=mine');
  });

  it('scopes My work to the authenticated typed user, not a URL-selected person', () => {
    const state = workListState(
      new URLSearchParams('work_scope=mine&assigned_to=999&work_page=2'),
      'all'
    )!;
    expect(workListRequest(state, 11)).toEqual({
      assigned_to: 11,
      search: '',
      priority: undefined,
      lifecycle_status: undefined,
      work_order_type: undefined,
      limit: 25,
      offset: 25
    });
    expect(
      workListRequest({ ...state, scope: 'all' }, 11).assigned_to
    ).toBeUndefined();
  });

  it('separates saved view choices by user and host without treating them as authority', () => {
    expect(workListPreferenceKey('plant-a', 11)).not.toBe(
      workListPreferenceKey('plant-b', 11)
    );
    expect(workListPreferenceKey('plant-a', 11)).not.toBe(
      workListPreferenceKey('plant-a', 12)
    );
    expect(workListPreferenceKey('', 11)).toBe('aimms-work-list:local:11');
  });
});

describe('Needs attention scope', () => {
  it('is a supported URL and saved scope that asks the server for its rule', () => {
    const state = workListState(
      new URLSearchParams('work_scope=attention'),
      'all'
    )!;
    expect(state.scope).toBe('attention');
    expect(workListState(new URLSearchParams(), 'attention')?.scope).toBe(
      'attention'
    );
    const request = workListRequest(state, 11);
    expect(request.attention).toBe('true');
    expect(request.assigned_to).toBeUndefined();
    expect(
      workListRequest({ ...state, scope: 'all' }, 11).attention
    ).toBeUndefined();
    expect(
      workListRequest({ ...state, scope: 'mine' }, 11).attention
    ).toBeUndefined();
  });

  it('keeps known reasons in display order and drops anything else', () => {
    expect(
      workAttentionReasons([
        'no_owner',
        'risk_score',
        'high_priority',
        'overdue'
      ])
    ).toEqual(['high_priority', 'overdue', 'no_owner']);
    expect(workAttentionReasons(null)).toEqual([]);
    expect(workAttentionReasons('overdue')).toEqual([]);
  });
});

describe('first-use persona scope', () => {
  it('starts supervisors on Needs attention and technicians on My work', () => {
    expect(
      workPersonaScope({
        rule: 'work_persona.v1',
        persona: 'supervisor',
        default_scope: 'attention'
      })
    ).toBe('attention');
    expect(
      workPersonaScope({ persona: 'technician', default_scope: 'mine' })
    ).toBe('mine');
  });

  it.each([
    undefined,
    null,
    'attention',
    {},
    { persona: null, default_scope: 'all' },
    { default_scope: 'manager' },
    { default_scope: ['attention'] }
  ])('falls back to the neutral All work for %j', (value) => {
    expect(workPersonaScope(value)).toBe('all');
  });

  it('is outranked by a scope in the link', () => {
    const persona = workPersonaScope({ default_scope: 'attention' });
    expect(
      workListState(new URLSearchParams('work_scope=mine'), persona)?.scope
    ).toBe('mine');
  });
});
