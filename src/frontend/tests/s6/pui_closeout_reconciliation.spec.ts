import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { closeoutWork, containment, openCloseout } from './closeoutFixtures';

nativeBaseline();
const material = {
  work_order: 73,
  source: 'job_kit',
  kits_enabled: true,
  stock_visible: true,
  component: null,
  requirements: [],
  unmatched_usage: [
    {
      id: 90,
      part: null,
      stock_item: {
        pk: 91,
        hidden: false,
        serial: 'SER-91',
        batch: 'LOT-7',
        location: 'Quarantine store'
      },
      planned_quantity: null,
      issued_quantity: 1,
      used_quantity: null,
      disposition: null,
      variance_reason: 'Retained for analysis; no disposition recorded',
      source: 'walkup',
      candidate_text: 'Removed seal',
      state: 'variance_open',
      resolved_by: null,
      updated_at: '2026-10-19T09:00:00Z'
    }
  ]
};
for (const scheme of ['light', 'dark'] as const) {
  test(`P46 custody, unknown time and zero entered downtime stay distinct (${scheme})`, async ({
    page
  }, info) => {
    const { commands } = await closeoutWork(page);
    let materialReads = 0;
    await page.route(
      '**/api/tasks/work-orders/73/closeout/part-usage/',
      (route) =>
        route.fulfill({
          json: [
            {
              id: 90,
              source: 'walkup',
              state: 'variance_open',
              candidate_text: 'Removed seal',
              planned_quantity: null,
              issued_quantity: '1',
              used_quantity: null,
              disposition: '',
              version: 1
            }
          ]
        })
    );
    await page.route('**/api/tasks/work-orders/73/readiness/**', (route) =>
      route.fulfill({
        json: {
          ready: false,
          blockers: [
            {
              code: 'PART_VARIANCE_UNRESOLVED',
              message:
                'Material disposition remains unresolved; owner unavailable',
              blocking: true
            }
          ],
          warnings: []
        }
      })
    );
    await page.route('**/api/tasks/work-orders/73/materials/', (route) => {
      materialReads++;
      return route.fulfill({ json: material });
    });
    await openCloseout(page, scheme);
    const reconciliation = page.getByTestId('closeout-reconciliation');
    await expect(reconciliation).toContainText('Service downtime: unavailable');
    await expect(reconciliation).toContainText(
      'Attributed time: unavailable · coverage unknown'
    );
    expect(materialReads).toBe(0);
    await reconciliation
      .getByText('Read material custody and serial / lot records', {
        exact: true
      })
      .click();
    await expect(reconciliation).toContainText('SER-91');
    await expect(reconciliation).toContainText('LOT-7');
    expect(materialReads).toBe(1);
    await expect(
      reconciliation.getByRole('link', { name: /Stock item 91/ })
    ).toHaveAttribute('href', '/stock/item/91/');
    await expect(reconciliation).toContainText('Retained for analysis');
    await expect(reconciliation).toContainText('variance_open');
    await expect(reconciliation).toContainText('Discrepancy owners');
    await page
      .getByRole('textbox', { name: 'Downtime (minutes)', exact: true })
      .fill('0');
    await page.getByText('Read report', { exact: true }).click();
    await expect(
      page.getByTestId('prepared-closeout').locator('pre')
    ).toContainText('Downtime (minutes): 0');
    await expect(reconciliation).toContainText('Service downtime: unavailable');
    expect(commands).toEqual([]);
    await page.getByText('Read report', { exact: true }).click();
    await containment(page);
    await reconciliation.evaluate((el) =>
      window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 200)
    );
    await page.screenshot({
      path: info.outputPath(`reconciliation-detail-${scheme}.png`)
    });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`reconciliation-${scheme}.png`),
      fullPage: true
    });
  });
}

test('P46 denied custody is unavailable and never turns into usable or returned stock', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Denied' } })
  );
  await openCloseout(page);
  const reconciliation = page.getByTestId('closeout-reconciliation');
  await reconciliation
    .getByText('Read material custody and serial / lot records', {
      exact: true
    })
    .click();
  await expect(reconciliation).toContainText(
    'Parts for this job could not be read'
  );
  await expect(reconciliation).not.toContainText('No parts are required');
  expect(commands).toEqual([]);
  await containment(page);
  await page.screenshot({
    path: info.outputPath('custody-denied.png'),
    fullPage: true
  });
});
