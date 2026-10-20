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
    fitted: withStock
      ? { version: 0, current: null, history: [], pending: [] }
      : null,
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

/** Wait for the mocked provider read before asserting domain values. */
async function gotoParts(page: Page, url: string) {
  const [response] = await Promise.all([
    page.waitForResponse(
      (reply) =>
        new URL(reply.url()).pathname === '/api/assets/registry/18/components/',
      { timeout: 60_000 }
    ),
    page.goto(url)
  ]);
  await response.finished();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0', {
    timeout: 60_000
  });
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
    const { reference_position, open_work, stock, fitted, ...rest } = record;
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
  await gotoParts(page, `/playwright/s0-native.html?${query}`);
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
  await expect(bearing.getByText('Slot assignment not verified')).toBeVisible();
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
    'Slot assignment not verified'
  );
  await expect(row(page, 'PS-MOTOR-DE-BRG:1')).toContainText('Not available');
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
  await expect(bearing).toContainText('Slot assignment not verified');
  // Not "Not in the model", "None open" or "None in stock": it was not said.
  await expect(bearing).not.toContainText('Not in the model');
  await expect(bearing).not.toContainText('None');
  await expect(bearing.getByText('Not available')).toHaveCount(4);
});

const destination = (page: Page) => page.getByTestId('destination');
const slotDialog = (page: Page) =>
  page.getByRole('dialog', { name: 'Motor Drive-End Bearing' });

test('a slot opens its own detail, and its native records lead back to it', async ({
  page
}, testInfo) => {
  const reads: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/assets/registry/18/components/'))
      reads.push(request.url());
  });
  await openParts(page);
  await row(page, 'PS-MOTOR-DE-BRG:1')
    .getByRole('button', { name: 'Motor Drive-End Bearing' })
    .click();
  const detail = slotDialog(page);
  await expect(detail).toBeVisible();
  await expect(destination(page)).toHaveText(
    '/machines/machine/18/parts/?slot=1'
  );
  await expect(detail).toContainText('Motor drive-end bearing');
  await expect(
    detail.getByRole('link', { name: 'View in 3D' })
  ).toHaveAttribute('href', '/machines/machine/18/equipment/?position=bearing');
  // Each fact on its own: nothing fitted is recorded; the family is not it.
  await expect(detail).toContainText('Not recorded: no fitted part');
  await expect(detail).toContainText('not what is fitted');
  await expect(detail).toContainText('Slot assignment not verified');
  await expect(detail).toContainText('1 open job(s) name this slot.');
  await expect(detail).toContainText(
    '6 in stock for this catalogue part, shared by every slot that uses it.'
  );
  await expect(
    detail.getByRole('link', { name: 'Open stock' })
  ).toHaveAttribute('href', '/part/501/stock');
  // The open slot is not a table filter, here or in what is read.
  await expect(page.getByText('Custom table filters are active')).toHaveCount(
    0
  );
  expect(reads.length).toBeGreaterThan(0);
  for (const url of reads)
    expect(new URL(url).searchParams.has('slot')).toBe(false);
  await page.screenshot({
    path: testInfo.outputPath('slot-detail.png'),
    animations: 'disabled'
  });

  await detail.getByRole('link', { name: 'Open part record' }).click();
  await expect(destination(page)).toHaveText('/part/501/');
  const back = page.getByRole('link', {
    name: 'Back to Synthetic pump · Parts'
  });
  await expect(back).toBeVisible();
  await back.click();
  // Back where it was: the same slot open on the same tab.
  await expect(destination(page)).toHaveText(
    '/machines/machine/18/parts/?slot=1'
  );
  await expect(slotDialog(page)).toBeVisible();
});

