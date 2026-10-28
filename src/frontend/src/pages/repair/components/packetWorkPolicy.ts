export interface LifecycleAvailability {
  permitted: boolean;
  ready: boolean;
  workVersion?: number;
  readinessVersion?: number;
  workError: boolean;
  readinessError: boolean;
  workFetching: boolean;
  readinessFetching: boolean;
  storageFailed: boolean;
  busy: boolean;
}

export function lifecycleActionDisabled(state: LifecycleAvailability): boolean {
  return (
    !state.permitted ||
    !state.ready ||
    state.workVersion === undefined ||
    state.readinessVersion !== state.workVersion ||
    state.workError ||
    state.readinessError ||
    state.workFetching ||
    state.readinessFetching ||
    state.storageFailed ||
    state.busy
  );
}
