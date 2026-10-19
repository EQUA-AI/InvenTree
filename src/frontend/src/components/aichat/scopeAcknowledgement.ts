import type {
  AnalysisScopeUpdate,
  ThreadScopePayload
} from '@lib/types/AimmsWire.generated';

const SOURCE_CLASSES = [
  'controlled_document',
  'asset_attachment',
  'work_order',
  'maintenance_record'
] as const;

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function validDate(value: unknown): boolean {
  if (value === null) return true;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}

function sameMembers(left: readonly unknown[], right: readonly unknown[]) {
  const expected = new Set(right);
  return (
    left.length === expected.size && left.every((value) => expected.has(value))
  );
}

export function parseThreadScopePayload(
  value: unknown,
  threadId: string
): ThreadScopePayload | null {
  const payload = record(value);
  const scope = record(payload?.scope);
  const window = record(scope?.date_window);
  if (
    !payload ||
    !scope ||
    !window ||
    !threadId ||
    payload.thread_id !== threadId ||
    typeof payload.version !== 'number' ||
    !Number.isSafeInteger(payload.version) ||
    payload.version < 0 ||
    typeof payload.editable !== 'boolean' ||
    typeof payload.display_label !== 'string' ||
    typeof payload.hash !== 'string' ||
    scope.schema_version !== 1 ||
    typeof scope.mode !== 'string' ||
    ![
      'legacy_unconfirmed',
      'all_authorized_assets',
      'explicit_assets'
    ].includes(scope.mode) ||
    typeof scope.display_label !== 'string' ||
    scope.display_label.length > 120 ||
    !Array.isArray(scope.machine_ids) ||
    scope.machine_ids.length > 50 ||
    !scope.machine_ids.every(
      (machineId) =>
        typeof machineId === 'number' &&
        Number.isSafeInteger(machineId) &&
        machineId > 0
    ) ||
    new Set(scope.machine_ids).size !== scope.machine_ids.length ||
    !Array.isArray(scope.source_classes) ||
    !scope.source_classes.length ||
    new Set(scope.source_classes).size !== scope.source_classes.length ||
    !scope.source_classes.every((source) =>
      SOURCE_CLASSES.some((supported) => source === supported)
    ) ||
    !validDate(window.from) ||
    !validDate(window.to) ||
    (typeof window.from === 'string' &&
      typeof window.to === 'string' &&
      window.from >= window.to)
  )
    return null;
  if (
    (scope.mode === 'explicit_assets') !== scope.machine_ids.length > 0 ||
    (payload.version === 0
      ? scope.mode !== 'legacy_unconfirmed' || payload.hash !== ''
      : !/^[a-f0-9]{64}$/i.test(payload.hash))
  )
    return null;
  return value as ThreadScopePayload;
}

export function parseScopeAcknowledgement(
  value: unknown,
  threadId: string,
  expectedVersion: number,
  requested: AnalysisScopeUpdate
): ThreadScopePayload | null {
  const payload = parseThreadScopePayload(value, threadId);
  if (
    !payload ||
    !Number.isSafeInteger(expectedVersion) ||
    expectedVersion < 0 ||
    !Number.isSafeInteger(expectedVersion + 1) ||
    payload.version !== expectedVersion + 1 ||
    !payload.editable ||
    !['explicit_assets', 'all_authorized_assets'].includes(requested.mode) ||
    payload.scope.mode !== requested.mode ||
    !sameMembers(payload.scope.machine_ids, requested.machine_ids ?? []) ||
    !sameMembers(
      payload.scope.source_classes,
      requested.source_classes ?? SOURCE_CLASSES
    ) ||
    payload.scope.date_window.from !== (requested.date_window?.from || null) ||
    payload.scope.date_window.to !== (requested.date_window?.to || null) ||
    payload.scope.display_label !== (requested.display_label || '')
  )
    return null;
  return payload;
}
