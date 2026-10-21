import type { Page } from '@playwright/test';

export async function mockMachine(
  page: Page,
  options: {
    canChange?: boolean;
    linked?: boolean;
    longValues?: boolean;
    /** Registered identity; defaults to a standalone pump. */
    assetType?: 'pump' | 'pumphouse' | 'equipment';
    parent?: number | null;
    parentName?: string | null;
    sourceKey?: string;
  } = {}
) {
  const location = {
    pk: 12,
    client: 1,
    parent: null,
    name: 'Synthetic site',
    code: 'S0',
    kind: 'site',
    description: '',
    timezone: 'UTC',
    effective_timezone: 'UTC',
    archived: false,
    version: 1,
    path: [{ pk: 12, name: 'Synthetic site' }],
    has_children: false
  };
  const machine = {
    pk: 18,
    name: options.longValues
      ? `Synthetic pump ${'LONG-ASSET-REFERENCE-'.repeat(8)}`
      : 'Synthetic pump',
    active: true,
    client: 1,
    asset_type: options.assetType ?? 'pump',
    parent: options.parent ?? null,
    parent_name: options.parentName ?? null,
    source_key: options.sourceKey ?? '',
    location: 'Synthetic legacy label',
    manufacturer: 'Synthetic manufacturer',
    model: 'Synthetic model',
    serial: options.longValues ? 'S0-SERIAL-'.repeat(12) : 'S0-serial',
    description: 'Synthetic description retained in native Details',
    physical_location: location,
    placement_version: 3,
    barcode_hash: options.linked ? 'synthetic-linked-tag' : ''
  };
  await page.route('**/api/assets/machines/18/', (route) =>
    route.fulfill({ json: machine })
  );
  await page.route('**/api/assets/locations/context/', (route) =>
    route.fulfill({
      json: {
        workspaces: [{ pk: 1, name: 'Synthetic workspace' }],
        can_add: false,
        can_change: options.canChange ?? true
      }
    })
  );
  await page.route('**/api/assets/locations/machines/**', (route) =>
    route.fulfill({
      json: { count: 1, results: [machine], next: null }
    })
  );
  await page.route('**/api/assets/locations/?*', (route) =>
    route.fulfill({
      json: { count: 1, results: [location], next: null }
    })
  );
}
