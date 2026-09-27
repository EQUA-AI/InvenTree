/*
 * Pure hierarchy/navigation/count/return-route logic for the physical
 * location workspace (packages C, D and machine E of the approved plan).
 * Node-only: no DOM, no React. Behaviour contracts, not source regexes.
 */
import { describe, expect, it } from 'vitest';

import {
  BRANCH_PAGE_LIMIT,
  REVEAL_MAX_PAGES,
  branchHasMore,
  branchPageParams,
  concatBranchPages,
  countSummary,
  isNodeValue,
  locationKind,
  machineDetailHref,
  machineEmptyKind,
  machineSourceView,
  machinesReturnHref,
  missingRevealEdge,
  nodeValue,
  revealExpandTargets,
  revealPageBump,
  revealPaging,
  workspaceLabel
} from './locationTree';

describe('locationKind', () => {
  it('normalizes backend kind values case-insensitively', () => {
    expect(locationKind('site')).toBe('site');
    expect(locationKind('AREA')).toBe('area');
    expect(locationKind('Production Line') === 'line').toBe(false);
    expect(locationKind('line')).toBe('line');
  });

  it('falls back to other for unknown or missing kinds', () => {
    expect(locationKind('spaceship')).toBe('other');
    expect(locationKind(null)).toBe('other');
    expect(locationKind(undefined)).toBe('other');
    expect(locationKind('')).toBe('other');
  });
});

describe('tree node values', () => {
  it('formats and validates node values', () => {
    expect(nodeValue(12)).toBe('12');
    expect(isNodeValue('12')).toBe(true);
    expect(isNodeValue('0')).toBe(false);
    expect(isNodeValue('-3')).toBe(false);
    expect(isNodeValue('pending-3')).toBe(false);
    expect(isNodeValue('')).toBe(false);
    expect(isNodeValue(null)).toBe(false);
  });
});

describe('branch pages', () => {
  it('concatenates loaded pages in order and deduplicates by pk', () => {
    const pages: ({ pk: number }[] | undefined)[] = [
      [{ pk: 1 }, { pk: 2 }],
      [{ pk: 2 }, { pk: 3 }],
      undefined
    ];
    expect(concatBranchPages(pages).map((row) => row.pk)).toEqual([1, 2, 3]);
  });

  it('reports continuation from the last loaded page only', () => {
    expect(branchHasMore([{ next: 'x' }, { next: null }])).toBe(false);
    expect(branchHasMore([{ next: 'x' }, { next: 'y' }])).toBe(true);
    expect(branchHasMore([{ next: 'x' }, undefined])).toBe(true);
    expect(branchHasMore([])).toBe(false);
  });

  it('builds offset paging params per branch', () => {
    expect(branchPageParams('root', 0)).toEqual({
      parent: 'root',
      limit: BRANCH_PAGE_LIMIT,
      offset: 0
    });
    expect(branchPageParams('11', 2)).toEqual({
      parent: '11',
      limit: BRANCH_PAGE_LIMIT,
      offset: 2 * BRANCH_PAGE_LIMIT
    });
  });
});

describe('missingRevealEdge', () => {
  const path = [{ pk: 1 }, { pk: 11 }, { pk: 150 }];

  it('returns null for empty or single-node paths', () => {
    expect(missingRevealEdge([], { root: [1] })).toBeNull();
    expect(missingRevealEdge([{ pk: 1 }], { root: [1] })).toBeNull();
  });

  it('finds the first edge whose child is not loaded yet', () => {
    expect(missingRevealEdge(path, { root: [1], '1': [11] })).toEqual({
      parent: '11',
      childPk: 150
    });
    expect(missingRevealEdge(path, { root: [2] })).toEqual({
      parent: 'root',
      childPk: 1
    });
    expect(
      missingRevealEdge(path, { root: [1], '1': [11], '11': [150] })
    ).toBeNull();
  });

  it('treats absent branch data as unloaded', () => {
    expect(missingRevealEdge(path, {})).toEqual({ parent: 'root', childPk: 1 });
    expect(missingRevealEdge(path, { root: [1] })).toEqual({
      parent: '1',
      childPk: 11
    });
  });
});

