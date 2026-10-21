/**
 * P02/P33: the Work-focus contract at the browser boundary.
 *
 * A question asked from a Work order is acknowledged by the server
 * (`POST /threads/{id}/work-focus`) before it is sent, and the answer names
 * the Work it was about. Everything here is parsing and display derivation:
 * the server decides access, and a malformed or mismatched response is never
 * treated as a confirmation.
 */
import type {
  ComponentFocusReference,
  EvidenceFocusReference,
  WorkFocusAcknowledgement,
  WorkFocusIdentity,
  WorkFocusState
} from '@lib/types/AimmsWire.generated';

import {
  type WorkPanel,
  workPanelState
} from '../../pages/tasks/workorders/workPanelState';

/** The server's receipt lifetime and size; a larger claim is not this contract. */
const MAX_RECEIPT_SECONDS = 300;
const MAX_RECEIPT_LENGTH = 4096;
const IDENTITY_KEYS = [
  'schema_version',
  'kind',
  'work_order_id',
  'lifecycle_version',
  'machine_id'
] as const;

/** A focused question's identity as the transcript keeps it. */
export interface ChatWorkFocus {
  identity: WorkFocusIdentity;
  /** What the launching page showed ("WO-73 · Replace seal"); display only. */
  label?: string;
  /** The validated return route, including the Work panel it came from. */
  returnPath?: string;
  /**
   * How the Work stands now, as the server re-checked it when the answer was
   * reloaded. Absent for a live answer and for an older server.
   */
  state?: WorkFocusState;
  references?: Partial<Record<'component' | 'evidence', WorkFocusState>>;
}

const WORK_FOCUS_STATES: readonly WorkFocusState[] = [
  'current',
  'changed',
  'unavailable'
];

