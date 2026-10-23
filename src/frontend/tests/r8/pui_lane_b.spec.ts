import { resolve } from 'node:path';
import { type Page, expect, test } from '@playwright/test';
const captures = resolve(
  import.meta.dirname,
  '../../../../data/devscratch/r8-w2b/captures'
);
const receivePath = '**/api/order/po/81/receive/';
const original = {
  maintenance_operation_key: 'fixture-stable-receipt',
  maintenance_requirement_version: 7,
  location: 42,
  items: [
    { line_item: 91, quantity: '1', batch_code: 'FIXTURE-BATCH', status: 10 }
  ]
};
const baseContext = {
  maintenance: true,
  binding: {
    work_order: 73,
    reference: 'FIXTURE-WO-73',
    kit_line: 21,
    purchase_order_line: 91,
    requirement_version: 7
  },
  receipts: [] as any[]
};
const stock = {
  stock_item: 301,
  quantity: '1',
  batch: 'FIXTURE-BATCH',
  location: 42,
  status: 10,
  status_custom_key: null,
  expiry_date: null
};
const receipt = (body = original, state = 'pending') => ({
  id: 801,
  operation_key: body.maintenance_operation_key,
  actor: 11,
  recorded_at: '2026-10-23T08:00:00Z',
  request: body,
  reservation_state: state,
  reservation_code: state === 'allocated' ? '' : 'RESERVATION_FAILED',
  stocks: [stock],
  allocation_ids: state === 'allocated' ? [401] : []
});
async function common(page: Page) {
  await page.route('https://**', (route) => route.abort());
  await page.route('**/api/**', (route) =>
    route.fulfill({ json: { results: [], count: 0 } })
  );
  await page.route('**/api/stock/location/tree/**', (route) =>
    route.fulfill({
      json: [{ pk: 42, name: 'Fixture stores / Shelf B2', sublocations: [] }]
    })
  );
  await page.route('**/api/stock/location/42/**', (route) =>
    route.fulfill({
      json: {
        pk: 42,
        name: 'Fixture stores',
        pathstring: 'Fixture stores / Shelf B2'
      }
    })
  );
}
function url(surface: string, scheme = 'light', extra = '') {
  return `/playwright/r8-procurement.html?surface=${surface}&scheme=${scheme}${extra}`;
}
async function capture(page: Page, name: string) {
  await page.screenshot({
    path: `${captures}/${name}.png`,
    fullPage: true,
    animations: 'disabled'
  });
  if (name.startsWith('closeout')) {
    if (page.viewportSize()!.width < 500)
      await page.getByTestId('closeout-materials').scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `${captures}/${name}-viewport.png`,
      fullPage: false,
      animations: 'disabled'
    });
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1
    )
  ).toBe(true);
}
const usage = [
  {
    id: 501,
    work_order: 73,
    allocation: 401,
    part: 71,
    stock_item: 301,
    stock_tracking_id: 901,
    source: 'kit',
    state: 'reconciled',
    candidate_text: '',
    planned_quantity: '1',
    issued_quantity: '1',
    used_quantity: '1',
    disposition: 'consumed',
    version: 2,
    material: {
      part: {
        pk: 71,
        name: 'Fixture seal/service kit',
        ipn: 'FIXTURE-SK',
        units: 'kit'
      },
      stock_item: {
        pk: 301,
        hidden: false,
        batch: 'FIXTURE-BATCH',
        location: 'Fixture stores / Shelf B2'
      },
      stock_tracking_id: 901,
      fitted_record: null,
      issued: { by: 'Fixture stores', at: '2026-10-23T09:00:00Z' },
      receipts: [
        {
          event_id: 801,
          recorded_at: '2026-10-23T08:00:00Z',
          actor: { pk: 11, label: 'Fixture stores', kind: 'internal_person' },
          stocks: [stock],
          purchase_order_id: 81,
          purchase_order_line_id: 91,
          reference: 'FIXTURE-PO-81',
          goods_total: '620',
          charges_total: '45',
          order_total: '665',
          currency: 'USD'
        }
      ]
    }
  }
];
for (const shape of [
  { label: 'desktop-light', scheme: 'light', width: 1440, height: 900 },
  { label: 'desktop-dark', scheme: 'dark', width: 1440, height: 900 },
  { label: 'phone', scheme: 'light', width: 390, height: 844 }
]) {
  test(`Closeout material evidence ${shape.label}`, async ({ page }) => {
    await page.setViewportSize(shape);
    await common(page);
    await page.route('**/api/fixture-closeout-materials/', (route) =>
      route.fulfill({ json: usage })
    );
    await page.goto(url('closeout', shape.scheme));
    const materials = page.getByTestId('closeout-materials');
    await expect(materials).toContainText('FIXTURE-PO-81');
    await expect(materials).toContainText('665');
    await expect(
      materials.getByRole('link', { name: 'Use tracking 901' })
    ).toHaveAttribute('href', /stock\/item\/301\/tracking\//);
    await expect(
      materials.getByRole('button', {
        name: 'Fit evidence: No authorized record supplied'
      })
    ).toHaveText('—');
    await capture(page, `closeout-${shape.label}`);
  });
  test(`Native receiving form ${shape.label}`, async ({ page }) => {
    await page.setViewportSize(shape);
    await common(page);
    await page.route(receivePath, (route) =>
      route.fulfill({
        json:
          route.request().method() === 'OPTIONS'
            ? {
                actions: {
                  POST: {
                    location: {
                      type: 'related field',
                      model: 'stocklocation',
                      api_url: '/api/stock/location/',
                      label: 'Location',
                      required: true
                    },
                    items: { type: 'list' }
                  }
                }
              }
            : baseContext
      })
    );
    await page.goto(url('receive', shape.scheme));
    await page.getByRole('button', { name: 'Receive fixture line' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(
      page.getByRole('textbox', { name: 'text-field-batch_code' })
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Receive', exact: true })
    ).toBeEnabled();
    await capture(page, `receiving-${shape.label}`);
  });
}
test('reload after lost native response retries exact original key and body on completed PO', async ({
  page
}) => {
  await common(page);
  let context = { ...baseContext, receipts: [receipt()] };
  const posts: any[] = [];
  await page.route(receivePath, async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      posts.push(body);
      context = { ...baseContext, receipts: [receipt(body, 'allocated')] };
      return route.fulfill({
        status: 201,
        json: [{ pk: 301, batch: 'FIXTURE-BATCH' }]
      });
    }
    return route.fulfill({
      json:
        route.request().method() === 'OPTIONS'
          ? {
              actions: {
                POST: {
                  location: {
                    type: 'related field',
                    model: 'stocklocation',
                    api_url: '/api/stock/location/',
                    label: 'Location',
                    required: true
                  },
                  items: { type: 'list' }
                }
              }
            }
          : context
    });
  });
  await page.goto(url('receive', 'light', '&complete=true'));
  await page.evaluate(
    (body) =>
      localStorage.setItem(
        'aimms-maintenance-receive:v1:r8-w2b-fixture:11:/api/order/po/81/receive/',
        JSON.stringify(body)
      ),
    original
  );
  await page.reload();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(
    page.getByText('Receipt recorded · reservation pending')
  ).toBeVisible();
  expect(posts).toEqual([]);
  await page.getByRole('button', { name: 'Retry original receipt' }).click();
  await expect(
    page.getByText('Receipt recorded · reservation recorded')
  ).toBeVisible();
  expect(posts).toEqual([original]);
  await capture(page, 'receiving-recovered');
});
test('failed receipt read remains visible and blocks submit', async ({
  page
}) => {
  await common(page);
  await page.route(receivePath, (route) =>
    route.fulfill({
      status: route.request().method() === 'GET' ? 403 : 200,
      json: {
        actions: {
          POST: {
            location: {
              type: 'related field',
              model: 'stocklocation',
              api_url: '/api/stock/location/',
              label: 'Location',
              required: true
            },
            items: { type: 'list' }
          }
        },
        detail: 'Fixture scope denied'
      }
    })
  );
  await page.goto(url('receive'));
  await page.getByRole('button', { name: 'Receive fixture line' }).click();
  await expect(page.getByText('Receipt status unavailable')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Receive', exact: true })
  ).toBeDisabled();
  await capture(page, 'receiving-denied');
});

test('native submission requires batch and preserves exact body after a lost response', async ({
  page
}) => {
  await common(page);
  let context = { ...baseContext };
  const posts: any[] = [];
  await page.route(receivePath, async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      posts.push(body);
      context = {
        ...baseContext,
        receipts: [receipt(body, posts.length === 1 ? 'pending' : 'allocated')]
      };
      if (posts.length === 1) return route.abort('failed');
      return route.fulfill({ status: 201, json: [{ pk: 301 }] });
    }
    return route.fulfill({
      json:
        route.request().method() === 'OPTIONS'
          ? {
              actions: {
                POST: {
                  location: {
                    type: 'related field',
                    model: 'stocklocation',
                    api_url: '/api/stock/location/',
                    label: 'Location',
                    required: true
                  },
                  items: { type: 'list' }
                }
              }
            }
          : context
    });
  });
  await page.goto(url('receive'));
  await page.getByRole('button', { name: 'Receive fixture line' }).click();
  const submit = page.getByRole('button', { name: 'Receive', exact: true });
  await expect(submit).toBeEnabled();
  await submit.click();
  expect(posts).toHaveLength(0);
  await page
    .getByRole('textbox', { name: 'text-field-batch_code' })
    .fill('FIXTURE-BATCH');
  await submit.click();
  await expect(
    page.getByText('Receipt recorded · reservation pending')
  ).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].maintenance_requirement_version).toBe(7);
  expect(posts[0].maintenance_operation_key).toMatch(/^[0-9a-f-]{36}$/);
  await page.reload();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Retry original receipt' }).click();
  await expect(
    page.getByText('Receipt recorded · reservation recorded')
  ).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
  await capture(page, 'receiving-native-replay');
});

