import type { AuthorityRecord } from '@lib/types/OperationalAuthority';

export interface AuthorityIntent {
  workOrderId: number;
  actorId: number;
  payload: {
    expected_version: number;
    expected_context: string;
    idempotency_key: string;
    operation: 'record' | 'verify';
    fields: Record<string, string | number | null>;
    supersedes?: number;
  };
}
export interface AuthorityReceipt {
  work_order_id: number;
  work_version: number;
  idempotency_key: string;
  expected_context: string;
  record: AuthorityRecord;
}
export type AuthorityCommandState =
  | { status: 'idle' | 'blocked' }
  | {
      status: 'pending' | 'uncertain' | 'failed' | 'stale';
      intent: AuthorityIntent;
      message?: string;
    }
  | { status: 'confirmed'; intent: AuthorityIntent; receipt: AuthorityReceipt };

export function matchingAuthorityReceipt(
  value: unknown,
  intent: AuthorityIntent
): value is AuthorityReceipt {
  if (!value || typeof value !== 'object') return false;
  const row = value as AuthorityReceipt;
  const record = row.record;
  if (
    !(
      row.work_order_id === intent.workOrderId &&
      row.work_version === intent.payload.expected_version + 1 &&
      row.idempotency_key === intent.payload.idempotency_key &&
      row.expected_context === intent.payload.expected_context &&
      record?.work_order_id === intent.workOrderId &&
      record.version === row.work_version &&
      Number.isSafeInteger(record.id) &&
      record.id > 0 &&
      record.supersedes === (intent.payload.supersedes ?? null) &&
      typeof record.recorded_by === 'string' &&
      !!record.recorded_by.trim() &&
      Number.isFinite(Date.parse(record.recorded_at))
    )
  )
    return false;
  if (intent.payload.operation === 'verify')
    return (
      record.gate_id === intent.payload.fields.gate_id &&
      record.verified_by_id === intent.actorId &&
      record.recorded_by_id !== intent.actorId &&
      !!record.verified_by &&
      Number.isFinite(Date.parse(record.verified_at || ''))
    );
  if (record.recorded_by_id !== intent.actorId) return false;
  const fields = intent.payload.fields;
  return Object.entries(fields).every(([key, value]) => {
    if (['duty', 'standby', 'redundancy', 'restrictions'].includes(key))
      return (
        !!record.details &&
        typeof record.details === 'object' &&
        (record.details[key as keyof typeof record.details] ?? null) ===
          (value || null)
      );
    if (key === 'binding_id')
      return record.basis?.telemetry?.binding_id === value;
    if (key === 'telemetry_token')
      return record.basis?.telemetry?.reading_token === value;
    if (key === 'telemetry_observed_at')
      return (
        Date.parse(record.basis?.telemetry?.observed_at || '') ===
        Date.parse(String(value))
      );
    const actual = record[key as keyof AuthorityRecord];
    if (['observed_at', 'effective_from', 'effective_until'].includes(key))
      return value === null
        ? actual === null
        : Date.parse(String(actual)) === Date.parse(String(value));
    return actual === value;
  });
}

/** Refresh and two tabs keep the same exact operation until it is reconciled. */
export function restoreAuthorityCommand(
  raw: string | null,
  workOrderId: number,
  actorId?: number
): AuthorityCommandState {
  if (raw === null) return { status: 'idle' };
  try {
    const row = JSON.parse(raw) as AuthorityCommandState;
    if (!row || !('intent' in row)) return { status: 'blocked' };
    const intent = row.intent;
    if (
      !intent ||
      intent.workOrderId !== workOrderId ||
      (actorId !== undefined && intent.actorId !== actorId) ||
      !Number.isSafeInteger(intent.actorId) ||
      intent.actorId < 1 ||
      !Number.isSafeInteger(intent.payload?.expected_version) ||
      intent.payload.expected_version < 1 ||
      typeof intent.payload.expected_context !== 'string' ||
      !/^[a-f0-9]{64}$/.test(intent.payload.expected_context) ||
      typeof intent.payload.idempotency_key !== 'string' ||
      !intent.payload.idempotency_key.trim() ||
      !['record', 'verify'].includes(intent.payload.operation) ||
      !intent.payload.fields ||
      typeof intent.payload.fields !== 'object' ||
      Array.isArray(intent.payload.fields)
    )
      return { status: 'blocked' };
    if (row.status === 'confirmed')
      return matchingAuthorityReceipt(row.receipt, intent)
        ? row
        : { status: 'uncertain', intent };
    if (row.status === 'pending') return { status: 'uncertain', intent };
    return ['uncertain', 'failed', 'stale'].includes(row.status)
      ? row
      : { status: 'blocked' };
  } catch {
    return { status: 'blocked' };
  }
}

/** A newer receipt cannot confirm an unrelated draft prepared on older Work. */
export function authorityIntentIsStale(
  state: AuthorityCommandState,
  intent: AuthorityIntent
) {
  return (
    state.status === 'confirmed' &&
    intent.payload.expected_version < state.receipt.work_version
  );
}

/** Work version advances rebase mounted sibling drafts; same-version source drift still requires review. */
export function authorityDraftContext(
  pinned: { version: number; token: string } | null,
  version: number,
  token: string
) {
  return pinned && pinned.version !== version ? { version, token } : pinned;
}
