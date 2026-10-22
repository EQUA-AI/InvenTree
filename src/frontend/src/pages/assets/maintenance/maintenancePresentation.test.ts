import { describe, expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray) => parts.join('')
}));
import type { AssetMaintenanceRecord } from '@lib/types/Assets';
import {
  emptyMaintenanceColumns,
  maintenanceMissingReasons
} from './maintenancePresentation';

const row = (changes: Partial<AssetMaintenanceRecord> = {}) =>
  ({
    pk: 1,
    summary: 'Bearing inspection',
    work_order: null,
    work_order_link_state: 'none',
    downtime_minutes: 0,
    ...changes
  }) as AssetMaintenanceRecord;

describe('maintenance page-level absence', () => {
  it('only hides columns missing with the same reason on every loaded row', () => {
    const missing = emptyMaintenanceColumns([
      row(),
      row({ pk: 2, work_order_component: 'Motor' })
    ]);
    expect(missing.work_order).toBe('No linked work order');
    expect(missing.work_order_component).toBeUndefined();
    expect(missing.downtime_minutes).toBeUndefined();
    expect(missing.summary).toBeUndefined();
  });
  it('never merges absent and withheld work links', () => {
    const missing = emptyMaintenanceColumns([
      row(),
      row({ work_order_link_state: 'unknown' })
    ]);
    expect(missing.work_order).toBeUndefined();
    expect(
      maintenanceMissingReasons(row({ work_order_link_state: 'unknown' }))
        .work_order
    ).toBe('Work order link not available');
  });
  it('keeps empty pages and real zero downtime honest', () => {
    expect(emptyMaintenanceColumns([])).toEqual({});
    expect(maintenanceMissingReasons(row()).downtime_minutes).toBeUndefined();
    expect(
      maintenanceMissingReasons(row({ downtime_minutes: null }))
        .downtime_minutes
    ).toBe('Downtime not recorded');
    expect(
      maintenanceMissingReasons(row({ follow_up_required: false }))
        .follow_up_required
    ).toBeUndefined();
    expect(
      emptyMaintenanceColumns([row({ follow_up_required: false })])
        .follow_up_required
    ).toBeUndefined();
  });
});
