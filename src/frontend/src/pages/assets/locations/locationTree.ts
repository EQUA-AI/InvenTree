/*
 * Pure hierarchy/navigation/count/return-route logic for the physical
 * location workspace (plan packages C, D and machine E: M1–M3, U2).
 *
 * No DOM, no React, no Lingui: user-facing labels stay in the components so
 * these contracts remain testable in the Node-only vitest runner
 * (locationTree.test.ts pins the behaviour).
 *
 * Reveal paging/expansion helpers below pin the deep-link reveal contract.
 */

/** Backend location kinds (`assets.location_models.MachineLocation.Kind`). */
export const LOCATION_KINDS = [
  'site',
  'facility',
  'building',
  'area',
  'line',
  'cell',
  'room',
  'other'
] as const;

export type LocationKind = (typeof LOCATION_KINDS)[number];

const KIND_SET: ReadonlySet<string> = new Set<string>(LOCATION_KINDS);

/** Normalized kind for icon/label lookup; anything unknown is `other`. */
export function locationKind(kind: string | null | undefined): LocationKind {
  const normalized = (kind ?? '').trim().toLowerCase();
  return (KIND_SET.has(normalized) ? normalized : 'other') as LocationKind;
}

/** Tree values are numeric pks as strings; placeholders never qualify. */
export function isNodeValue(value: string | null | undefined): boolean {
  return !!value && /^[0-9]+$/.test(value) && Number(value) > 0;
}

export function nodeValue(pk: number): string {
  return String(pk);
}

/** Concatenate loaded branch pages in order, deduplicating repeated pks. */
export function concatBranchPages<T extends { pk: number }>(
  pages: readonly (readonly T[] | undefined | null)[]
): T[] {
  const seen = new Set<number>();
  const rows: T[] = [];
  for (const page of pages) {
    for (const row of page ?? []) {
      if (!seen.has(row.pk)) {
        seen.add(row.pk);
        rows.push(row);
      }
    }
  }
  return rows;
}

/**
 * A branch has more rows when its last loaded page reports continuation.
 * Undefined trailing pages do not count as "no more data".
 */
export function branchHasMore(
  pages: readonly ({ next: string | null } | undefined | null)[]
): boolean {
  for (let i = pages.length - 1; i >= 0; i--) {
    const page = pages[i];
    if (page) return page.next != null;
  }
  return false;
}

export const BRANCH_PAGE_LIMIT = 100;

/** Upper bound on auto-reveal paging so a bad deep link cannot spin forever. */
export const REVEAL_MAX_PAGES = 12;

export function branchPageParams(
  parent: string,
  pageIndex: number
): { parent: string; limit: number; offset: number } {
  return {
    parent,
    limit: BRANCH_PAGE_LIMIT,
    offset: pageIndex * BRANCH_PAGE_LIMIT
  };
}

export interface RevealEdge {
  /** Parent branch value ('root' or a node pk as string). */
  parent: string;
  /** Child pk that must become visible for the selected path. */
  childPk: number;
}

/**
 * First edge of a root→self path whose child is not present in the loaded
 * branch rows yet. Returns null when the whole path is revealable (or the
 * path has nothing to reveal).
 */
export function missingRevealEdge(
  path: readonly { pk: number }[],
  loadedChildren: Readonly<Record<string, readonly number[] | undefined>>
): RevealEdge | null {
  for (let i = 0; i < path.length; i++) {
    const parent = i === 0 ? 'root' : String(path[i - 1].pk);
    const childPk = path[i].pk;
    const loaded = loadedChildren[parent];
    if (!loaded || !loaded.includes(childPk)) return { parent, childPk };
  }
  return null;
}

/** Next page count to request for a branch, or null at the reveal bound. */
export function revealPageBump(currentPages: number): number | null {
  return currentPages < REVEAL_MAX_PAGES ? currentPages + 1 : null;
}

export interface RevealBranchState {
  hasMore: boolean;
  isFetching: boolean;
  loadedPages: number;
  requestedPages: number;
}

export type RevealPagingAction =
  /** The missing edge's parent branch is collapsed: the user hid it on purpose. */
  | { action: 'skip' }
  /** Branch data is still settling; re-check when it lands. */
  | { action: 'wait' }
  /** Request the next branch page. */
  | { action: 'page'; pages: number }
  /**
   * The missing child can no longer be fetched: the server reports the
   * branch complete (`server-exhausted`) or the reveal page bound is reached
   * (`page-bound`). Never request pages past what the server or the bound
   * allows.
   */
  | { action: 'unavailable'; reason: 'server-exhausted' | 'page-bound' };

/**
 * Background reveal paging decision for one missing path edge. Collapsed
 * branches are left alone (selection reveal must not fight the user), and an
 * exhausted server branch stops paging immediately instead of spinning to
 * REVEAL_MAX_PAGES.
 */