describe('revealPageBump', () => {
  it('pages forward until the reveal bound is reached', () => {
    expect(revealPageBump(1)).toBe(2);
    expect(revealPageBump(REVEAL_MAX_PAGES - 1)).toBe(REVEAL_MAX_PAGES);
    expect(revealPageBump(REVEAL_MAX_PAGES)).toBeNull();
    expect(revealPageBump(REVEAL_MAX_PAGES + 3)).toBeNull();
  });
});

describe('revealPaging', () => {
  const expanded = { root: true, '1': true, '11': true };
  const settled = {
    hasMore: true,
    isFetching: false,
    loadedPages: 1,
    requestedPages: 1
  };

  it('never pages a branch the user collapsed', () => {
    expect(
      revealPaging({
        parent: '11',
        expanded: { root: true, '1': true, '11': false },
        branch: settled,
        currentPages: 1
      })
    ).toEqual({ action: 'skip' });
  });

  it('waits for in-flight branch pages instead of stacking requests', () => {
    expect(
      revealPaging({
        parent: '11',
        expanded,
        branch: { ...settled, isFetching: true },
        currentPages: 1
      })
    ).toEqual({ action: 'wait' });
    expect(
      revealPaging({
        parent: '11',
        expanded,
        branch: { ...settled, loadedPages: 1, requestedPages: 2 },
        currentPages: 2
      })
    ).toEqual({ action: 'wait' });
    expect(
      revealPaging({
        parent: '11',
        expanded,
        branch: undefined,
        currentPages: 1
      })
    ).toEqual({ action: 'wait' });
  });

  it('pages forward while the server reports continuation', () => {
    expect(
      revealPaging({ parent: '11', expanded, branch: settled, currentPages: 1 })
    ).toEqual({ action: 'page', pages: 2 });
    expect(
      revealPaging({
        parent: 'root',
        expanded: {},
        branch: settled,
        currentPages: 1
      })
    ).toEqual({ action: 'page', pages: 2 });
  });

  it('stops at an exhausted branch without requesting more pages', () => {
    expect(
      revealPaging({
        parent: '11',
        expanded,
        branch: { ...settled, hasMore: false },
        currentPages: 1
      })
    ).toEqual({ action: 'unavailable', reason: 'server-exhausted' });
  });

  it('reports the page bound as unavailable instead of paging past it', () => {
    expect(
      revealPaging({
        parent: '11',
        expanded,
        branch: settled,
        currentPages: REVEAL_MAX_PAGES
      })
    ).toEqual({ action: 'unavailable', reason: 'page-bound' });
  });
});

describe('revealExpandTargets', () => {
  const ancestors = ['1', '11'];

  it('restores reveal ancestors dropped by a partial tree rebuild', () => {
    expect(revealExpandTargets(ancestors, {})).toEqual(['1', '11']);
    expect(revealExpandTargets(ancestors, { '1': true })).toEqual(['11']);
    expect(revealExpandTargets(ancestors, { '1': true, '11': true })).toEqual(
      []
    );
  });

  it('treats controller default-false keys as restorable', () => {
    expect(revealExpandTargets(ancestors, { '1': false, '11': false })).toEqual(
      ['1', '11']
    );
    expect(revealExpandTargets(ancestors, { '1': true, '11': false })).toEqual([
      '11'
    ]);
  });
});

describe('countSummary', () => {
  const counts = { direct_machines: 5, total_machines: 9 };

  it('matches the active table scope', () => {
    expect(countSummary(counts, { direct: true, searched: false })).toEqual({
      view: 5,
      direct: 5,
      showDirectBreakdown: false,
      locationTotalsOnly: false
    });
    expect(countSummary(counts, { direct: false, searched: false })).toEqual({
      view: 9,
      direct: 5,
      showDirectBreakdown: true,
      locationTotalsOnly: false
    });
  });

  it('labels totals as location totals while a search is active', () => {
    const summary = countSummary(counts, { direct: false, searched: true });
    expect(summary.locationTotalsOnly).toBe(true);
    expect(summary.view).toBe(9);
  });

  it('keeps unknown counts distinct from numeric zero', () => {
    expect(countSummary(undefined, { direct: false, searched: false })).toEqual(
      {
        view: null,
        direct: null,
        showDirectBreakdown: true,
        locationTotalsOnly: false
      }
    );
  });
});

