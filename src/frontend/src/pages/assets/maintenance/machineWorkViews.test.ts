import { describe, expect, it } from 'vitest';

import {
  machineOpenWorkHref,
  machineWorkCounts,
  machineWorkParams,
  machineWorkState,
  openCount,
  openWorkRequest
} from './machineWorkViews';

const summary = (open: unknown, completed: unknown = { count: 4 }) => ({
  rule: 'machine_work_views.v1',
  machine: 18,
  open_statuses: ['planned', 'ready', 'in_progress', 'on_hold', 'verifying'],
  open,
  completed
});

describe('machineWorkState', () => {
  it('defaults to open work, page one', () => {
    expect(machineWorkState(new URLSearchParams())).toEqual({
      view: 'open',
      search: '',
      page: 1
    });
  });

  it('reads the view, search and page beside other parameters', () => {
    expect(
      machineWorkState(
        new URLSearchParams(
          'from=machines&search=pump&work_view=open&work_search=WO-7&work_page=3'
        )
      )
    ).toEqual({ view: 'open', search: 'WO-7', page: 3 });
  });

  it('refuses a view or page it does not have', () => {
    for (const query of [
      'work_view=all',
      'work_page=0',
      'work_page=-1',
      'work_page=2.5',
      'work_page=x'
    ])
      expect(machineWorkState(new URLSearchParams(query))).toBeNull();
  });
});

describe('machineWorkParams', () => {
  it('keeps the Machines list return state and drops defaults', () => {
    const next = machineWorkParams(
      new URLSearchParams('from=machines&search=pump&work_page=4'),
      { view: 'open', search: '', page: 1 }
    );
    expect(next.toString()).toBe('from=machines&search=pump');
  });

  it('writes a completed view without open-list state', () => {
    const next = machineWorkParams(new URLSearchParams('from=sites'), {
      view: 'completed',
      search: 'ignored',
      page: 5
    });
    expect(next.toString()).toBe('from=sites&work_view=completed');
  });
});

describe('openWorkRequest', () => {
  it('narrows the native list to this machine and its open set', () => {
    expect(openWorkRequest(18, { search: '  bearing ', page: 3 })).toEqual({
      machine: 18,
      lifecycle_group: 'open',
      limit: 25,
      offset: 50,
      search: 'bearing'
    });
  });

  it('sends no empty search', () => {
    expect(openWorkRequest(18, { search: ' ', page: 1 })).not.toHaveProperty(
      'search'
    );
  });
});

describe('machineWorkCounts', () => {
  it('reads both counts and the open states', () => {
    const counts = machineWorkCounts(
      summary({ count: 3, highest_priority: 'high' })
    );
    expect(counts.open).toEqual({ kind: 'open', count: 3, priority: 'high' });
    expect(counts.completed).toEqual({ kind: 'known', count: 4 });
    expect(counts.openStatuses).toHaveLength(5);
    expect(openCount(counts.open)).toBe(3);
  });

  it('keeps an explicit zero apart from an unknown count', () => {
    const none = machineWorkCounts(
      summary({ count: 0, highest_priority: null })
    );
    expect(openCount(none.open)).toBe(0);
    const unknown = machineWorkCounts(summary(null));
    expect(unknown.open).toEqual({ kind: 'unavailable' });
    expect(openCount(unknown.open)).toBeNull();
    expect(unknown.completed).toEqual({ kind: 'known', count: 4 });
  });

  it('treats anything malformed or unversioned as not available', () => {
    const cases: [unknown, 'known' | 'unavailable'][] = [
      [null, 'unavailable'],
      [[], 'unavailable'],
      ['x', 'unavailable'],
      [
        { ...summary({ count: 1, highest_priority: 'low' }), rule: 'other' },
        'unavailable'
      ],
      [
        summary({ count: -1, highest_priority: null }, { count: -2 }),
        'unavailable'
      ],
      [
        summary({ count: 1.5, highest_priority: 'low' }, { count: 'many' }),
        'unavailable'
      ],
      [summary({ count: 2, highest_priority: 'urgent' }), 'known']
    ];
    for (const [value, completed] of cases) {
      const counts = machineWorkCounts(value);
      expect(openCount(counts.open)).toBeNull();
      expect(counts.completed.kind).toBe(completed);
    }
  });
});

describe('machineOpenWorkHref', () => {
  it('opens the Maintenance tab and keeps the link return state', () => {
    expect(
      machineOpenWorkHref('/machines/machine/7/?from=machines&search=pump')
    ).toBe('/machines/machine/7/maintenance/?from=machines&search=pump');
    expect(machineOpenWorkHref('/machines/machine/7/')).toBe(
      '/machines/machine/7/maintenance/'
    );
  });

  it('starts the open view fresh rather than on an old page', () => {
    expect(
      machineOpenWorkHref(
        '/machines/machine/7?from=sites&work_page=9&work_view=completed'
      )
    ).toBe('/machines/machine/7/maintenance/?from=sites');
  });
});
