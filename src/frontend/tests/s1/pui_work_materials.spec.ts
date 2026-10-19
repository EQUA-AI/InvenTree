import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { workOrder } from '../s0/workFixtures';
import { mockWorkList } from './workListFixtures';

nativeBaseline();

/** R6 P22: the job's material readout, as the server records it. */
function readout(overrides: Record<string, unknown> = {}) {
  const part = {
    pk: 5,
    name: 'Mechanical seal kit',
    ipn: 'PS-SEAL',
    units: 'ea',
    virtual: false
  };
  const stock = {
    pk: 91,
    hidden: false,
    serial: '',
    batch: 'B-7',
    location: 'Store A / Bay 2',
    location_pk: 4
  };
  return {
    work_order: 73,
    source: 'job_kit',
    kits_enabled: true,
    stock_visible: true,
    component: {
      ref: 'PS-MECH-SEAL:1',
      pk: 12,
      name: 'Mechanical Seal',
      machine: 18
    },
    requirements: [
      {
        key: 'line-1',
        part,
        requested_part: null,
        quantity: 2,
        required: true,
        fit_basis: {
          source: 'procedure',
          substitution_policy: 'none',
          note: ''
        },
        reservations: [
          {
            id: 1,
            quantity: 1,
            status: 'consumed',
            stock_item: stock,
            reserved: { by: 'planner', at: '2026-10-19T08:00:00Z' },
            staged: null,
            issued: { by: null, at: '2026-10-19T09:00:00Z' },
            closed: {
              status: 'consumed',
              by: 'tech-a',
              at: '2026-10-19T10:00:00Z',
              tracking_id: 400
            },
            usage: [
              {
                id: 3,
                part,
                stock_item: stock,
                planned_quantity: 1,
                issued_quantity: 1,
                used_quantity: 1,
                disposition: 'consumed',
                variance_reason: '',
                source: 'kit',
                candidate_text: '',
                state: 'reconciled',
                resolved_by: 'tech-a',
                updated_at: '2026-10-19T10:05:00Z'
              }
            ]
          },
          {
            id: 2,
            quantity: 1,
            status: 'reserved',
            stock_item: stock,
            reserved: { by: 'planner', at: '2026-10-19T08:00:00Z' },
            staged: null,
            issued: null,
            closed: null,
            usage: []
          }
        ],
        shortages: [],
        availability_check: null
      }
    ],
    unmatched_usage: [],
    ...overrides
  };
}

async function openParts(
  page: Page,
  {
    body = readout(),
    status = 200,
    role
  }: { body?: object; status?: number; role?: 'viewer' } = {}
) {
  await mockWorkList(page);
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ json: workOrder() })
  );
  const reads: string[] = [];
  await page.route('**/api/tasks/work-orders/73/**', (route) => {
    reads.push(new URL(route.request().url()).pathname);
    return route.request().url().includes('/materials/')
      ? route.fulfill({ status, json: status === 200 ? body : { detail: 'x' } })
      : route.fulfill({ json: [] });
  });
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    work_access: 'reader',
    entry: '/maintenance/work-orders/73/?work_panel=parts'
  });
  if (role) params.set('role', role);
  await page.goto(`/playwright/s0-native.html?${params}`);
  return reads;
}

const requirement = (page: Page) => page.locator('[data-requirement="line-1"]');