export function revealPaging(opts: {
  parent: string;
  expanded: Readonly<Record<string, boolean | undefined>>;
  branch: RevealBranchState | undefined;
  currentPages: number;
}): RevealPagingAction {
  if (opts.parent !== 'root' && !opts.expanded[opts.parent]) {
    return { action: 'skip' };
  }
  const branch = opts.branch;
  if (
    !branch ||
    branch.isFetching ||
    branch.loadedPages < branch.requestedPages
  ) {
    return { action: 'wait' };
  }
  if (!branch.hasMore)
    return { action: 'unavailable', reason: 'server-exhausted' };
  const bumped = revealPageBump(opts.currentPages);
  return bumped == null
    ? { action: 'unavailable', reason: 'page-bound' }
    : { action: 'page', pages: bumped };
}

/**
 * Which reveal ancestors must be forced open when the selection changes.
 *
 * Both a key missing from the map and a key sitting at the tree controller's
 * default `false` (it seeds every data node with one) count as "not
 * expanded" for the reveal.
 */
export function revealExpandTargets(
  ancestors: readonly string[],
  expanded: Readonly<Record<string, boolean | undefined>>
): string[] {
  return ancestors.filter((id) => expanded[id] !== true);
}

export interface MachineCountTotals {
  direct_machines: number;
  total_machines: number;
}

export interface CountSummary {
  /** "Machines in this view" value; null keeps unknown distinct from zero. */
  view: number | null;
  /** Direct-here breakdown value. */
  direct: number | null;
  /** The direct breakdown only renders when it adds information. */
  showDirectBreakdown: boolean;
  /** While a table search is active the cards are location totals. */
  locationTotalsOnly: boolean;
}

export function countSummary(
  counts: MachineCountTotals | undefined | null,
  opts: { direct: boolean; searched: boolean }
): CountSummary {
  return {
    view: counts
      ? opts.direct
        ? counts.direct_machines
        : counts.total_machines
      : null,
    direct: counts ? counts.direct_machines : null,
    showDirectBreakdown: !opts.direct,
    locationTotalsOnly: opts.searched
  };
}

export type MachineSourceView = 'sites' | 'machines' | 'unassigned';

const SOURCE_VIEWS: ReadonlySet<string> = new Set<MachineSourceView>([
  'sites',
  'machines',
  'unassigned'
]);

export function machineSourceView(
  params: URLSearchParams
): MachineSourceView | null {
  const raw = params.get('from');
  return raw && SOURCE_VIEWS.has(raw) ? (raw as MachineSourceView) : null;
}

function validLocation(raw: string | null): number | null {
  return raw && /^[0-9]+$/.test(raw) && Number(raw) > 0 ? Number(raw) : null;
}

export interface MachineLinkScope {
  location: number | null;
  direct: boolean;
  source: MachineSourceView | null;
}

/**
 * Machine links carry the source view and the physical-location filters so the
 * detail page can return to the exact list the visit came from. Without
 * a source the link carries the filters only.
 */
export function machineDetailHref(
  machinePk: number,
  scope: MachineLinkScope
): string {
  const query = new URLSearchParams();
  if (scope.source) query.set('from', scope.source);

  if (scope.location != null) query.set('location', String(scope.location));
  if (scope.direct) query.set('scope', 'direct');
  const search = query.toString();
  return search
    ? `/machines/machine/${machinePk}/?${search}`
    : `/machines/machine/${machinePk}/`;
}

/**
 * Explicit machine→list return routes (U2): location-origin visits go back to
 * the sites panel with their filters; All Machines / Unassigned return to
 * their own panels instead of the remembered one. Without a valid source
 * parameter the legacy `/machines/index/?…` route preserves location filters.
 */
export function machinesReturnHref(params: URLSearchParams): string {
  const source = machineSourceView(params);

  const location = validLocation(params.get('location'));
  const direct = params.get('scope') === 'direct';
  const query = new URLSearchParams();
  if (source === 'sites' || source === null) {
    if (location != null) query.set('location', String(location));
    if (direct) query.set('scope', 'direct');
    const base =
      source === 'sites' ? '/machines/index/sites/' : '/machines/index/';
    return query.toString() ? `${base}?${query}` : base;
  }

  const base =
    source === 'machines'
      ? '/machines/index/machines/'
      : '/machines/index/unassigned/';
  return query.toString() ? `${base}?${query}` : base;
}

export type MachineEmptyKind =
  | 'no-search-matches'
  | 'no-direct-machines'
  | 'no-machines-here'
  | 'no-unassigned-machines';

/** Distinct honest empty states; failures never route through here. */
export function machineEmptyKind(opts: {
  searched: boolean;
  unassigned: boolean;
  locationSelected: boolean;
  includeDescendants: boolean;
}): MachineEmptyKind {
  if (opts.searched) return 'no-search-matches';
  if (opts.unassigned) return 'no-unassigned-machines';
  if (opts.locationSelected && !opts.includeDescendants)
    return 'no-direct-machines';
  return 'no-machines-here';
}

/**
 * Workspace disambiguation for identical root names: only shown when the
 * actor actually has multiple authorized workspaces.
 */
export function workspaceLabel(
  client: number,
  workspaces: readonly { pk: number; name: string }[]
): string | null {
  if (workspaces.length < 2) return null;
  return workspaces.find((workspace) => workspace.pk === client)?.name ?? null;
}
