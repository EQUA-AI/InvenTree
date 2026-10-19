import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';

nativeBaseline();

/** Registry component rows for pump 18, as the Parts tab reads them. */
function components(withStock: boolean) {
  const row = (pk: number, code: string, name: string, extra: object) => ({
    pk,
    uuid: `00000000-0000-4000-8000-${String(pk).padStart(12, '0')}`,
    machine: 18,
    machine_name: 'Synthetic pump',
    part: 500 + pk,
    part_name: name,
    virtual: false,
    code,
    name,
    status: 'draft',
    provenance: 'Inferred from source tags; physical assignment is unverified.',
    review_note: '',
    reviewed_at: null,
    reference_position: null,
    open_work: { count: 0 },
    stock: withStock ? { quantity: 0 } : null,
    ...extra
  });
  return [
    row(1, 'PS-MOTOR-DE-BRG:1', 'Motor Drive-End Bearing', {
      reference_position: 'bearing',
      open_work: { count: 1 },
      stock: withStock ? { quantity: 6 } : null
    }),
    row(2, 'PS-MOTOR:1', 'Electric Motor', {
      reference_position: 'motor',
      status: 'verified',
      provenance: 'Workbook population 4c1f00a2e9b1',
      open_work: null
    }),
    row(3, 'PS-THRUST-BRG:1', 'Thrust Bearing', {})
  ];
}

async function openParts(
  page: Page,
  {
    role,
    scheme,
    legacy = false
  }: { role?: 'viewer'; scheme?: 'dark'; legacy?: boolean } = {}
) {
  await mockMachine(page);
  const withStock = role !== 'viewer';
  // An older server sends none of the Parts context.
  const rows = components(withStock).map((record) => {
    if (!legacy) return record;
    const { reference_position, open_work, stock, ...rest } = record;
    return rest;
  });
  await page.route('**/api/assets/registry/18/components/**', (route) =>
    route.fulfill({ json: { count: rows.length, results: rows } })
  );
  await page.route('**/api/assets/parts/?*', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  const query = new URLSearchParams({
    surface: 'machine',
    entry: '/machines/machine/18/parts/'
  });
  if (role) query.set('role', role);
  if (scheme) query.set('scheme', scheme);
  await page.goto(`/playwright/s0-native.html?${query}`);
}

const row = (page: Page, code: string) =>
  page.getByRole('row').filter({ hasText: code });

test('the Parts tab keeps its route and says what is known about each slot', async ({
  page
}, testInfo) => {
  await openParts(page);
  await expect(
    page.getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tab', { name: 'Installed Parts' })).toHaveCount(
    0
  );
  for (const header of [
    '3D position',
    'Fitted item',
    'Fit basis',
    'Open work',
    'Catalogue stock'
  ])
    await expect(
      page.getByRole('columnheader', { name: header, exact: true })
    ).toBeVisible();

  const bearing = row(page, 'PS-MOTOR-DE-BRG:1');
  await expect(bearing).toContainText('Motor drive-end bearing');
  // Nothing records what is fitted: said, not filled in from the catalogue.
  await expect(bearing).toContainText('Not recorded');
  await expect(bearing.getByText('Draft', { exact: true })).toBeVisible();
  await expect(bearing.getByText('Fit not verified')).toBeVisible();
  await expect(bearing).toContainText('1 open');
  // The catalogue part opens its own native page; nothing is duplicated here.
  await expect(
    bearing.getByRole('link', { name: 'Motor Drive-End Bearing' })
  ).toHaveAttribute('href', /\/part\/501\/$/);
  await expect(
    bearing.getByRole('link', { name: '6 in stock' })
  ).toHaveAttribute('href', /\/part\/501\/stock$/);

  const motor = row(page, 'PS-MOTOR:1');
  await expect(motor.getByText('Verified', { exact: true })).toBeVisible();
  await expect(motor.getByText('Assignment verified')).toBeVisible();
  // Work that could not be checked is not shown as none.
  await expect(motor).toContainText('Not available');

  const thrust = row(page, 'PS-THRUST-BRG:1');
  await expect(thrust).toContainText('Not in the model');
  await expect(thrust).toContainText('None in stock');
  await expect(thrust).toContainText('None open');
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({
    path: testInfo.outputPath('parts-tab.png'),
    fullPage: true,
    animations: 'disabled'
  });
});

test('a reader without stock access sees no stock column and no part links', async ({
  page
}, testInfo) => {
  await openParts(page, { role: 'viewer', scheme: 'dark' });
  await expect(row(page, 'PS-MOTOR-DE-BRG:1')).toContainText(
    'Fit not verified'
  );
  await expect(
    page.getByRole('columnheader', { name: 'Catalogue stock' })
  ).toHaveCount(0);
  await expect(
    row(page, 'PS-MOTOR-DE-BRG:1').getByRole('link', {
      name: 'Motor Drive-End Bearing'
    })
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('parts-viewer-dark.png'),
    fullPage: true,
    animations: 'disabled'
  });
});

test('rows from a server without Parts context are not shown as known', async ({
  page
}) => {
  await openParts(page, { legacy: true });
  const bearing = row(page, 'PS-MOTOR-DE-BRG:1');
  await expect(bearing).toContainText('Fit not verified');
  // Not "Not in the model", "None open" or "None in stock": it was not said.
  await expect(bearing).not.toContainText('Not in the model');
  await expect(bearing).not.toContainText('None');
  await expect(bearing.getByText('Not available')).toHaveCount(3);
});
