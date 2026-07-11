/**
 * Shared API helpers, label maps, and option lists for the repair Safety panel.
 *
 * The backend (repair app) is the source of truth for safety enforcement; these
 * helpers only normalise request/response handling and presentation so the UI
 * mirrors the backend `detail` wording exactly.
 */
import { t } from '@lingui/core/macro';
import type { AxiosInstance } from 'axios';

import type { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

/** Normalised result of a gate action (confirm/verify/waive). */
export interface GateActionResult {
  ok: boolean;
  detail: string;
  status: number;
  data: any;
}

/** Build the URL for a gate-scoped action endpoint. */
export function gateActionUrl(
  endpoint: ApiEndpoints,
  packetId: number,
  gateId: number
): string {
  return apiUrl(endpoint, packetId, { gate_pk: gateId });
}

/**
 * POST a gate action and normalise both success (200 `{ok, detail}`) and
 * failure (400 `{ok, detail}`) into a single shape, so callers can inspect the
 * backend `detail` without a try/catch dance. This matters for the high-risk
 * waiver path, where the backend returns 400 with "Safety approval required:".
 */
export async function postGateAction(
  api: AxiosInstance,
  endpoint: ApiEndpoints,
  packetId: number,
  gateId: number,
  body: Record<string, unknown> = {}
): Promise<GateActionResult> {
  try {
    const response = await api.post(
      gateActionUrl(endpoint, packetId, gateId),
      body
    );
    return {
      ok: response.data?.ok ?? true,
      detail: response.data?.detail ?? '',
      status: response.status,
      data: response.data
    };
  } catch (error: any) {
    return {
      ok: false,
      detail: error?.response?.data?.detail ?? t`Safety action failed`,
      status: error?.response?.status ?? 0,
      data: error?.response?.data
    };
  }
}

/**
 * Whether a waiver failure is actually an approval-created response rather than
 * a hard error (backend currently returns 400 with this detail wording).
 */
export function isApprovalRequired(result: GateActionResult): boolean {
  return /safety approval required/i.test(result.detail ?? '');
}

/** Human-readable label for a gate type. */
export function gateTypeLabel(value: string): string {
  switch (value) {
    case 'loto':
      return t`Lockout/Tagout`;
    case 'permit':
      return t`Permit`;
    case 'ppe':
      return t`PPE`;
    case 'isolation':
      return t`Isolation`;
    case 'hot_work':
      return t`Hot Work`;
    default:
      return t`Other`;
  }
}

/** Mantine color for a gate status. */
export function gateStatusColor(status: string): string {
  switch (status) {
    case 'confirmed':
      return 'green';
    case 'waived':
      return 'orange';
    case 'pending':
      return 'yellow';
    default:
      return 'gray';
  }
}

/** Human-readable label for a gate status. */
export function gateStatusLabel(status: string): string {
  switch (status) {
    case 'confirmed':
      return t`Confirmed`;
    case 'waived':
      return t`Waived`;
    case 'pending':
      return t`Pending`;
    default:
      return status;
  }
}

/** Whether a gate supports lockout points (LOTO / isolation). */
export function gateSupportsLockout(gateType: string): boolean {
  return gateType === 'loto' || gateType === 'isolation';
}

/** Energy source options for lockout points. */
export function energySourceOptions(): { value: string; label: string }[] {
  return [
    { value: 'electrical', label: t`Electrical` },
    { value: 'hydraulic', label: t`Hydraulic` },
    { value: 'pneumatic', label: t`Pneumatic` },
    { value: 'mechanical', label: t`Mechanical` },
    { value: 'thermal', label: t`Thermal` },
    { value: 'chemical', label: t`Chemical` },
    { value: 'gravity', label: t`Gravity` },
    { value: 'other', label: t`Other` }
  ];
}

/** Lockout point lifecycle statuses, in order. */
export function lockoutStatusOptions(): { value: string; label: string }[] {
  return [
    { value: 'identified', label: t`Identified` },
    { value: 'isolated', label: t`Isolated` },
    { value: 'locked', label: t`Locked` },
    { value: 'verified', label: t`Verified` },
    { value: 'restored', label: t`Restored` }
  ];
}

/** Ordered lifecycle for the lockout point stepper. */
export const LOCKOUT_STATUS_ORDER = [
  'identified',
  'isolated',
  'locked',
  'verified',
  'restored'
];

/** Mantine color for a lockout point status. */
export function lockoutStatusColor(status: string): string {
  switch (status) {
    case 'verified':
      return 'green';
    case 'restored':
      return 'blue';
    case 'locked':
      return 'yellow';
    case 'isolated':
      return 'orange';
    default:
      return 'gray';
  }
}

/** Proof type options for the proof modal. */
export function proofTypeOptions(): { value: string; label: string }[] {
  return [
    { value: 'photo', label: t`Photo` },
    { value: 'scan', label: t`Scan` },
    { value: 'reading', label: t`Reading` },
    { value: 'geofence', label: t`Geofence` },
    { value: 'signature', label: t`Signature` }
  ];
}