/** A served state, or undefined for anything this client does not know. */
export function parseWorkFocusState(
  value: unknown
): WorkFocusState | undefined {
  return WORK_FOCUS_STATES.find((state) => state === value);
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function positiveId(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

const REVISION = /^[a-f0-9]{64}$/;
const POSITIONS = [
  'machine',
  'motor',
  'bearing',
  'coupling',
  'seal',
  'impeller',
  'pump',
  'discharge',
  'base'
];

export function parseComponentFocus(
  value: unknown
): ComponentFocusReference | null {
  const c = record(value);
  return c &&
    Object.keys(c).length === 5 &&
    positiveId(c.machine_id) &&
    c.rule === 'reference_positions.v1' &&
    typeof c.position_id === 'string' &&
    POSITIONS.includes(c.position_id) &&
    (c.slot_code === null ||
      (typeof c.slot_code === 'string' && !!c.slot_code)) &&
    typeof c.revision === 'string' &&
    REVISION.test(c.revision)
    ? {
        machine_id: c.machine_id,
        rule: 'reference_positions.v1',
        position_id: c.position_id,
        slot_code: c.slot_code as string | null,
        revision: c.revision
      }
    : null;
}
export function parseEvidenceFocus(
  value: unknown
): EvidenceFocusReference | null {
  const e = record(value);
  return e &&
    Object.keys(e).length === 3 &&
    ['attachment', 'closeout_reading', 'media'].includes(e.kind as string) &&
    positiveId(e.id) &&
    typeof e.revision === 'string' &&
    REVISION.test(e.revision)
    ? {
        kind: e.kind as EvidenceFocusReference['kind'],
        id: e.id,
        revision: e.revision
      }
    : null;
}
/** The exact versioned identity; unknown fields and partial coordinates fail closed. */
export function parseWorkFocusIdentity(
  value: unknown
): WorkFocusIdentity | null {
  const f = record(value);
  if (
    !f ||
    ![1, 2, 3].includes(f.schema_version as number) ||
    !IDENTITY_KEYS.every((k) => k in f)
  )
    return null;
  const v3 = f.schema_version === 3;
  const request = f.schema_version === 2 || (v3 && 'request_id' in f);
  const component =
    v3 && 'component' in f ? parseComponentFocus(f.component) : undefined;
  const evidence =
    v3 && 'evidence' in f ? parseEvidenceFocus(f.evidence) : undefined;
  const keys = [
    ...IDENTITY_KEYS,
    ...(request ? ['request_id', 'request_version'] : []),
    ...(v3 && 'component' in f ? ['component'] : []),
    ...(v3 && 'evidence' in f ? ['evidence'] : [])
  ];
  if (
    Object.keys(f).length !== keys.length ||
    !Object.keys(f).every((k) => keys.includes(k)) ||
    (request &&
      (!positiveId(f.request_id) || !positiveId(f.request_version))) ||
    (v3 &&
      ((!component && !evidence) || component === null || evidence === null)) ||
    !(f.machine_id === null || positiveId(f.machine_id)) ||
    (component && component.machine_id !== f.machine_id) ||
    (f.kind === 'work_order'
      ? !positiveId(f.work_order_id) || !positiveId(f.lifecycle_version)
      : !(
          v3 &&
          f.kind === 'reference_position' &&
          component &&
          f.work_order_id === null &&
          f.lifecycle_version === null &&
          !request
        ))
  )
    return null;
  return f as unknown as WorkFocusIdentity;
}

/**
 * Accept an acknowledgement only for the exact thread, Work and version the
 * client asked about. A 200 that names anything else is not a confirmation.
 */
export function parseWorkFocusAcknowledgement(
  value: unknown,
  threadId: string,
  workOrderId: number | null,
  expectedVersion: number | null,
  requestId?: number,
  requestVersion?: number,
  component?: ComponentFocusReference,
  evidence?: EvidenceFocusReference
): WorkFocusAcknowledgement | null {
  const payload = record(value);
  const focus = parseWorkFocusIdentity(payload?.focus);
  if (
    !payload ||
    !focus ||
    !threadId ||
    payload.schema_version !== 1 ||
    payload.thread_id !== threadId ||
    focus.work_order_id !== workOrderId ||
    focus.lifecycle_version !== expectedVersion ||
    (focus.request_id ?? undefined) !== requestId ||
    (focus.request_version ?? undefined) !== requestVersion ||
    JSON.stringify(
      focus.component ? parseComponentFocus(focus.component) : undefined
    ) !==
      JSON.stringify(component ? parseComponentFocus(component) : undefined) ||
    JSON.stringify(
      focus.evidence ? parseEvidenceFocus(focus.evidence) : undefined
    ) !== JSON.stringify(evidence ? parseEvidenceFocus(evidence) : undefined) ||
    typeof payload.receipt !== 'string' ||
    payload.receipt.length === 0 ||
    payload.receipt.length > MAX_RECEIPT_LENGTH ||
    typeof payload.expires_in !== 'number' ||
    !Number.isSafeInteger(payload.expires_in) ||
    payload.expires_in < 1 ||
    payload.expires_in > MAX_RECEIPT_SECONDS
  )
    return null;
  return {
    schema_version: 1,
    thread_id: threadId,
    focus,
    receipt: payload.receipt,
    expires_in: payload.expires_in
  };
}

const WORK_ROUTE = /^\/maintenance\/work-orders\/(\d+)\/?(?:\?[^#]*)?$/;

/**
 * Where "Open work order" goes: the Work's own route, keeping the panel the
 * question was asked from when the launch path was that same Work. Anything
 * else — another record, another page, an absolute URL — falls back to the
 * Work's default view, so a stored path can never redirect elsewhere.
 */
export function workFocusReturnPath(
  workOrderId: number | null,
  launchPath?: string | null,
  requestId?: number | null
): string {
  if (workOrderId === null) return '/';
  const canonical = `/maintenance/work-orders/${workOrderId}`;
  const match = launchPath ? WORK_ROUTE.exec(launchPath) : null;
  if (positiveId(requestId))
    return `${canonical}?work_panel=diagnose&investigation_request=${requestId}`;
  if (!match || Number(match[1]) !== workOrderId) return canonical;
  const query = launchPath!.includes('?')
    ? launchPath!.slice(launchPath!.indexOf('?') + 1)
    : '';
  const panel: WorkPanel | null = workPanelState(new URLSearchParams(query));
  return panel && panel !== 'work'
    ? `${canonical}?work_panel=${panel}`
    : canonical;
}

/**
 * A focus from a reloaded message (ids only) or null when it is malformed.
 * The state travels only with a valid identity; an unknown state is dropped,
 * never guessed.
 */
export function reloadedWorkFocus(
  value: unknown,
  stateValue?: unknown,
  referenceValues?: unknown
): ChatWorkFocus | null {
  const identity = parseWorkFocusIdentity(value);
  if (!identity) return null;
  const state = parseWorkFocusState(stateValue);
  const references = Object.fromEntries(
    Object.entries(record(referenceValues) ?? {}).filter(
      ([key, state]) =>
        ['component', 'evidence'].includes(key) && parseWorkFocusState(state)
    )
  );
  return {
    identity,
    returnPath: focusReturnPath(identity),
    ...(state ? { state } : {}),
    ...(Object.keys(references).length ? { references } : {})
  };
}

/** Return only to a canonical Work/request or the selected reference position. */
export function focusReturnPath(
  identity: WorkFocusIdentity,
  launchPath?: string
): string {
  if (identity.work_order_id !== null)
    return workFocusReturnPath(
      identity.work_order_id,
      launchPath,
      identity.request_id
    );
  const c = identity.component;
  return c
    ? `/machines/machine/${c.machine_id}/equipment/?position=${encodeURIComponent(c.position_id)}${launchPath?.includes('view=schematic') ? '&view=schematic' : ''}`
    : '/';
}
