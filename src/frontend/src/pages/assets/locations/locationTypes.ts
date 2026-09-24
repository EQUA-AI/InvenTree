import type { AssetMachine } from '@lib/types/Assets';

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
  physical_location: LocationNode | null;
  placement_version: number;
}

export interface LocationContext {
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
