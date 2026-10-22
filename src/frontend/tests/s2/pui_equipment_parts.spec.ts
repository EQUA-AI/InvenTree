import { type Page, expect, test } from '@playwright/test';
import { readable } from '../s8/contrast';
import { nativeThemeFixtures } from '../s8/themeFixtures';

import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { components } from './partsFixtures';

nativeBaseline();
nativeThemeFixtures();
test.use({ timezoneId: 'UTC' });

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
    legacy = false,
    expand = true,
    nativeShell = false,
    assetType = 'pump'
  }: {
    role?: 'viewer';
    scheme?: 'light' | 'dark';
    legacy?: boolean;
    expand?: boolean;
    nativeShell?: boolean;
    assetType?: 'pump' | 'pumphouse';
  } = {}
) {
  await mockMachine(page, { assetType });
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
    surface: nativeShell ? 'machine-shell' : 'machine',
    entry: '/machines/machine/18/parts/'
  });
  if (nativeShell) {
    query.set('nativeTheme', 'true');
    query.set('compactChrome', 'true');
  }
  if (role) query.set('role', role);
  if (scheme) query.set('scheme', scheme);
  await gotoParts(page, `/playwright/s0-native.html?${query}`);
  if (expand && page.viewportSize()!.width <= 768) {
    for (const details of await page.locator('details').all()) {
      await details.locator('summary').click();
      await expect(details).toHaveAttribute('open', '');
    }
  }
}

const row = (page: Page, code: string) => {
  const record = components(true).find((component) => component.code === code)!;
  return page.getByRole('row').filter({
    has: page.getByRole('button', { name: record.name, exact: true })
  });
};

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
    ...(page.viewportSize()!.width > 768 ? ['3D position', 'Fitted item'] : []),
    'Fit basis',
    'Open work',
    'Catalogue stock'
  ])
    await expect(
      page.getByRole('columnheader', { name: header, exact: true })
    ).toBeVisible();

  const bearing = row(page, 'PS-MOTOR-DE-BRG:1');
  await expect(bearing).toContainText('Motor drive-end bearing');
  // The dash still explains absence accessibly; the catalogue is not a fit.
  await expect(
    bearing.getByRole('button', {
      name: 'No verified fitted identity is recorded for this slot.',
      exact: true
    })
  ).toHaveText('—');
  await expect(
    bearing.getByLabel('Draft: slot assignment not verified', { exact: true })
  ).toHaveText('Draft');
  await expect(bearing.locator('[data-field="open_work"]')).toHaveText('1');
  // The catalogue part opens its own native page; nothing is duplicated here.
  await expect(
    bearing.getByRole('link', { name: 'Motor Drive-End Bearing' })
  ).toHaveAttribute('href', /\/part\/501\/$/);
  await expect(
    bearing.getByRole('link', { name: '6 in stock' })
  ).toHaveAttribute('href', /\/part\/501\/stock$/);

  const motor = row(page, 'PS-MOTOR:1');
  await expect(
    motor.getByLabel('Verified: assignment verified', { exact: true })
  ).toHaveText('Verified');
  // Work that could not be checked is not shown as none.
  await expect(
    motor.getByRole('button', {
      name: 'Open work: Read failed',
      exact: true
    })
  ).toHaveText('Read failed');
  await expect(motor.locator('[data-field="provenance"]')).toHaveText(
    'Registry import'
  );

  const thrust = row(page, 'PS-THRUST-BRG:1');
  await expect(
    thrust.getByRole('button', { name: 'Not in the model', exact: true })
  ).toHaveText('—');
  await expect(thrust.locator('[data-field="stock"]')).toHaveText('0');
  await expect(thrust.locator('[data-field="open_work"]')).toHaveText('0');
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
  await expect(
    row(page, 'PS-MOTOR-DE-BRG:1').getByLabel(
      'Draft: slot assignment not verified',
      { exact: true }
    )
  ).toHaveText('Draft');
  await expect(
    row(page, 'PS-MOTOR-DE-BRG:1').getByRole('button', {
      name: 'Fitted identity is unavailable for your role.',
      exact: true
    })
  ).toHaveText('—');
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
  await expect(
    bearing.getByLabel('Draft: slot assignment not verified', { exact: true })
  ).toHaveText('Draft');
  // Not "Not in the model", "None open" or "None in stock": it was not said.
  await expect(bearing).not.toContainText('Not in the model');
  await expect(bearing).not.toContainText('None');
  for (const label of [
    '3D position',
    'Fitted item',
    'Open work',
    'Catalogue stock'
  ]) {
    await expect(
      bearing.getByRole('button', {
        name: `${label}: Read failed`,
        exact: true
      })
    ).toHaveText('Read failed');
  }
});

