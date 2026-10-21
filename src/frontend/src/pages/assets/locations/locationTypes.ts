import type { AssetMachine } from '@lib/types/Assets';

import type { MachineConditionWire } from './machineCondition';
import type { MachineCriticality } from './machineListState';
import type { MachineOpenWork } from './machineWork';

export const locationApi = '/api/assets/locations/';

export interface LocationNode {
  pk: number;
  client: number;
  parent: number | null;
  name: string;
  code: string;
  kind: string;
  description: string;
  timezone: string;
  effective_timezone: string | null;
  archived: boolean;
  version: number;
  path: { pk: number; name: string }[];
  has_children: boolean;
  counts?: {
    direct_machines: number;
    total_machines: number;
    direct_open_work_orders: number | null;
    total_open_work_orders: number | null;
  };
}

export interface LocatedMachine extends AssetMachine {
  open_warning_alarms?: number | null;
  physical_location: LocationNode | null;
  placement_version: number;
  /** R6 UI01: open Work in the reader's Work scope; null when unknown. */
  open_work?: MachineOpenWork | null;
  /** R6 UI01: declared criticality; null when nothing valid is declared. */
  criticality?: MachineCriticality | null;
  /** R6 UI01: Health condition and coverage; null when it cannot be judged. */
  condition?: MachineConditionWire | null;
}

export interface LocationContext {
  plant_timezone?: string | null;
  workspaces: { pk: number; name: string }[];
  can_add: boolean;
  can_change: boolean;
}

export interface PageResult<T> {
  count: number;
  results: T[];
  next: string | null;
}

export function locationPath(node: LocationNode) {
  return node.path.map((p) => p.name).join(' / ');
}