test('closing a slot returns focus, and row clicks never hijack a link', async ({
  page
}) => {
  await openParts(page);
  const trigger = row(page, 'PS-MOTOR-DE-BRG:1').getByRole('button', {
    name: 'Motor Drive-End Bearing'
  });
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(slotDialog(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(slotDialog(page)).toHaveCount(0);
  await expect(destination(page)).toHaveText('/machines/machine/18/parts/');
  await expect(trigger).toBeFocused();

  // Anywhere else on the row opens it too.
  await row(page, 'PS-MOTOR:1').getByText('Workbook population').click();
  await expect(
    page.getByRole('dialog', { name: 'Electric Motor' })
  ).toBeVisible();
  await page.keyboard.press('Escape');

  // A link in the row goes where it says, with no slot opened on the way.
  await row(page, 'PS-MOTOR-DE-BRG:1')
    .getByRole('link', { name: '6 in stock' })
    .click();
  await expect(destination(page)).toHaveText('/part/501/stock');
});

test('a slot that is not in the list says so', async ({ page }) => {
  await mockMachine(page);
  await page.route('**/api/assets/registry/18/components/**', (route) =>
    route.fulfill({ json: { count: 3, results: components(true) } })
  );
  await page.route('**/api/assets/parts/?*', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  await page.goto(
    `/playwright/s0-native.html?${new URLSearchParams({
      surface: 'machine',
      entry: '/machines/machine/18/parts/?slot=999'
    })}`
  );
  await expect(page.getByRole('dialog')).toContainText(
    'This component is not in the list shown.'
  );
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 fitted serial and lot, date and replacement history (${scheme})`, async ({
    page
  }, info) => {
    await mockMachine(page);
    const fit = {
      id: 41,
      stock_item: 91,
      serial: 'SER-91',
      lot: 'LOT-7',
      fitted_at: '2026-10-18T09:00:00Z',
      work_order: null,
      recorded_by: 'Synthetic technician',
      recorded_at: '2026-10-18T09:30:00Z',
      verified_by: 'Synthetic engineer',
      verified_at: '2026-10-18T10:00:00Z',
      source: 'manual',
      reason: 'Verified label and fit evidence'
    };
    const rows = components(true).map((r) => ({
      ...r,
      fitted: {
        version: 3,
        current: r.pk === 1 ? fit : null,
        history:
          r.pk === 1
            ? [
                {
                  ...fit,
                  id: 40,
                  stock_item: 90,
                  serial: 'SER-90',
                  fitted_at: '2026-09-18T09:00:00Z'
                }
              ]
            : [],
        pending: []
      }
    }));
    await page.route('**/api/assets/registry/18/components/**', (route) =>
      route.fulfill({ json: { count: rows.length, results: rows } })
    );
    await page.route('**/api/assets/parts/?*', (route) =>
      route.fulfill({ json: { count: 0, results: [], next: null } })
    );
    await gotoParts(
      page,
      `/playwright/s0-native.html?surface=machine&entry=/machines/machine/18/parts/&scheme=${scheme}`
    );
    await expect(row(page, 'PS-MOTOR-DE-BRG:1')).toContainText('SER-91');
    await row(page, 'PS-MOTOR-DE-BRG:1')
      .getByRole('button', { name: 'Motor Drive-End Bearing', exact: true })
      .click();
    const detail = page.getByRole('dialog', {
      name: 'Motor Drive-End Bearing'
    });
    await expect(detail.getByTestId('fitted-identity')).toContainText(
      'Synthetic technician'
    );
    await expect(detail.getByTestId('fitted-identity')).toContainText(
      'Synthetic engineer'
    );
    await expect(detail).toContainText('Slot assignment not verified');
    await detail.getByText('Replacement history (1)', { exact: true }).click();
    await expect(detail).toContainText('SER-90');
    await expect(
      detail.getByRole('link', { name: 'Stock item 91', exact: true })
    ).toHaveAttribute('href', '/stock/item/91/');
    await page.screenshot({
      path: info.outputPath(`o15-fitted-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  });
}

test('O15 a manually recorded existing fit stays pending verification', async ({
  page
}, info) => {
  await mockMachine(page);
  const rows = components(true).map((r) => ({
    ...r,
    fitted: { version: 0, current: null, history: [], pending: [] }
  }));
  await page.route('**/api/assets/registry/18/components/**', (route) =>
    route.fulfill({ json: { count: rows.length, results: rows } })
  );
  await page.route('**/api/assets/parts/?*', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  const sent: unknown[] = [];
  await page.route('**/api/assets/recorded/18/slots/1/fits/', async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        version: 1,
        current: null,
        history: [],
        pending: [
          {
            id: 41,
            stock_item: 91,
            serial: 'SER-91',
            lot: 'LOT-7',
            fitted_at: '2026-10-18T09:00:00Z',
            work_order: null,
            recorded_by_id: 11,
            recorded_by: 'Synthetic technician',
            recorded_at: '2026-10-18T09:30:00Z',
            verified_by: null,
            verified_at: null,
            source: 'manual',
            reason: 'Read fitted label'
          }
        ]
      }
    });
  });
  await gotoParts(
    page,
    '/playwright/s0-native.html?surface=machine&entry=/machines/machine/18/parts/'
  );
  await row(page, 'PS-MOTOR-DE-BRG:1')
    .getByRole('button', { name: 'Motor Drive-End Bearing', exact: true })
    .click();
  const detail = page.getByRole('dialog', { name: 'Motor Drive-End Bearing' });
  await detail
    .getByText('Record existing fit for verification', { exact: true })
    .click();
  await detail
    .getByRole('textbox', { name: 'Native stock item ID', exact: true })
    .fill('91');
  await detail
    .getByRole('textbox', {
      name: 'Fitted date (ISO time with zone)',
      exact: true
    })
    .fill('2026-10-18T09:00:00Z');
  await detail
    .getByRole('textbox', { name: 'Fit evidence / reason', exact: true })
    .fill('Read fitted label');
  await detail.getByRole('button', { name: 'Record pending fit' }).click();
  await expect(detail).toContainText('Pending verification — not current fit');
  await expect(
    detail.getByRole('button', { name: 'Verify existing fit' })
  ).toBeDisabled();
  await expect(detail).toContainText(
    'A different authorized registry reviewer must verify this entry.'
  );
  await expect(detail).toContainText('No verified current fit is recorded.');
  await expect(detail).not.toContainText('no fitted part, serial, lot');
  await page.screenshot({
    path: info.outputPath('o15-manual-fit-pending.png'),
    fullPage: true,
    animations: 'disabled'
  });
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({
    operation: 'record',
    expected_version: 0,
    fields: { stock_item: 91 }
  });
});
