/** R6 P02: small display views of native records. They grant no authority. */
import type {
  ComponentFocusReference,
  EvidenceFocusReference,
  WorkFocusIdentity
} from './AimmsWire.generated';
import type {
  CloseoutCapture,
  CloseoutEffectRow,
  CloseoutReadingRow,
  CloseoutWorkOrder
} from './Closeout';
import type {
  HandledAction,
  InvestigationArtifact,
  InvestigationRequest
} from './WorkExecution';

export interface DisplaySource {
  record: string | null;
  id: number | string | null;
  machine_id?: number | null;
  revision: number | string | null;
  provider: string | null;
}
export interface CanonicalDisplay<T, S extends string = string> {
  source: DisplaySource;
  state: S | null;
  /** Preserve the complete response, including attribution and absent facts. */
  record: T;
}
export function displayWork<T extends CloseoutWorkOrder>(
  record: T
): CanonicalDisplay<T> {
  return {
    source: {
      record: 'work_order',
      id: record.id,
      revision: record.lifecycle_version,
      provider: null
    },
    state: record.lifecycle_status,
    record
  };
}
export function displayRequest(
  record: InvestigationRequest
): CanonicalDisplay<InvestigationRequest, InvestigationRequest['status']> {
  return {
    source: {
      record: 'investigation_request',
      id: record.id,
      revision: record.version,
      provider: null
    },
    state: record.status,
    record
  };
}
export function displayArtifact(
  record: InvestigationArtifact
): CanonicalDisplay<InvestigationArtifact> {
  return {
    source: {
      record: 'investigation_artifact',
      id: record.id,
      revision: null,
      provider: null
    },
    state: null,
    record
  };
}
export function displayAction(
  record: HandledAction
): CanonicalDisplay<HandledAction, HandledAction['state']> {
  return {
    source: { ...record.source, revision: null },
    state: record.state,
    record
  };
}
/** Native recorded follow-up status, including uncertain dispatch outcomes. */
export function displayUpdate(
  record: CloseoutEffectRow
): CanonicalDisplay<CloseoutEffectRow> {
  return {
    source: {
      record: 'closeout_effect',
      id: record.id,
      revision: null,
      provider: null
    },
    state: record.status,
    record
  };
}
export function displayCloseout(
  record: CloseoutCapture
): CanonicalDisplay<CloseoutCapture> {
  return {
    source: {
      record: 'closeout_capture',
      id: record.id,
      revision: record.revision,
      provider: null
    },
    state: record.status,
    record
  };
}
export function displayReading(
  record: CloseoutReadingRow
): CanonicalDisplay<CloseoutReadingRow> & {
  focus: EvidenceFocusReference | null;
} {
  return {
    source: {
      record: 'closeout_reading',
      id: record.id,
      revision: record.focus_revision ?? null,
      provider: null
    },
    state: record.verification_state,
    record,
    focus: record.focus_revision
      ? {
          kind: 'closeout_reading',
          id: record.id,
          revision: record.focus_revision
        }
      : null
  };
}
/** Optional future authority rows: absence never means authorized or in service. */
export function displayAuthority<
  T extends {
    id: number | string;
    status?: string | null;
    version?: number | string | null;
    source?: { record?: string | null; provider?: string | null };
  }
>(
  record?: T | null
): { state: 'not_recorded'; record: null; source: null } | CanonicalDisplay<T> {
  return record == null
    ? { state: 'not_recorded', record: null, source: null }
    : {
        source: {
          record: record.source?.record ?? null,
          id: record.id,
          revision: record.version ?? null,
          provider: record.source?.provider ?? null
        },
        state: record.status ?? null,
        record
      };
}
export function displayComponent(
  machineId: number,
  rule: string,
  position: { id: string; focus_revision?: string | null },
  slotCode: string | null = null
): ComponentFocusReference | null {
  return rule === 'reference_positions.v1' && position.focus_revision
    ? {
        machine_id: machineId,
        rule,
        position_id: position.id,
        slot_code: slotCode,
        revision: position.focus_revision
      }
    : null;
}
export function displayFocus(
  record: WorkFocusIdentity
): CanonicalDisplay<WorkFocusIdentity> {
  return {
    source: {
      record: record.kind,
      id: record.work_order_id ?? record.component?.position_id ?? null,
      machine_id: record.machine_id,
      revision: record.lifecycle_version ?? record.component?.revision ?? null,
      provider:
        record.kind === 'reference_position'
          ? (record.component?.rule ?? null)
          : null
    },
    state: null,
    record
  };
}