test('missing cost and fit stay unknown and explain on keyboard focus', async ({
  page
}) => {
  await common(page);
  const rows = structuredClone(usage);
  rows[0].material.receipts[0].order_total = null as any;
  await page.route('**/api/fixture-closeout-materials/', (route) =>
    route.fulfill({ json: rows })
  );
  await page.goto(url('closeout'));
  const material = page.getByTestId('closeout-materials');
  await expect(material.getByText('PO total:')).toBeVisible();
  await material
    .getByRole('button', { name: 'PO total: No authorized record supplied' })
    .focus();
  await expect(page.getByRole('tooltip')).toContainText(
    'No authorized record supplied'
  );
  await material
    .getByRole('button', {
      name: 'Fit evidence: No authorized record supplied'
    })
    .focus();
  await expect(
    page.getByRole('tooltip', {
      name: 'Fit evidence: No authorized record supplied',
      exact: true
    })
  ).toBeVisible();
  await capture(page, 'closeout-unknown-focused');
});

test('reservation attention requires original actor and never starts a new receipt', async ({
  page
}) => {
  await common(page);
  const prior = { ...receipt(original, 'attention'), actor: 22 };
  const posts: unknown[] = [];
  await page.route(receivePath, (route) => {
    if (route.request().method() === 'POST')
      posts.push(route.request().postDataJSON());
    return route.fulfill({
      json:
        route.request().method() === 'OPTIONS'
          ? { actions: { POST: {} } }
          : { ...baseContext, receipts: [prior] }
    });
  });
  await page.goto(url('receive'));
  await page.getByRole('button', { name: 'Receive fixture line' }).click();
  await expect(
    page.getByText('Receipt recorded · reservation needs attention')
  ).toBeVisible();
  await expect(
    page.getByText('Original receiving actor required')
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Retry original receipt' })
  ).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Receive', exact: true })
  ).toBeDisabled();
  expect(posts).toEqual([]);
  await capture(page, 'receiving-attention');
});