describe('machine return routes', () => {
  const session = '123e4567-e89b-12d3-a456-426614174000';

  it('validates the source view parameter', () => {
    expect(machineSourceView(new URLSearchParams('from=sites'))).toBe('sites');
    expect(machineSourceView(new URLSearchParams('from=machines'))).toBe(
      'machines'
    );
    expect(machineSourceView(new URLSearchParams('from=unassigned'))).toBe(
      'unassigned'
    );
    expect(machineSourceView(new URLSearchParams('from=bogus'))).toBeNull();
    expect(machineSourceView(new URLSearchParams(''))).toBeNull();
  });

  it('carries the source view and filters into the machine link', () => {
    expect(
      machineDetailHref(5, {
        session,
        location: 12,
        direct: true,
        source: 'sites'
      })
    ).toBe(
      `/machines/machine/5/?from=sites&demo_session=${session}&location=12&scope=direct`
    );
    expect(
      machineDetailHref(7, {
        session: null,
        location: null,
        direct: false,
        source: 'machines'
      })
    ).toBe('/machines/machine/7/?from=machines');
  });

  it('returns location-origin visits to the sites panel with filters', () => {
    const params = new URLSearchParams(
      `from=sites&demo_session=${session}&location=12&scope=direct`
    );
    expect(machinesReturnHref(params)).toBe(
      `/machines/index/sites/?demo_session=${session}&location=12&scope=direct`
    );
  });

  it('returns All Machines and Unassigned visits to their own panels', () => {
    expect(
      machinesReturnHref(
        new URLSearchParams(`from=machines&demo_session=${session}`)
      )
    ).toBe(`/machines/index/machines/?demo_session=${session}`);
    expect(machinesReturnHref(new URLSearchParams('from=unassigned'))).toBe(
      '/machines/index/unassigned/'
    );
  });

  it('preserves the legacy breadcrumb contract without a source view', () => {
    // Byte-compatible with the pre-existing demo link tests.
    expect(
      machinesReturnHref(
        new URLSearchParams(`demo_session=${session}&location=12&scope=direct`)
      )
    ).toBe(`/machines/index/?demo_session=${session}&location=12&scope=direct`);
    expect(machinesReturnHref(new URLSearchParams(''))).toBe(
      '/machines/index/'
    );
    expect(
      machinesReturnHref(new URLSearchParams('from=bogus&location=12'))
    ).toBe('/machines/index/?location=12');
  });
});

describe('machineEmptyKind', () => {
  it('distinguishes search misses from empty scopes', () => {
    expect(
      machineEmptyKind({
        searched: true,
        unassigned: false,
        locationSelected: true,
        includeDescendants: true
      })
    ).toBe('no-search-matches');
    expect(
      machineEmptyKind({
        searched: false,
        unassigned: true,
        locationSelected: false,
        includeDescendants: true
      })
    ).toBe('no-unassigned-machines');
    expect(
      machineEmptyKind({
        searched: false,
        unassigned: false,
        locationSelected: true,
        includeDescendants: false
      })
    ).toBe('no-direct-machines');
    expect(
      machineEmptyKind({
        searched: false,
        unassigned: false,
        locationSelected: true,
        includeDescendants: true
      })
    ).toBe('no-machines-here');
    expect(
      machineEmptyKind({
        searched: false,
        unassigned: false,
        locationSelected: false,
        includeDescendants: true
      })
    ).toBe('no-machines-here');
  });
});

describe('workspaceLabel', () => {
  const workspaces = [
    { pk: 1, name: 'North client' },
    { pk: 2, name: 'South client' }
  ];

  it('disambiguates only when multiple workspaces are authorized', () => {
    expect(workspaceLabel(1, workspaces)).toBe('North client');
    expect(workspaceLabel(2, workspaces)).toBe('South client');
    expect(workspaceLabel(9, workspaces)).toBeNull();
    expect(workspaceLabel(1, [workspaces[0]])).toBeNull();
  });
});
