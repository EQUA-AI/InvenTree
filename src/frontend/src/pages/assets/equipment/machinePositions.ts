import type { HealthState } from '@lib/types/MachineHealth';

import type { TwinPartId } from './twinScene.mjs';

/**
 * R6 P07: the machine's own records for a reference-model position, as
 * `/api/assets/machines/<pk>/positions/` (`reference_positions.v1`) serves
 * them. A position stands for a catalogue component family; its records are
 * the machine's slots for that family, their signals and their work. Nothing
 * is borrowed from a neighbouring position or another machine.
 */

export interface PositionComponent {
  id?: number;
  code: string;
  name: string;
  status: 'draft' | 'verified';
  provenance: string;
  part: { pk: number; name: string; IPN: string };
}

export interface PositionSignal {
  binding_id: number;
  display_name: string;
  unit: string;
  value: number | string | null;
  observed_at: string | null;
  quality: string;
  stale: boolean;
  state: HealthState;
}

export interface PositionWorkRow {
  id: number;
  reference: string | null;
  title: string;
  priority: 'low' | 'medium' | 'high';
  lifecycle_status: string;
  affected_component_ref: string;
  owner: string | null;
}

export interface MachinePosition {
  id: TwinPartId;
  focus_revision?: string | null;
  family: string | null;
  family_name: string | null;
  components: PositionComponent[];
  signals: PositionSignal[];
  /** Null when the Work scope is unavailable or there is no slot to own work. */
  work: { count: number; results: PositionWorkRow[] } | null;
}

export interface MachinePositions {
  rule: string;
  machine: number;
  machine_work: { count: number } | null;
  positions: MachinePosition[];
}

export type PositionSummary =
  | { kind: 'machine'; machineWork: number | null }
  | { kind: 'unbound'; machineWork: number | null }
  | {
      kind: 'no-slot';
      family: string;
      familyName: string | null;
      machineWork: number | null;
    }
  | {
      kind: 'bound';
      family: string;
      familyName: string | null;
      position: MachinePosition;
      /** Work on the machine not attributed to this position. */
      parentWork: number | null;
    };

/**
 * What the inspector can truthfully say about one position. Machine work is
 * always context; a position's own work is only what names its slot.
 */
export function positionSummary(
  data: MachinePositions | undefined,
  id: TwinPartId
): PositionSummary | null {
  if (!data) return null;
  const machineWork = data.machine_work?.count ?? null;
  if (id === 'machine') return { kind: 'machine', machineWork };
  const position = data.positions.find((candidate) => candidate.id === id);
  if (!position || !position.family) return { kind: 'unbound', machineWork };
  if (!position.components.length)
    return {
      kind: 'no-slot',
      family: position.family,
      familyName: position.family_name,
      machineWork
    };
  return {
    kind: 'bound',
    family: position.family,
    familyName: position.family_name,
    position,
    parentWork:
      machineWork === null || !position.work
        ? machineWork
        : Math.max(0, machineWork - position.work.count)
  };
}
