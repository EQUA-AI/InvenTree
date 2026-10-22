export const MACHINE_PAGE_SIZE = 25;

/**
 * R6 UI01: the declared machine classification (``aimms.machine_profile.v1``),
 * highest first. It is not an alarm severity or a work priority.
 */
export const MACHINE_CRITICALITIES = [
  'critical',
  'high',
  'medium',
  'low'
] as const;
export type MachineCriticality = (typeof MACHINE_CRITICALITIES)[number];

export function machineCriticality(value: unknown): MachineCriticality | null {
  return MACHINE_CRITICALITIES.find((level) => level === value) ?? null;
}

export interface MachineListState {
  search: string;
  alarmsOnly: boolean;
  showInactive?: boolean;
  showArchived?: boolean;
  criticality: MachineCriticality | '';
  page: number;
}

function machinePage(raw: string | null): number {
  const page = Number(raw);
  return raw &&
    /^\d+$/.test(raw) &&
    Number.isSafeInteger(page) &&
    page > 0 &&
    page <= Math.floor(Number.MAX_SAFE_INTEGER / MACHINE_PAGE_SIZE)
    ? page
    : 1;
}

export function machineListState(params: URLSearchParams): MachineListState {
  return {
    search: params.get('search') ?? '',
    alarmsOnly: params.get('has_alarms') === 'true',
    showInactive: params.get('show_inactive') === 'true',
    showArchived: params.get('show_archived') === 'true',
    criticality: machineCriticality(params.get('criticality')) ?? '',
    page: machinePage(params.get('page'))
  };
}

export function machineListParams(
  params: URLSearchParams,
  updates: Partial<MachineListState>
): URLSearchParams {
  const current = machineListState(params);
  const state = { ...current, ...updates };
  if (
    updates.page === undefined &&
    (state.search !== current.search ||
      state.alarmsOnly !== current.alarmsOnly ||
      !!state.showInactive !== !!current.showInactive ||
      state.criticality !== current.criticality)
  ) {
    state.page = 1;
  }
  const next = new URLSearchParams(params);
  if (state.search) next.set('search', state.search);
  else next.delete('search');
  if (state.alarmsOnly) next.set('has_alarms', 'true');
  else next.delete('has_alarms');
  if (state.showArchived) next.set('show_archived', 'true');
  else next.delete('show_archived');
  if (state.showInactive) next.set('show_inactive', 'true');
  else next.delete('show_inactive');
  if (state.criticality) next.set('criticality', state.criticality);
  else next.delete('criticality');
  const page = machinePage(String(state.page));
  if (page > 1) next.set('page', String(page));
  else next.delete('page');
  return next;
}

export function machineLocationParams(
  params: URLSearchParams,
  location: number | null
): URLSearchParams {
  const next = machineListParams(params, {
    search: '',
    alarmsOnly: false,
    criticality: '',
    page: 1
  });
  if (location != null) next.set('location', String(location));
  else next.delete('location');
  return next;
}
