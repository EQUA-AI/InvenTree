/**
 * R6 P12 / UI06: the machine Maintenance tab's two views and their URL state.
 *
 * Open work and Completed history are separate views with their own counts,
 * both from the server's `machine_work_views.v1` summary. The view, the open
 * list's search and page live in the URL beside whatever the page already
 * carries (the Machines list's return state), so opening a work order and
 * pressing Back lands on the same view, page and search.
 */

import {
  type MachineWorkSummary,
  type WorkPriority,
  machineWorkSummary
} from '../locations/machineWork';

export type MachineWorkView = 'open' | 'completed';

/** Rows per page of the Open work view; the server caps pages at 200. */
export const OPEN_WORK_PAGE_SIZE = 25;

export const WORK_VIEW_PARAM = 'work_view';
export const WORK_SEARCH_PARAM = 'work_search';
export const WORK_PAGE_PARAM = 'work_page';

export interface MachineWorkState {
  view: MachineWorkView;
  search: string;
  page: number;
}

/**
 * The view state from the URL, or null when the link names a view or page
 * this tab does not have - the panel then offers a way back rather than
 * guessing which view was meant.
 */
export function machineWorkState(
  params: URLSearchParams
): MachineWorkState | null {
  const rawView = params.get(WORK_VIEW_PARAM);
  if (rawView !== null && rawView !== 'open' && rawView !== 'completed')
    return null;
  const rawPage = params.get(WORK_PAGE_PARAM);
  let page = 1;
  if (rawPage !== null) {
    if (!/^[1-9][0-9]{0,5}$/.test(rawPage)) return null;
    page = Number(rawPage);
  }
  return {
    view: (rawView as MachineWorkView | null) ?? 'open',
    search: (params.get(WORK_SEARCH_PARAM) ?? '').slice(0, 200),
    page
  };
}

/**
 * The URL with this tab's state replaced and every other parameter kept.
 * Defaults are left out so a plain link stays plain.
 */
export function machineWorkParams(
  previous: URLSearchParams,
  state: MachineWorkState
): URLSearchParams {
  const next = new URLSearchParams(previous);
  next.delete(WORK_VIEW_PARAM);
  next.delete(WORK_SEARCH_PARAM);
  next.delete(WORK_PAGE_PARAM);
  if (state.view !== 'open') next.set(WORK_VIEW_PARAM, state.view);
  if (state.view === 'open') {
    if (state.search) next.set(WORK_SEARCH_PARAM, state.search);
    if (state.page > 1) next.set(WORK_PAGE_PARAM, String(state.page));
  }
  return next;
}

/** The native Work-list read for one page of a machine's open work. */
export function openWorkRequest(
  machineId: number,
  state: Pick<MachineWorkState, 'search' | 'page'>
): Record<string, string | number> {
  const request: Record<string, string | number> = {
    machine: machineId,
    lifecycle_group: 'open',
    limit: OPEN_WORK_PAGE_SIZE,
    offset: (state.page - 1) * OPEN_WORK_PAGE_SIZE
  };
  const search = state.search.trim();
  if (search) request.search = search;
  return request;
}

export type CompletedCount =
  | { kind: 'unavailable' }
  | { kind: 'known'; count: number };

export interface MachineWorkCounts {
  open: MachineWorkSummary;
  completed: CompletedCount;
  /** The open lifecycle states the server counted, in native order. */
  openStatuses: string[];
}

const UNAVAILABLE: MachineWorkCounts = {
  open: { kind: 'unavailable' },
  completed: { kind: 'unavailable' },
  openStatuses: []
};

/**
 * The server's summary, defensively: anything malformed is "not available",
 * never a zero that would read as "nothing open" or "no history".
 */
export function machineWorkCounts(value: unknown): MachineWorkCounts {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    return UNAVAILABLE;
  const record = value as Record<string, unknown>;
  if (record.rule !== 'machine_work_views.v1') return UNAVAILABLE;
  const completed = record.completed as Record<string, unknown> | null;
  const completedCount =
    completed &&
    typeof completed === 'object' &&
    typeof completed.count === 'number' &&
    Number.isSafeInteger(completed.count) &&
    completed.count >= 0
      ? ({ kind: 'known', count: completed.count } as const)
      : ({ kind: 'unavailable' } as const);
  const statuses = Array.isArray(record.open_statuses)
    ? record.open_statuses.filter(
        (status): status is string => typeof status === 'string'
      )
    : [];
  return {
    open: machineWorkSummary(record.open),
    completed: completedCount,
    openStatuses: statuses
  };
}

/** The open count as a number, or null when it is not known. */
export function openCount(summary: MachineWorkSummary): number | null {
  if (summary.kind === 'unavailable') return null;
  return summary.kind === 'none' ? 0 : summary.count;
}

export function openPriority(summary: MachineWorkSummary): WorkPriority | null {
  return summary.kind === 'open' ? summary.priority : null;
}

/**
 * A machine link opened on its Maintenance tab's Open work view, keeping the
 * return state the link already carries.
 */
export function machineOpenWorkHref(detailHref: string): string {
  const [path, query = ''] = detailHref.split('?');
  const base = path.endsWith('/') ? path : `${path}/`;
  const params = new URLSearchParams(query);
  params.delete(WORK_VIEW_PARAM);
  params.delete(WORK_SEARCH_PARAM);
  params.delete(WORK_PAGE_PARAM);
  const search = params.toString();
  return `${base}maintenance/${search ? `?${search}` : ''}`;
}