test('each material stage keeps its own record and recorder', async ({
  page
}, testInfo) => {
  const reads = await openParts(page);
  const line = requirement(page);
  await expect(line).toContainText('Mechanical seal kit');
  await expect(line).toContainText('Required 2 ea');
  await expect(line).toContainText('Reserved 2 ea of 2 ea');
  const used = line.locator('[data-reservation="1"]');
  await expect(used).toContainText('planner');
  // Issue records when, not who; use comes with its recorder.
  await expect(used).toContainText('who not recorded');
  await expect(used).toContainText('tech-a');
  await expect(used).toContainText('Closeout record: issued 1 ea, used 1 ea');
  const live = line.locator('[data-reservation="2"]');
  await expect(live).toContainText('not yet');
  await expect(
    used
      .getByRole('link', {
        name: /Stock item 91 · Batch B-7 · Store A \/ Bay 2/
      })
      .first()
  ).toHaveAttribute('href', '/stock/item/91/');
  await expect(page.locator('[data-covered]')).toHaveText(
    'Available stock used for this job'
  );
  await expect(
    page.getByText('Fitted on the machine is not recorded separately from use.')
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Mechanical Seal', exact: true })
  ).toHaveAttribute('href', '/machines/machine/18/parts/?slot=12');
  // No routine manual chores in the main path.
  for (const chore of [
    'Check usable stock',
    'Reserve',
    'Select offer',
    'Record installed'
  ])
    await expect(page.getByRole('button', { name: chore })).toHaveCount(0);
  expect(reads).toEqual(['/api/tasks/work-orders/73/materials/']);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({
    path: testInfo.outputPath('work-materials.png'),
    fullPage: true,
    animations: 'disabled'
  });

  await line
    .getByRole('link', { name: 'Open part record: Mechanical seal kit' })
    .click();
  await expect(page.getByTestId('destination')).toHaveText('/part/5/');
  await page.getByRole('link', { name: 'Back to WO-73 · Parts' }).click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/73/?work_panel=parts'
  );
  await expect(requirement(page)).toBeVisible();
});

test('a short line names its owner and never says stock covered the job', async ({
  page
}) => {
  const body = readout();
  const [line] = body.requirements as any[];
  line.reservations = [line.reservations[1]];
  line.shortages = [
    {
      id: 8,
      quantity: 1,
      status: 'ordered',
      purchase_order_line: 31,
      reason: ''
    }
  ];
  await openParts(page, { body });
  await expect(requirement(page)).toContainText('Reserved 1 ea of 2 ea');
  await expect(requirement(page)).toContainText(
    '1 ea · ordered · purchase order line 31'
  );
  await expect(page.locator('[data-covered]')).toHaveCount(0);
});

test('without a job kit a stock check is not shown as a reservation', async ({
  page
}) => {
  await openParts(page, {
    body: readout({
      source: 'requirements',
      kits_enabled: false,
      requirements: [
        {
          key: 'line-1',
          part: {
            pk: 5,
            name: 'Mechanical seal kit',
            ipn: '',
            units: 'ea',
            virtual: false
          },
          requested_part: null,
          quantity: 2,
          required: true,
          fit_basis: null,
          reservations: null,
          shortages: null,
          availability_check: {
            status: 'full',
            quantity: 2,
            note: 'Fully allocated: 2 available in stock',
            checked_at: '2026-10-19T07:00:00Z'
          }
        }
      ]
    })
  });
  const line = requirement(page);
  await expect(line).toContainText(
    'Not reserved: no stock item is reserved to this requirement.'
  );
  await expect(line).toContainText(
    'Stock check (on-hand count, not a reservation)'
  );
  await expect(line).toContainText(
    'Fit basisNot recorded for this requirement'
  );
  await expect(page.locator('[data-covered]')).toHaveCount(0);
});

test('a reader without stock or part roles sees quantities, not items or links', async ({
  page
}, testInfo) => {
  const body = readout({ stock_visible: false });
  for (const reservation of (body.requirements as any[])[0].reservations) {
    reservation.stock_item = { pk: null, hidden: true };
    for (const usage of reservation.usage)
      usage.stock_item = { pk: null, hidden: true };
  }
  await openParts(page, { body, role: 'viewer' });
  await expect(requirement(page)).toContainText(
    'Stock item not available to you'
  );
  await expect(
    page.getByRole('link', { name: /Open part record/ })
  ).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Stock item/ })).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('work-materials-viewer.png'),
    fullPage: true,
    animations: 'disabled'
  });
});

test('a server without the readout keeps the native requirement table', async ({
  page
}) => {
  await openParts(page, { status: 404 });
  await expect(
    page.getByText('Detailed material records are not available on this server')
  ).toBeVisible();
});

test('a failed read shows nothing old and offers retry', async ({ page }) => {
  await openParts(page, { status: 500 });
  await expect(
    page.getByText('Parts for this job could not be read')
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
  await expect(requirement(page)).toHaveCount(0);
});
