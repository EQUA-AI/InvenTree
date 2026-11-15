/** Optional metadata does not enroll a reader or acknowledge a native move. */
export interface PlacementRow {
  pk: number;
  client: number | null;
  placement_version: number;
  physical_location: { pk: number } | null;
  placement_sync?: unknown;
}

export type PlacementSyncState = 'pending' | 'current' | 'unqualified' | null;

export interface PlacementSyncWire {
  schema_version: 'aimms.placement.sync.v1';
  state: Exclude<PlacementSyncState, null>;
  machine?: string;
  client?: string;
  location?: string | null;
  native_version?: string;
  source_fence?: string;
}

function sourceId(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

/** Unknown evidence is never pending/current, and absence stays default off. */
export function placementPending(row: PlacementRow): PlacementSyncState {
  const value = row.placement_sync;
  if (value === undefined || value === null) return null;
  if (typeof value !== 'object') return 'unqualified';
  const metadata = value as Partial<PlacementSyncWire>;
  if (
    metadata.schema_version !== 'aimms.placement.sync.v1' ||
    !['pending', 'current'].includes(metadata.state ?? '')
  ) {
    return 'unqualified';
  }
  if (
    !sourceId(row.pk) ||
    !sourceId(row.client) ||
    !Number.isSafeInteger(row.placement_version) ||
    row.placement_version < 0 ||
    metadata.machine !== String(row.pk) ||
    metadata.client !== String(row.client) ||
    metadata.native_version !== String(row.placement_version) ||
    typeof metadata.source_fence !== 'string' ||
    metadata.source_fence.length > 19 ||
    !/^[1-9][0-9]*$/.test(metadata.source_fence) ||
    (row.physical_location === null
      ? metadata.location !== null
      : !sourceId(row.physical_location?.pk) ||
        metadata.location !== String(row.physical_location.pk))
  ) {
    return 'unqualified';
  }
  return metadata.state === 'current' ? 'current' : 'pending';
}
