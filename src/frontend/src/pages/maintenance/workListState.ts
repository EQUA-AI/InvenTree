import type { KanbanPriority } from '@lib/types/Tasks';

export const WORK_LIST_PAGE_SIZE = 25;
export const WORK_LIFECYCLES = [
  'draft',
  'planned',
  'ready',
  'in_progress',
  'on_hold',
  'verifying',
  'completed',
  'canceled'
] as const;
export const WORK_TYPES = [
  'corrective',
  'preventive',
  'inspection',
  'calibration',
  'other'
] as const;
export type WorkListScope = 'mine' | 'attention' | 'all';

/**
 * R6 UI10 "Needs attention" reason codes, in display order. The server's
 * ``needs_attention.v1`` rule supplies them per row; unknown codes are dropped.
 */
export const WORK_ATTENTION_REASONS = [
  'high_priority',
  'overdue',
  'on_hold',
  'no_owner'
] as const;
export type WorkAttentionReason = (typeof WORK_ATTENTION_REASONS)[number];

export function workAttentionReasons(value: unknown): WorkAttentionReason[] {
  if (!Array.isArray(value)) return [];
  return WORK_ATTENTION_REASONS.filter((reason) => value.includes(reason));
}

/**
 * R6 UI10 first-use scope from the server's ``work_persona.v1`` answer:
 * supervisors start on Needs attention, technicians on My work. Anything
 * else, including a failed read, is the neutral All work. It only chooses
 * where the list starts; it is never saved as the user's own choice.
 */
export function workPersonaScope(value: unknown): WorkListScope {
  const scope =
    value && typeof value === 'object'
      ? (value as { default_scope?: unknown }).default_scope
      : undefined;
  return scope === 'mine' || scope === 'attention' ? scope : 'all';
}

export interface WorkListState {
  scope: WorkListScope;
  search: string;
  priority: KanbanPriority | '';
  lifecycle: (typeof WORK_LIFECYCLES)[number] | '';
  workType: (typeof WORK_TYPES)[number] | '';
  page: number;
}

const QUERY_KEYS = [
  'work_scope',
  'search',
  'priority',
  'lifecycle_status',
  'work_order_type',
  'work_page'
];

export function workListPreferenceKey(host: string, actorId: number): string {
  return `aimms-work-list:${encodeURIComponent(host || 'local')}:${actorId}`;
}

export function workListState(
  params: URLSearchParams,
  saved: WorkListScope
): WorkListState | null {
  const scope = params.get('work_scope') ?? saved;
  const priority = params.get('priority') ?? '';
  const lifecycle = params.get('lifecycle_status') ?? '';
  const workType = params.get('work_order_type') ?? '';
  if (
    !['mine', 'attention', 'all'].includes(scope) ||
    (priority && !['low', 'medium', 'high'].includes(priority)) ||
    (lifecycle && !WORK_LIFECYCLES.some((value) => value === lifecycle)) ||
    (workType && !WORK_TYPES.some((value) => value === workType))
  )
    return null;
  const rawPage = params.get('work_page');
  const parsedPage = Number(rawPage);
  const page =
    rawPage &&
    /^\d+$/.test(rawPage) &&
    Number.isSafeInteger(parsedPage) &&
    parsedPage > 0 &&
    parsedPage <= Math.floor(Number.MAX_SAFE_INTEGER / WORK_LIST_PAGE_SIZE)
      ? parsedPage
      : 1;
  return {
    scope: scope as WorkListScope,
    search: params.get('search') ?? '',
    priority: priority as WorkListState['priority'],
    lifecycle: lifecycle as WorkListState['lifecycle'],
    workType: workType as WorkListState['workType'],
    page
  };
}

export function workListParams(
  params: URLSearchParams,
  state: WorkListState
): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of QUERY_KEYS) next.delete(key);
  next.set('work_scope', state.scope);
  if (state.search) next.set('search', state.search);
  if (state.priority) next.set('priority', state.priority);
  if (state.lifecycle) next.set('lifecycle_status', state.lifecycle);
  if (state.workType) next.set('work_order_type', state.workType);
  if (state.page > 1) next.set('work_page', String(state.page));
  return next;
}

export function workListRequest(state: WorkListState, actorId: number) {
  return {
    assigned_to: state.scope === 'mine' ? actorId : undefined,
    attention: state.scope === 'attention' ? 'true' : undefined,
    search: state.search,
    priority: state.priority || undefined,
    lifecycle_status: state.lifecycle || undefined,
    work_order_type: state.workType || undefined,
    limit: WORK_LIST_PAGE_SIZE,
    offset: (state.page - 1) * WORK_LIST_PAGE_SIZE
  };
}
