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

/** The verified ids of a focus, or null for anything else. */
export function parseWorkFocusIdentity(
  value: unknown
): WorkFocusIdentity | null {
  const focus = record(value);
  if (
    !focus ||
    Object.keys(focus).length !== IDENTITY_KEYS.length ||
    !IDENTITY_KEYS.every((key) => key in focus) ||
    focus.schema_version !== 1 ||
    focus.kind !== 'work_order' ||
    !positiveId(focus.work_order_id) ||
    !positiveId(focus.lifecycle_version) ||
    !(focus.machine_id === null || positiveId(focus.machine_id))
  )
    return null;
  return {
    schema_version: 1,
    kind: 'work_order',
    work_order_id: focus.work_order_id,
    lifecycle_version: focus.lifecycle_version,
    machine_id: focus.machine_id
  };
}

/**
 * Accept an acknowledgement only for the exact thread, Work and version the
 * client asked about. A 200 that names anything else is not a confirmation.
 */
export function parseWorkFocusAcknowledgement(
  value: unknown,
  threadId: string,
  workOrderId: number,
  expectedVersion: number
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
  workOrderId: number,
  launchPath?: string | null
): string {
  const canonical = `/maintenance/work-orders/${workOrderId}`;
  const match = launchPath ? WORK_ROUTE.exec(launchPath) : null;
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
  stateValue?: unknown
): ChatWorkFocus | null {
  const identity = parseWorkFocusIdentity(value);
  if (!identity) return null;
  const state = parseWorkFocusState(stateValue);
  return {
    identity,
    returnPath: workFocusReturnPath(identity.work_order_id),
    ...(state ? { state } : {})
  };
}