for (const scheme of ['light', 'dark'] as const) {
  test(`Parts facts keep keyboard explanations and provenance within the viewport (${scheme})`, async ({
    page
  }, info) => {
    await openParts(page, { scheme });
    const thrust = row(page, 'PS-THRUST-BRG:1');
    await expect(
      thrust.getByRole('button', { name: 'Inferred SCADA', exact: true })
    ).toBeVisible();
    await readable(thrust.getByText('Draft', { exact: true }));
    await readable(
      thrust.getByRole('button', { name: 'Thrust Bearing', exact: true })
    );
    await readable(
      thrust.getByRole('link', { name: 'Thrust Bearing', exact: true })
    );
    await expect(thrust).not.toContainText('PS-THRUST-BRG:1');
    const trigger = thrust.getByRole('button', {
      name: 'Thrust Bearing',
      exact: true
    });
    await trigger.focus();
    await trigger.press('Enter');
    const detail = page.getByRole('dialog', {
      name: 'Thrust Bearing',
      exact: true
    });
    await expect(detail).toContainText('PS-THRUST-BRG:1');
    const drawerReason = detail.getByRole('button', {
      name: 'Not in the 3D model',
      exact: true
    });
    await page.keyboard.press('Tab');
    await drawerReason.focus();
    await expect(
      page.getByRole('tooltip', { name: 'Not in the 3D model', exact: true })
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(detail).toHaveCount(0);
    const missing = thrust.getByRole('button', {
      name: 'Not in the model',
      exact: true
    });
    await expect(missing).toHaveAttribute('tabindex', '-1');
    await page.keyboard.press('Tab');
    await missing.focus();
    await expect(missing).toBeFocused();
    await expect(
      page.getByRole('tooltip', { name: 'Not in the model', exact: true })
    ).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    const draft = thrust.getByLabel('Draft: slot assignment not verified', {
      exact: true
    });
    await page.keyboard.press('Tab');
    await draft.focus();
    await expect(draft).toBeFocused();
    await expect(
      page.getByRole('tooltip', {
        name: 'Slot assignment not verified',
        exact: true
      })
    ).toBeVisible();

    const motor = row(page, 'PS-MOTOR:1');
    const source = motor.getByRole('button', {
      name: 'Registry import',
      exact: true
    });
    await expect(motor.locator('[data-field="provenance"]')).not.toContainText(
      '4c1f00a2e9b1'
    );
    await page.keyboard.press('Tab');
    await source.focus();
    await expect(source).toBeFocused();
    await expect(
      page.getByRole('tooltip', {
        name: /Workbook population 4c1f00a2e9b1 · 10 Oct 2026 [0-9:]+ · your clock/
      })
    ).toBeVisible();
    await source.click();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    const width = page.viewportSize()!.width;
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(width);
    for (const code of ['PS-MOTOR-DE-BRG:1', 'PS-MOTOR:1', 'PS-THRUST-BRG:1']) {
      const bounds = await row(page, code).boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1);
    }
    if (width <= 768) {
      for (const target of [missing, draft, source]) {
        const bounds = await target.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.height).toBeGreaterThanOrEqual(44);
        expect(bounds!.width).toBeGreaterThanOrEqual(44);
      }
      // The table becomes stacked facts, with its name and source spanning it.
      const name = await motor
        .getByRole('button', { name: 'Electric Motor', exact: true })
        .boundingBox();
      const provenance = await motor
        .locator('[data-field="provenance"]')
        .boundingBox();
      const motorBounds = await motor.boundingBox();
      expect(motorBounds).not.toBeNull();
      // Full-span content uses the row's width, allowing its 24 px padding
      // and a small border/rounding tolerance, rather than old desktop widths.
      for (const field of ['name', 'part_name', 'provenance']) {
        const bounds = await motor
          .locator(`[data-field="${field}"]`)
          .boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.width).toBeGreaterThanOrEqual(motorBounds!.width - 32);
      }
      expect(name).not.toBeNull();
      expect(provenance).not.toBeNull();
      expect(provenance!.y).toBeGreaterThan(name!.y + name!.height);
    }
    await page.screenshot({
      path: info.outputPath(`parts-facts-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  });
}

test('desktop Parts header text fits the native shell and numeric columns stay right aligned', async ({
  page
}, info) => {
  test.skip(info.project.name.endsWith('phone'), 'Desktop column geometry');
  for (const assetType of ['pumphouse', 'pump'] as const) {
    await openParts(page, { nativeShell: true, assetType });
    const stock = page.getByRole('columnheader', {
      name: 'Catalogue stock',
      exact: true
    });
    await expect(stock).toHaveText('Catalogue stock');
    for (const [label, accessor] of [
      ['Open work', 'open_work'],
      ['Catalogue stock', 'stock']
    ]) {
      // Native DataTable applies header alignment to its text element.
      await expect(
        page
          .getByRole('columnheader', { name: label, exact: true })
          .getByText(label, { exact: true })
      ).toHaveCSS('text-align', 'right');
      await expect(
        page.locator(`tbody td:has([data-field="${accessor}"])`).first()
      ).toHaveCSS('text-align', 'right');
    }
    const failure = page.getByRole('button', {
      name: 'Open work: Read failed',
      exact: true
    });
    const failureLabel = failure.getByText('Read failed', { exact: true });
    const lines = await failureLabel.evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return Array.from(range.getClientRects()).filter(
        (rect) => rect.width && rect.height
      ).length;
    });
    expect(lines, 'Read failed remains a compact whole phrase').toBe(1);
    for (const header of await page.getByRole('columnheader').all()) {
      const result = await header.evaluate((cell) => {
        const bounds = cell.getBoundingClientRect();
        const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        const overflow: string[] = [];
        let visibleText = 0;
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          if (!node.textContent?.trim()) continue;
          const range = document.createRange();
          range.selectNodeContents(node);
          for (const rect of range.getClientRects()) {
            if (!rect.width || !rect.height) continue;
            visibleText += 1;
            if (
              rect.left < bounds.left - 1 ||
              rect.right > bounds.right + 1 ||
              rect.top < bounds.top - 1 ||
              rect.bottom > bounds.bottom + 1
            )
              overflow.push(node.textContent.trim());
          }
        }
        return { visibleText, overflow };
      });
      expect(result.visibleText).toBeGreaterThan(0);
      expect(
        result.overflow,
        `${assetType}: full header text stays inside its cell`
      ).toEqual([]);
    }
  }
});

for (const assetType of ['pump', 'pumphouse'] as const) {
  test(`phone Parts leads with a complete compact card and reveals every secondary fact by keyboard (${assetType})`, async ({
    page
  }, info) => {
    test.skip(!info.project.name.endsWith('phone'), 'Phone disclosure layout');
    await openParts(page, { expand: false, assetType });
    const bearing = row(page, 'PS-MOTOR-DE-BRG:1');
    await expect(bearing.locator('td:visible').first()).toContainText(
      'Motor Drive-End Bearing'
    );
    for (const cell of await bearing.locator('td:visible').all())
      await expect(cell).toHaveCSS('border-bottom-width', '0px');
    const details = bearing.locator('details');
    await expect(details).not.toHaveAttribute('open');
    await expect(
      bearing.locator('[data-field="reference_position"]')
    ).toBeHidden();
    await expect(bearing.locator('[data-field="fitted_item"]')).toBeHidden();
    await expect(bearing.locator('[data-field="provenance"]')).toBeHidden();
    if (assetType === 'pumphouse')
      await expect(bearing.locator('[data-field="machine_name"]')).toBeHidden();
    await page.evaluate(() => window.scrollTo(0, 0));
    const bounds = (await bearing.boundingBox())!;
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
    const disclosure = details.locator('summary');
    await disclosure.focus();
    await expect(disclosure).toHaveAccessibleName(
      'Details for Motor Drive-End Bearing'
    );
    await disclosure.press('Enter');
    await expect(details).toHaveAttribute('open', '');
    if (assetType === 'pumphouse')
      await expect(
        bearing.locator('[data-field="machine_name"]')
      ).toBeVisible();
    await expect(
      bearing.locator('[data-field="reference_position"]')
    ).toContainText('Motor drive-end bearing');
    await expect(
      bearing.getByRole('button', {
        name: 'No verified fitted identity is recorded for this slot.',
        exact: true
      })
    ).toHaveText('—');
    await expect(bearing.locator('[data-field="provenance"]')).toHaveText(
      'Inferred SCADA'
    );
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await disclosure.press('Enter');
    await expect(details).not.toHaveAttribute('open');
  });
}

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
  await expect(
    detail.getByRole('button', {
      name: 'Draft: slot assignment not verified',
      exact: true
    })
  ).toHaveText('Draft');
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

  // A noninteractive primary fact opens the row on both table and card.
  // Secondary facts live in the phone disclosure, whose clicks stay local.
  await row(page, 'PS-MOTOR:1')
    .locator('[data-field="stock"]')
    .getByText('0', { exact: true })
    .click();
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
    await expect(
      detail.getByRole('button', {
        name: 'Draft: slot assignment not verified',
        exact: true
      })
    ).toHaveText('Draft');
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