test('late first-submit failure after A to B to A keeps the original native receipt intent', async ({
  page
}) => {
  await common(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const posts: any[] = [];
  await page.route('**/api/order/po/*/receive/', async (route) => {
    if (route.request().method() === 'POST') {
      posts.push(route.request().postDataJSON());
      await gate;
      return route.fulfill({
        status: 400,
        json: { detail: 'Fixture late validation refusal' }
      });
    }
    return route.fulfill({
      json:
        route.request().method() === 'OPTIONS'
          ? {
              actions: {
                POST: {
                  location: {
                    type: 'related field',
                    model: 'stocklocation',
                    api_url: '/api/stock/location/',
                    label: 'Location',
                    required: true
                  },
                  items: { type: 'list' }
                }
              }
            }
          : baseContext
    });
  });
  await page.goto(url('receive', 'light', '&scopeSwitch=1'));
  await page.getByRole('button', { name: 'Receive fixture line' }).click();
  await page
    .getByRole('textbox', { name: 'text-field-batch_code' })
    .fill('FIXTURE-BATCH');
  await page.getByRole('button', { name: 'Receive', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  // The fixture control models parent navigation while the modal's loading overlay is active.
  await page
    .getByRole('button', { name: 'Change receiving scope' })
    .evaluate((button) => (button as HTMLButtonElement).click());
  await expect(
    page.getByRole('button', { name: 'Change receiving scope' })
  ).toHaveAttribute('data-order', '82');
  await page
    .getByRole('button', { name: 'Change receiving scope' })
    .evaluate((button) => (button as HTMLButtonElement).click());
  await expect(
    page.getByRole('button', { name: 'Change receiving scope' })
  ).toHaveAttribute('data-order', '81');
  const lateResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/api/order/po/81/receive/') &&
      response.status() === 400
  );
  release();
  await (await lateResponse).finished();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
  await expect(
    page.getByRole('button', { name: 'Retry original receipt' })
  ).toBeVisible();
  const retained = await page.evaluate(() =>
    localStorage.getItem(
      'aimms-maintenance-receive:v1:r8-w2b-fixture:11:/api/order/po/81/receive/'
    )
  );
  expect(JSON.parse(retained!)).toEqual(posts[0]);
  expect(posts).toHaveLength(1);
});
