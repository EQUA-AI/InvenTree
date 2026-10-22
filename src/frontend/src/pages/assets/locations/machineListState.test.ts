import { describe, expect, it } from 'vitest';

import {
  countSummary,
  machineDetailHref,
  machineEmptyKind,
  machineLocationId,
  machinesReturnHref
} from './locationTree';
import {
  MACHINE_PAGE_SIZE,
  machineCriticality,
  machineListParams,
  machineListState,
  machineLocationParams
} from './machineListState';

describe('machine list URL state', () => {
  it('defaults only unsupported or absent state', () => {
    expect(machineListState(new URLSearchParams())).toEqual({
      showInactive: false,
      showArchived: false,
      search: '',
      alarmsOnly: false,
      criticality: '',
      page: 1
    });
    expect(
      machineListState(
        new URLSearchParams('search=S1+%26+pump&has_alarms=true&page=2')
      )
    ).toEqual({
      showInactive: false,
      showArchived: false,
      search: 'S1 & pump',
      alarmsOnly: true,
      criticality: '',
      page: 2
    });
  });

  it.each([
    '0',
    '-1',
    '1.5',
    '1e3',
    'NaN',
    'Infinity',
    '9007199254740992',
    '360287970189640'
  ])('rejects unsafe page %s', (page) => {
    expect(machineListState(new URLSearchParams({ page })).page).toBe(1);
  });

  it('keeps valid offsets exactly representable', () => {
    const page = Math.floor(Number.MAX_SAFE_INTEGER / MACHINE_PAGE_SIZE);
    expect(
      machineListState(new URLSearchParams({ page: String(page) })).page
    ).toBe(page);
    expect(Number.isSafeInteger((page - 1) * MACHINE_PAGE_SIZE)).toBe(true);
  });

  it('resets page on search or alarm changes without discarding location or unrelated URL state', () => {
    const origin = new URLSearchParams(
      'location=12&scope=direct&search=pump&has_alarms=true&page=3&other=retained'
    );
    const searched = machineListParams(origin, { search: 'S1 & pump' });
    expect(machineListState(searched)).toEqual({
      showInactive: false,
      showArchived: false,
      search: 'S1 & pump',
      alarmsOnly: true,
      criticality: '',
      page: 1
    });
    expect(searched.get('location')).toBe('12');
    expect(searched.get('scope')).toBe('direct');
    expect(searched.get('other')).toBe('retained');
    expect(
      machineListState(machineListParams(origin, { alarmsOnly: false })).page
    ).toBe(1);
    expect(origin.get('page')).toBe('3');
  });

  it('preserves page on no-op filters and changes only page for pagination', () => {
    const origin = new URLSearchParams('search=pump&has_alarms=true&page=2');
    expect(machineListParams(origin, { search: 'pump' }).get('page')).toBe('2');
    expect(machineListState(machineListParams(origin, { page: 3 }))).toEqual({
      showInactive: false,
      showArchived: false,
      search: 'pump',
      alarmsOnly: true,
      criticality: '',
      page: 3
    });
  });

  it('restores a complete state atomically and omits default query values', () => {
    const restored = machineListParams(new URLSearchParams(), {
      showInactive: false,
      showArchived: false,
      search: 'pump',
      alarmsOnly: true,
      page: 2
    });
    expect(restored.toString()).toBe('search=pump&has_alarms=true&page=2');
    expect(
      machineListParams(restored, {
        showInactive: false,
        showArchived: false,
        search: '',
        alarmsOnly: false,
        page: 1
      }).toString()
    ).toBe('');
  });

  it('starts a newly selected location unfiltered on page one', () => {
    const origin = new URLSearchParams(
      'location=12&scope=direct&search=pump&has_alarms=true&page=2'
    );
    expect(machineLocationParams(origin, 21).toString()).toBe(
      'location=21&scope=direct'
    );
    expect(machineLocationParams(origin, null).has('location')).toBe(false);
  });

  it.each(['sites', 'machines', 'unassigned'] as const)(
    'round-trips supported state for %s without forwarding unrelated detail parameters',
    (source) => {
      const list = {
        showInactive: false,
        showArchived: false,
        search: 'S1 & pump',
        alarmsOnly: true,
        criticality: 'critical' as const,
        page: 2
      };
      const href = machineDetailHref(26, {
        source,
        location: source === 'sites' ? 12 : null,
        direct: source === 'sites',
        list
      });
      const detail = new URL(href, 'https://example.invalid');
      expect(machineListState(detail.searchParams)).toEqual(list);
      detail.searchParams.set('demo_session', 'retired');
      detail.searchParams.set('unrecognized', 'not-forwarded');
      const returned = new URL(
        machinesReturnHref(detail.searchParams),
        detail.origin
      );
      expect(returned.pathname).toBe(`/machines/index/${source}/`);
      expect(machineListState(returned.searchParams)).toEqual(list);
      expect(returned.searchParams.get('location')).toBe(
        source === 'sites' ? '12' : null
      );
      expect(returned.searchParams.has('demo_session')).toBe(false);
      expect(returned.searchParams.has('unrecognized')).toBe(false);
      expect(returned.searchParams.has('from')).toBe(false);
    }
  );

  it('keeps unknown source on the native legacy return route with supported list state', () => {
    expect(
      machinesReturnHref(
        new URLSearchParams('from=unknown&location=12&search=pump&page=2')
      )
    ).toBe('/machines/index/?location=12&search=pump&page=2');
    expect(
      machinesReturnHref(
        new URLSearchParams(
          'from=machines&location=12&scope=direct&page=bad&has_alarms=unknown'
        )
      )
    ).toBe('/machines/index/machines/');
  });

  it.each(['0', '-1', '1.5', '1e3', '9007199254740992'])(
    'rejects invalid location %s instead of rounding to another location',
    (location) => {
      expect(machineLocationId(new URLSearchParams({ location }))).toBeNull();
    }
  );

  it('labels alarm-filtered cards as location totals, not filtered results', () => {
    const summary = countSummary(
      { direct_machines: 60, total_machines: 80 },
      { direct: true, searched: false, alarmsOnly: true }
    );
    expect(summary).toEqual({
      view: 60,
      direct: 60,
      showDirectBreakdown: false,
      locationTotalsOnly: true
    });
  });

  it('reads only declared criticality levels from the link', () => {
    expect(
      machineListState(new URLSearchParams('criticality=high')).criticality
    ).toBe('high');
    for (const value of ['CRITICAL', 'severe', 'none', ''])
      expect(
        machineListState(new URLSearchParams({ criticality: value }))
          .criticality
      ).toBe('');
    expect(machineCriticality('medium')).toBe('medium');
    expect(machineCriticality(null)).toBeNull();
    expect(machineCriticality(['low'])).toBeNull();
  });

  it('resets page on a criticality change and clears it with a new location', () => {
    const origin = new URLSearchParams('location=12&criticality=high&page=3');
    const changed = machineListParams(origin, { criticality: 'critical' });
    expect(changed.get('criticality')).toBe('critical');
    expect(changed.has('page')).toBe(false);
    expect(
      machineListParams(origin, { criticality: '' }).has('criticality')
    ).toBe(false);
    expect(machineLocationParams(origin, 21).toString()).toBe('location=21');
  });

  it('labels criticality-filtered cards as location totals and empty results as filter misses', () => {
    expect(
      countSummary(
        { direct_machines: 60, total_machines: 80 },
        { direct: false, searched: false, filtered: true }
      ).locationTotalsOnly
    ).toBe(true);
    const empty = {
      searched: false,
      unassigned: false,
      locationSelected: true,
      includeDescendants: true
    };
    expect(machineEmptyKind({ ...empty, filtered: true })).toBe(
      'no-filter-matches'
    );
    expect(
      machineEmptyKind({ ...empty, filtered: true, alarmsOnly: true })
    ).toBe('no-filter-matches');
    expect(machineEmptyKind({ ...empty, alarmsOnly: true })).toBe(
      'no-machines-in-alarm'
    );
  });
});

describe('machine activity scope', () => {
  it('defaults to active-only and preserves an explicit inclusion through navigation', () => {
    expect(machineListState(new URLSearchParams()).showInactive).toBe(false);
    const params = machineListParams(new URLSearchParams('page=3'), {
      showInactive: true
    });
    expect(params.get('show_inactive')).toBe('true');
    expect(params.get('page')).toBeNull();
    expect(
      machineListState(machineLocationParams(params, 12)).showInactive
    ).toBe(true);
    expect(
      machineListParams(params, { showInactive: false }).has('show_inactive')
    ).toBe(false);
  });
});

it('carries explicit inactive and archived scope into a machine and back', () => {
  const scope = machineListState(
    new URLSearchParams('show_inactive=true&show_archived=true')
  );
  const href = machineDetailHref(26, {
    source: 'sites',
    location: 12,
    direct: false,
    list: scope
  });
  const returned = machinesReturnHref(
    new URL(href, 'https://example.invalid').searchParams
  );
  expect(
    new URL(returned, 'https://example.invalid').searchParams.get(
      'show_archived'
    )
  ).toBe('true');
  expect(
    new URL(returned, 'https://example.invalid').searchParams.get(
      'show_inactive'
    )
  ).toBe('true');
});
