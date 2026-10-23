import { resolve } from 'node:path';
import { type Page, expect, test } from '@playwright/test';
import { materials, source } from './procurementFixtures';

const captures = resolve(
  import.meta.dirname,
  process.env.AIMMS_CAPTURE_DIR ?? '../../../../data/devscratch/r8-w2a/captures'
);
const entry = '/playwright/r8-procurement.html';
async function fixture(
  page: Page,
  state = 'ordered',
  options: {
    po?: boolean;
    role?: string;
    scheme?: string;
    custody?: boolean;
  } = {}
) {
  let failure = 0;
  let custodyStatus = 'reserved';
  const posts: { path: string; body: any }[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      posts.push({ path, body: request.postDataJSON() });
      return route.fulfill({
        status: 400,
        json: { detail: 'Unexpected fixture command' }
      });
    }
    if (request.method() === 'OPTIONS')
      return route.fulfill({ json: { actions: { POST: {} } } });
    if (path === '/api/tasks/work-orders/826/materials/')
      return route.fulfill(
        failure
          ? { status: failure, json: { detail: 'Fixture read failure' } }
          : {
              json: (() => {
                const data = materials(
                  options.custody && custodyStatus === 'consumed'
                    ? 'consumed'
                    : state
                );
                if (options.custody) {
                  data.custody_writable = options.role !== 'reader';
                  data.requirements[0].fulfillment_mode = 'reserve_consume';
                  const row = data.requirements[0].reservations![0];
                  row.status = custodyStatus;
                  if (custodyStatus === 'issued') {
                    data.procurement!.lines[0].reserved_quantity = '0';
                    data.procurement!.lines[0].issued_quantity = '1';
                  }
                  if (custodyStatus !== 'reserved')
                    row.issued = {
                      by: 'Fixture Buyer',
                      at: '2026-10-23T09:00:00Z'
                    };
                  if (custodyStatus === 'consumed')
                    row.closed = {
                      status: 'consumed',
                      by: 'Fixture Buyer',
                      at: '2026-10-23T09:01:00Z',
                      tracking_id: 901
                    };
                }
                return data;
              })()
            }
      );
    if (path === '/api/attachment/')
      return route.fulfill({
        json: [
          {
            pk:
              new URL(request.url()).searchParams.get('model_type') ===
              'purchaseorder'
                ? 92
                : 91,
            filename: 'fixture-original.pdf',
            attachment: '/media/fixture-original.pdf',
            model_type:
              new URL(request.url()).searchParams.get('model_type') ??
              'workorder',
            model_id: Number(
              new URL(request.url()).searchParams.get('model_id') ?? 826
            )
          }
        ]
      });
    if (path === '/api/order/po/51/recorded-evidence/')
      return route.fulfill({
        json: materials(state).procurement!.lines[0].orders[0]
          ?.recorded_document
          ? [materials(state).procurement!.lines[0].orders[0].recorded_document]
          : []
      });
    if (path === '/api/order/po/51/')
      return route.fulfill({
        json: {
          pk: 51,
          reference: 'PO-FIXTURE-51',
          description: 'Explicit representative fixture',
          supplier: 31,
          supplier_detail: {
            pk: 31,
            name: 'Fixture Industrial Supply',
            currency: 'USD'
          },
          status: state === 'draft' ? 10 : 20,
          issue_date: state === 'draft' ? null : '2026-10-23',
          order_currency: 'USD',
          total_price: '665.00',
          completed: false,
          line_items: 1,
          completed_lines: 0,
          creation_date: '2026-10-23',
          tags: []
        }
      });
    if (path === '/api/instance/info/')
      return route.fulfill({
        json: { attachment_count: 0, note_count: 0, parameter_count: 0 }
      });
    if (path === '/api/company/31/')
      return route.fulfill({
        json: {
          pk: 31,
          name: 'Fixture Industrial Supply',
          currency: 'USD',
          is_supplier: true,
          active: true
        }
      });
    if (path === '/api/user/')
      return route.fulfill({
        json: [
          {
            pk: 11,
            username: 'fixture-buyer',
            first_name: 'Fixture',
            last_name: 'Buyer'
          },
          {
            pk: 12,
            username: 'fixture-approver',
            first_name: 'Fixture',
            last_name: 'Named Approver'
          }
        ]
      });
    if (path === '/api/company/part/')
      return route.fulfill({
        json: source.offers.map((offer) => ({
          pk: offer.supplier_part_id,
          SKU: offer.supplier_sku,
          supplier: offer.supplier_id,
          supplier_detail: { name: `Fixture supplier ${offer.supplier_id}` }
        }))
      });
    if (path === '/api/order/po-line/') return route.fulfill({ json: [] });
    if (path.includes('/status/')) return route.fulfill({ json: {} });
    if (path === '/api/tasks/work-orders/826/')
      return route.fulfill({
        json: {
          id: 826,
          reference: 'WO-FIXTURE-3826',
          machine: 18,
          machine_name: 'Fixture Pump 08'
        }
      });
    return route.fulfill({ json: { count: 0, results: [] } });
  });
  const params = new URLSearchParams({
    scheme: options.scheme ?? 'light',
    primary: 'indigo',
    ...(options.role ? { role: options.role } : {}),
    ...(options.po
      ? { entry: '/purchasing/purchase-order/51/detail/?workOrder=826' }
      : {})
  });
  await page.goto(`${entry}?${params}`);
  return {
    posts,
    errors,
    custodyStatus: (value: string) => {
      custodyStatus = value;
    },
    fail: (status: number) => {
      failure = status;
    }
  };
}

for (const scheme of ['light', 'dark']) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 }
  ]) {
    for (const state of [
      'shortage',
      'selected',
      'reserved',
      'pending',
      'consumed'
    ]) {
      test(`${state} ${scheme} ${viewport.width}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        const f = await fixture(page, state, { scheme });
        const panel = page.getByTestId('work-procurement');
        await expect(panel).toBeVisible();
        await expect(
          panel.getByRole('link', {
            name: 'Fixture seal/service kit',
            exact: true
          })
        ).toBeVisible();
        if (state === 'selected')
          await expect(
            panel.getByRole('button', {
              name: 'Request named approval',
              exact: true
            })
          ).toBeVisible();
        if (state === 'reserved')
          await expect(
            panel.getByText('Reserved 1 kit', { exact: true })
          ).toBeVisible();
        if (state === 'pending')
          await expect(
            panel.getByText('Reservation needs attention', { exact: true })
          ).toBeVisible();
        if (state === 'consumed') {
          await expect(
            panel.getByText('Used 1 kit', { exact: true })
          ).toBeVisible();
          await expect(
            panel.getByText('Short 1 kit', { exact: true })
          ).toHaveCount(0);
        }
        await expect
          .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
          .toBeLessThanOrEqual(viewport.width);
        if (
          viewport.width >= 1200 &&
          !process.env.AIMMS_CAPTURE_DIR?.includes('/before')
        ) {
          const actionRow = panel.locator('[aria-label="Procurement actions"]');
          await expect(actionRow).toBeVisible();
          const tops = await actionRow
            .locator('button, a')
            .evaluateAll((nodes) =>
              nodes.map((node) => node.getBoundingClientRect().top)
            );
          expect(tops.length).toBeGreaterThan(0);
          expect(Math.max(...tops) - Math.min(...tops)).toBeLessThanOrEqual(1);
        }
        expect(f.posts).toHaveLength(0);
        expect(f.errors).toEqual([]);
        await page.screenshot({
          animations: 'disabled',
          path: resolve(
            captures,
            `work-${state}-${scheme}-${viewport.width}.png`
          ),
          fullPage: false
        });
      });
    }
    test(`native PO ${scheme} ${viewport.width}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const f = await fixture(page, 'acknowledged', { scheme, po: true });
      await expect(page.getByTestId('po-procurement')).toBeVisible();
      await expect(
        page.getByText('Recorded · not sent', { exact: true })
      ).toBeVisible();
      await expect(
        page.getByText('Fixture Named Approver', { exact: true })
      ).toBeVisible();
      await expect(
        page.getByText('FIXTURE-ACK-1 · 1 kit', { exact: true })
      ).toBeVisible();
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
        .toBeLessThanOrEqual(viewport.width);
      expect(f.errors).toEqual([]);
      expect(f.posts).toHaveLength(0);
      await page.screenshot({
        animations: 'disabled',
        path: resolve(captures, `po-${scheme}-${viewport.width}.png`),
        fullPage: false
      });
    });
  }
}

test('refresh retains exact facts; scope denial removes them', async ({
  page
}) => {
  const f = await fixture(page, 'reserved');
  await expect(page.getByText('Reserved 1 kit', { exact: true })).toBeVisible();
  f.fail(503);
  await page.getByRole('button', { name: 'Refresh fixture' }).click();
  await expect(
    page.getByText('Refresh failed · showing last read', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Reserved 1 kit', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Revise source' })
  ).toBeDisabled();
  f.fail(403);
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByTestId('work-procurement')).toHaveCount(0);
  expect(f.posts).toHaveLength(0);
});

test('legacy and feature fallback remain available', async ({ page }) => {
  const f = await fixture(page);
  f.fail(404);
  await page.getByRole('button', { name: 'Refresh fixture' }).click();
  await expect(
    page.getByText('Legacy required parts fixture', { exact: true })
  ).toBeVisible();
  expect(f.posts).toHaveLength(0);
});

test('reader sees facts without mutation controls and missing fact explains on focus', async ({
  page
}) => {
  await fixture(page, 'shortage', { role: 'reader' });
  await expect(page.getByRole('button', { name: 'Record source' })).toHaveCount(
    0
  );
  const missing = page.getByRole('button', {
    name: 'No source selection recorded'
  });
  await missing.focus();
  await expect(page.getByRole('tooltip')).toContainText(
    'No source selection recorded'
  );
});

test('source and acknowledgment currency warnings retain historical evidence', async ({
  page
}) => {
  await fixture(page, 'stale');
  await expect(
    page.getByText('Source needs review', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Request named approval' })
  ).toHaveCount(0);
  await page.getByText('Source evidence', { exact: true }).click();
  await expect(
    page.getByRole('link', { name: 'Fixture quote B · p. 1' })
  ).toHaveAttribute('href', /fixture-original.pdf#page=1$/);
});

test('source modal opens native evidence and offer controls without a write', async ({
  page
}) => {
  const f = await fixture(page, 'shortage');
  await page
    .getByRole('button', { name: 'Record source', exact: true })
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Source selection' })
  ).toBeVisible();
  await expect(
    page.getByLabel('Technical source PDF', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Record source selection', exact: true })
  ).toBeDisabled();
  expect(f.posts).toHaveLength(0);
});

test('acknowledgment exact retained retry survives reload and later refusal', async ({
  page
}) => {
  await fixture(page, 'ordered');
  const calls: any[] = [];
  await page.route(
    '**/api/tasks/work-orders/826/procurement/acknowledgment/',
    async (route) => {
      calls.push(route.request().postDataJSON());
      await route.abort('failed');
    }
  );
  // A persisted browser intent stands for a timed-out prior submission. It is
  // explicit fixture data; no reconstructed body or new key may replace it.
  const body = {
    operation_key: 'fixture-ack-stable',
    expected_version: 3,
    kit_line_id: 71,
    source_at: '2026-10-23T09:00:00+03:00',
    provenance: source.provenance,
    purchase_order_line_id: 81,
    supplier_reference: 'FIXTURE-ACK',
    accepted_quantity: '1',
    terms: 'Fixture NET30',
    promised_at: '2026-10-25T09:00:00+03:00',
    source: source.technical_evidence[0],
    deviations: []
  };
  await page.evaluate(
    ({ body }) =>
      localStorage.setItem(
        `aimms:r8:command:${location.origin}:11:ack:826:71`,
        JSON.stringify({
          endpoint: '/api/tasks/work-orders/826/procurement/acknowledgment/',
          kind: 'ack',
          body
        })
      ),
    { body }
  );
  await page.reload();
  await page
    .getByRole('button', { name: 'Record acknowledgment', exact: true })
    .click();
  await page.getByRole('button', { name: 'Retry original operation' }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0]).toEqual(body);
  await page.route(
    '**/api/tasks/work-orders/826/procurement/acknowledgment/',
    (route) =>
      route.fulfill({ status: 403, json: { detail: 'Fixture access revoked' } })
  );
  await page.getByRole('button', { name: 'Retry original operation' }).click();
  await expect(
    page.getByRole('button', { name: 'Retry original operation' })
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      localStorage.getItem(`aimms:r8:command:${location.origin}:11:ack:826:71`)
    )
  ).toContain('fixture-ack-stable');
});

for (const scheme of ['light', 'dark']) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 }
  ]) {
    for (const action of ['source', 'ack', 'approval', 'document']) {
      test(`capture ${action} form ${scheme} ${viewport.width}`, async ({
        page
      }) => {
        await page.setViewportSize(viewport);
        const f = await fixture(
          page,
          action === 'source'
            ? 'shortage'
            : action === 'approval'
              ? 'selected'
              : 'ordered',
          { scheme, po: action === 'document' }
        );
        const button =
          action === 'source'
            ? 'Record source'
            : action === 'ack'
              ? 'Record acknowledgment'
              : action === 'approval'
                ? 'Request named approval'
                : 'Record PDF and email';
        await page.getByRole('button', { name: button, exact: true }).click();
        const dialog = page.getByRole('dialog');
        await expect(dialog).toBeVisible();
        await expect
          .poll(() =>
            dialog.evaluate((element) =>
              Number(getComputedStyle(element).opacity)
            )
          )
          .toBe(1);
        await expect
          .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
          .toBeLessThanOrEqual(viewport.width);
        expect(f.posts).toHaveLength(0);
        expect(f.errors).toEqual([]);
        await page.screenshot({
          animations: 'disabled',
          path: resolve(
            captures,
            `form-${action}-${scheme}-${viewport.width}.png`
          ),
          fullPage: false
        });
      });
    }
  }
}

test('named review follows existing open, reviewed, approve flow then links its exact native line', async ({
  page
}) => {
  const f = await fixture(page, 'selected');
  const id = '12345678-1234-1234-1234-123456789abc';
  let status = 'pending';
  const commands: { path: string; body: any }[] = [];
  await page.route('**/api/approvals/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (method === 'POST') {
      const body = route.request().postDataJSON();
      commands.push({ path, body });
      if (path === '/api/approvals/')
        return route.fulfill({
          status: 201,
          json: {
            id,
            action_type: 'purchase_order',
            assigned_to_user: 11,
            status
          }
        });
      if (path.endsWith('/open/')) status = 'in_review';
      if (path.endsWith('/approve/')) status = 'succeeded';
      return route.fulfill({
        json: { id, status, action_type: 'purchase_order' }
      });
    }
    if (path === '/api/approvals/') return route.fulfill({ json: [] });
    return route.fulfill({
      json: {
        id,
        assigned_to_user: 11,
        summary: 'Fixture named purchase',
        action_type: 'purchase_order',
        status,
        risk_tier: 2,
        current_revision_number: 1,
        review_hash: 'a'.repeat(64),
        payload: { operation: 'place_purchase_order' },
        execution_result:
          status === 'succeeded' ? { order_id: 51, line_ids: [81] } : null,
        review_sections: [
          {
            id: 'goods',
            label: 'Fixture goods',
            text: '1 kit · USD 620.00',
            required: true
          },
          {
            id: 'charges',
            label: 'Fixture charges',
            text: 'Freight USD 45.00 · Total USD 665.00',
            required: true
          }
        ]
      }
    });
  });
  await page.route(
    '**/api/tasks/work-orders/826/job-kit/shortages/99/link-po/',
    async (route) => {
      commands.push({
        path: new URL(route.request().url()).pathname,
        body: route.request().postDataJSON()
      });
      return route.fulfill({ json: { id: 99, purchase_order_line: 81 } });
    }
  );
  await page
    .getByRole('button', { name: 'Request named approval', exact: true })
    .click();
  await page.getByRole('combobox', { name: 'Named approver' }).click();
  await page
    .getByRole('option', { name: 'Fixture Buyer', exact: true })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Request named approval', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Open for review', exact: true })
  ).toBeVisible();
  expect(commands[0].body.assigned_to_user_id).toBe(11);
  expect(commands[0].body.payload.line_items[0].supplier_part_id).toBe(61);
  expect(commands[0].body.payload.extra_lines).toEqual([
    { description: 'Freight', quantity: '1', unit_price: '45.00' }
  ]);
  expect(commands[0].body.payload.description).toContain(
    '[Fixture r8-fixture:lane-a]'
  );
  await page
    .getByRole('button', { name: 'Open for review', exact: true })
    .click();
  await page
    .getByRole('checkbox', {
      name: 'I have reviewed every required section of this request.'
    })
    .check();
  await page
    .getByRole('button', { name: 'Confirm reviewed', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Prepare approval', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Type the required confirmation phrase' })
    .fill('approve 12345678');
  await page
    .getByRole('button', { name: 'Confirm decision', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Link approved order', exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(
    commands.find((command) => command.path.endsWith('/confirm-viewed/'))?.body
  ).toEqual({
    revision: 1,
    review_hash: 'a'.repeat(64),
    sections: ['goods', 'charges']
  });
  expect(commands.at(-1)?.body).toEqual({ purchase_order_line_id: 81 });
  expect(f.errors).toEqual([]);
});

test('recorded document submission never sends and replays the exact retained key after reload', async ({
  page
}) => {
  const f = await fixture(page, 'ordered', { po: true });
  const calls: any[] = [];
  let recover = false;
  await page.route('**/api/order/po/51/recorded-evidence/', async (route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: [] });
    const body = route.request().postDataJSON();
    calls.push(body);
    if (!recover) return route.abort('failed');
    return route.fulfill({
      json: {
        pk: 90,
        order_id: 51,
        operation_key: body.operation_key,
        state: 'recorded_not_sent',
        supplier_email_sent: false,
        attachment_state: 'verified'
      }
    });
  });
  await page
    .getByRole('button', { name: 'Record PDF and email', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Recipient', exact: true })
    .fill('fixture-recipient@example.com');
  await page
    .getByRole('textbox', { name: 'Buyer email', exact: true })
    .fill('fixture-buyer@example.com');
  await page
    .getByRole('textbox', { name: 'Buyer name', exact: true })
    .fill('Fixture Buyer');
  await page
    .getByRole('textbox', { name: 'Delivery address', exact: true })
    .fill('Explicit fixture receiving address');
  await page.getByRole('combobox', { name: 'Provenance' }).click();
  await page.getByRole('option', { name: 'Representative fixture' }).click();
  await page
    .getByRole('button', {
      name: 'Record PDF and email · no send',
      exact: true
    })
    .click();
  await expect(
    page.getByRole('button', { name: 'Retry original operation' })
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole('button', { name: 'Record PDF and email', exact: true })
    .click();
  recover = true;
  await page.getByRole('button', { name: 'Retry original operation' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(calls).toHaveLength(2);
  expect(calls[1]).toEqual(calls[0]);
  expect(calls[0].provenance_kind).toBe('fixture');
  expect(f.posts).toHaveLength(0);
  expect(f.errors).toEqual([]);
});

test('source form hashes original bytes and retains the complete evidence command before retry', async ({
  page
}) => {
  const f = await fixture(page, 'shortage');
  const pdf = Buffer.from(
    '%PDF-1.4\n% Explicit fixture bytes for UI hash transport verification\n%%EOF'
  );
  await page.route('**/media/fixture-original.pdf', (route) =>
    route.fulfill({ contentType: 'application/pdf', body: pdf })
  );
  const calls: any[] = [];
  let recover = false;
  await page.route(
    '**/api/tasks/work-orders/826/procurement/source-selection/',
    async (route) => {
      const body = route.request().postDataJSON();
      calls.push(body);
      if (!recover) return route.abort('failed');
      return route.fulfill({
        json: {
          event_id: 501,
          operation_key: body.operation_key,
          event_type: 'PROC_SOURCE_SELECTED',
          source_at: body.source_at,
          recorded_at: '2026-10-23T09:10:00+03:00',
          actor: { pk: 11, label: 'Fixture Buyer', kind: 'internal_person' },
          snapshot: body
        }
      });
    }
  );
  await page
    .getByRole('button', { name: 'Record source', exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await dialog
    .getByRole('textbox', {
      name: 'Source time (ISO 8601 with timezone)',
      exact: true
    })
    .fill('2026-10-23T09:00:00+03:00');
  await dialog
    .getByRole('checkbox', { name: 'Representative fixture' })
    .check();
  await dialog
    .getByRole('textbox', { name: 'Fixture namespace', exact: true })
    .fill('r8-fixture:lane-a');
  await dialog
    .getByRole('textbox', { name: 'Evidence reference', exact: true })
    .fill('Explicit fixture source decision');
  async function choose(label: string, option: string) {
    await dialog.getByRole('combobox', { name: label, exact: true }).click();
    await page.getByRole('option', { name: option, exact: true }).click();
  }
  await choose('Technical source PDF', 'fixture-original.pdf');
  await dialog
    .getByRole('textbox', {
      name: 'Technical source PDF · Document revision',
      exact: true
    })
    .fill('Fixture tech revision 1');
  await choose('Supplier part', 'Fixture supplier 31 · FIXTURE-SEAL-KIT');
  for (const [label, value] of Object.entries({
    'Quote reference': 'UI fixture quote',
    'Quote revision': '1',
    'Order quantity': '1',
    'Order unit': 'kit',
    'Base units per order unit': '1',
    Currency: 'USD',
    'Unit price': '620.00',
    'Discount amount': '0',
    Freight: '45.00',
    Tax: '0',
    'Other charges': '0',
    'Delivered total': '665.00',
    Terms: 'Fixture NET30',
    'Promised arrival (ISO 8601 with timezone)': '2026-10-25T09:00:00+03:00',
    'Valid through (ISO 8601 with timezone)': '2099-01-01T00:00:00Z'
  })) {
    await dialog.getByRole('textbox', { name: label, exact: true }).fill(value);
  }
  await choose('Arrival basis', 'Supplier quote');
  await choose('Technical eligibility', 'Supported by source');
  await choose('Quote PDF', 'fixture-original.pdf');
  await dialog
    .getByRole('textbox', {
      name: 'Quote PDF · Document revision',
      exact: true
    })
    .fill('Fixture quote revision 1');
  await choose('Selected offer', 'UI fixture quote');
  await dialog
    .getByRole('textbox', { name: 'Selection reason', exact: true })
    .fill('Explicit representative source evidence');
  await dialog
    .getByRole('button', { name: 'Record source selection', exact: true })
    .click();
  await expect(
    dialog.getByRole('button', { name: 'Retry original operation' })
  ).toBeVisible();
  expect(calls).toHaveLength(1);
  expect(calls[0].requirement_version).toBe(2);
  expect(calls[0].expected_version).toBe(3);
  expect(calls[0].configuration_revision).toBe('b'.repeat(64));
  expect(calls[0].needed_at).toBeNull();
  expect(calls[0].deadline_source).toBeNull();
  expect(calls[0].provenance.mode).toBe('recorded_fixture');
  expect(calls[0].technical_evidence[0].sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(calls[0].offers[0].source.sha256).toBe(
    calls[0].technical_evidence[0].sha256
  );
  recover = true;
  await dialog
    .getByRole('button', { name: 'Retry original operation' })
    .click();
  await expect(dialog).toHaveCount(0);
  expect(calls[1]).toEqual(calls[0]);
  expect(f.posts).toHaveLength(0);
  expect(f.errors).toEqual([]);
});

test('Work to native PO and back retains exact scoped context', async ({
  page
}) => {
  const f = await fixture(page, 'acknowledged');
  await page.getByRole('link', { name: 'PO-FIXTURE-51', exact: true }).click();
  await expect(page.getByTestId('po-procurement')).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'WO-FIXTURE-3826', exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('destination')).toContainText('workOrder=826');
  await page
    .getByRole('link', { name: 'WO-FIXTURE-3826', exact: true })
    .click();
  await expect(page.getByTestId('work-procurement')).toBeVisible();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/826/parts/'
  );
  expect(f.posts).toHaveLength(0);
});

test('an altered original source is visibly refused without displaying different PDF bytes', async ({
  page
}) => {
  const f = await fixture(page, 'selected');
  await page.route('**/media/fixture-original.pdf', (route) =>
    route.fulfill({
      contentType: 'application/pdf',
      body: '%PDF-1.4\n% changed fixture bytes\n%%EOF'
    })
  );
  await page.getByText('Source evidence', { exact: true }).click();
  await page
    .getByRole('link', { name: 'Fixture quote A · p. 1', exact: true })
    .click();
  await expect(
    page.getByText('Source unavailable or changed', { exact: true })
  ).toBeVisible();
  expect(f.posts).toHaveLength(0);
  expect(f.errors).toEqual([]);
});

for (const scheme of ['light', 'dark']) {
  for (const state of ['reserved', 'pending', 'consumed', 'po']) {
    test(`phone continuation ${state} ${scheme}`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      const f = await fixture(page, state === 'po' ? 'acknowledged' : state, {
        scheme,
        po: state === 'po'
      });
      const panel = page.getByTestId(
        state === 'po' ? 'po-procurement' : 'work-procurement'
      );
      await expect(panel).toBeVisible();
      await panel.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        window.scrollTo(
          0,
          window.scrollY + rect.bottom - window.innerHeight + 12
        );
      });
      await page.screenshot({
        animations: 'disabled',
        path: resolve(captures, `phone-continuation-${state}-${scheme}.png`)
      });
      expect(f.posts).toHaveLength(0);
      expect(f.errors).toEqual([]);
    });
  }
}

test('native draft requests a named approval without issuing the order directly', async ({
  page
}) => {
  const f = await fixture(page, 'draft', { po: true });
  await page.goto(
    `${entry}?${new URLSearchParams({ entry: '/purchasing/purchase-order/51/detail/' })}`
  );
  const requests: any[] = [];
  await page.route('**/api/approvals/', async (route) => {
    if (route.request().method() === 'POST')
      requests.push(route.request().postDataJSON());
    return route.fulfill({
      status: 400,
      json: { detail: 'Explicit fixture refusal' }
    });
  });
  await page
    .getByRole('button', { name: 'Request named approval', exact: true })
    .click();
  await page.getByRole('combobox', { name: 'Named approver' }).click();
  await page.getByRole('option', { name: 'Fixture Named Approver' }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Request named approval', exact: true })
    .click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].assigned_to_user_id).toBe(12);
  expect(requests[0].payload).toEqual({
    operation: 'issue_purchase_order',
    order_id: 51,
    currency: 'USD'
  });
  expect(f.posts).toHaveLength(0);
});

test('PO identity read failure is visible while the independently authorized facts remain', async ({
  page
}) => {
  await fixture(page, 'acknowledged', { po: true });
  await expect(
    page.getByRole('link', { name: 'WO-FIXTURE-3826' })
  ).toBeVisible();
  await page.route('**/api/tasks/work-orders/826/', (route) =>
    route.fulfill({
      status: 503,
      json: { detail: 'Fixture identity read failure' }
    })
  );
  await page.getByRole('button', { name: 'Refresh fixture' }).click();
  await expect(
    page.getByText('Work refresh failed · showing last read', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Recorded · not sent', { exact: true })
  ).toBeVisible();
});

test('native named approval retains the reviewed decision across lost response and reload', async ({
  page
}) => {
  const f = await fixture(page, 'selected');
  const id = '12345678-1234-1234-1234-123456789abc';
  let status = 'pending';
  const commands: { path: string; body: any }[] = [];
  await page.route('**/api/approvals/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (method === 'POST') {
      const body = route.request().postDataJSON();
      commands.push({ path, body });
      if (path === '/api/approvals/')
        return route.fulfill({
          status: 201,
          json: {
            id,
            action_type: 'purchase_order',
            assigned_to_user: 11,
            status
          }
        });
      if (path.endsWith('/open/')) status = 'in_review';
      if (path.endsWith('/approve/')) {
        status = 'succeeded';
        if (
          commands.filter((row) => row.path.endsWith('/approve/')).length === 1
        )
          return route.abort('failed');
      }
      return route.fulfill({
        json: { id, status, action_type: 'purchase_order' }
      });
    }
    if (path === '/api/approvals/') return route.fulfill({ json: [] });
    return route.fulfill({
      json: {
        id,
        assigned_to_user: 11,
        summary: 'Fixture named purchase',
        action_type: 'purchase_order',
        status,
        risk_tier: 2,
        current_revision_number: 1,
        review_hash: 'a'.repeat(64),
        payload: { operation: 'place_purchase_order' },
        execution_result:
          status === 'succeeded' ? { order_id: 51, line_ids: [81] } : null,
        review_sections: [
          {
            id: 'goods',
            label: 'Fixture goods',
            text: '1 kit · USD 620.00',
            required: true
          },
          {
            id: 'charges',
            label: 'Fixture charges',
            text: 'Freight USD 45.00 · Total USD 665.00',
            required: true
          }
        ]
      }
    });
  });
  await page.route(
    '**/api/tasks/work-orders/826/job-kit/shortages/99/link-po/',
    async (route) => {
      commands.push({
        path: new URL(route.request().url()).pathname,
        body: route.request().postDataJSON()
      });
      return route.fulfill({ json: { id: 99, purchase_order_line: 81 } });
    }
  );
  await page
    .getByRole('button', { name: 'Request named approval', exact: true })
    .click();
  await page.getByRole('combobox', { name: 'Named approver' }).click();
  await page
    .getByRole('option', { name: 'Fixture Buyer', exact: true })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Request named approval', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Open for review', exact: true })
  ).toBeVisible();
  expect(commands[0].body.assigned_to_user_id).toBe(11);
  expect(commands[0].body.payload.line_items[0].supplier_part_id).toBe(61);
  expect(commands[0].body.payload.extra_lines).toEqual([
    { description: 'Freight', quantity: '1', unit_price: '45.00' }
  ]);
  expect(commands[0].body.payload.description).toContain(
    '[Fixture r8-fixture:lane-a]'
  );
  await page
    .getByRole('button', { name: 'Open for review', exact: true })
    .click();
  await page
    .getByRole('checkbox', {
      name: 'I have reviewed every required section of this request.'
    })
    .check();
  await page
    .getByRole('button', { name: 'Confirm reviewed', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Prepare approval', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Type the required confirmation phrase' })
    .fill('approve 12345678');
  await page
    .getByRole('button', { name: 'Confirm decision', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Recover original approval' })
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole('button', { name: 'Request named approval', exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .getByRole('dialog')
        .evaluate((element) => Number(getComputedStyle(element).opacity))
    )
    .toBe(1);
  await page.screenshot({
    path: resolve(captures, 'native-approval-recovery.png')
  });
  await page.getByRole('button', { name: 'Recover original approval' }).click();
  const approvals = commands.filter((row) => row.path.endsWith('/approve/'));
  expect(approvals).toHaveLength(2);
  expect(approvals[1].body).toEqual(approvals[0].body);
  await page
    .getByRole('button', { name: 'Link approved order', exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(
    commands.find((command) => command.path.endsWith('/confirm-viewed/'))?.body
  ).toEqual({
    revision: 1,
    review_hash: 'a'.repeat(64),
    sections: ['goods', 'charges']
  });
  expect(commands.at(-1)?.body).toEqual({ purchase_order_line_id: 81 });
  expect(f.errors).toEqual([]);
});

for (const shape of [
  { label: 'desktop-light', scheme: 'light', width: 1440, height: 900 },
  { label: 'desktop-dark', scheme: 'dark', width: 1440, height: 900 },
  { label: 'phone', scheme: 'light', width: 390, height: 844 }
]) {
  test(`mounted material issue and use recover after reload ${shape.label}`, async ({
    page
  }) => {
    await page.setViewportSize(shape);
    const f = await fixture(page, 'reserved', {
      custody: true,
      scheme: shape.scheme
    });
    const calls: {
      action: string;
      body: unknown;
      readback: string | undefined;
    }[] = [];
    await page.route(
      '**/api/tasks/work-orders/826/job-kit/allocations/301/*/',
      async (route) => {
        const action = new URL(route.request().url()).pathname
          .split('/')
          .at(-2)!;
        const readback = route.request().headers()['x-aimms-reconcile-only'];
        calls.push({ action, body: route.request().postDataJSON(), readback });
        f.custodyStatus(action === 'issue' ? 'issued' : 'consumed');
        if (!readback) return route.abort('failed');
        return route.fulfill({
          json: {
            id: 301,
            status: action === 'issue' ? 'issued' : 'consumed',
            issued_by: 11,
            issued_at: '2026-10-23T09:00:00Z',
            stock_tracking_id: action === 'consume' ? 901 : null
          }
        });
      }
    );
    await page.getByText('Material record', { exact: true }).click();
    await page
      .getByRole('button', { name: 'Record issue', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Confirm issue', exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: resolve(captures, `custody-issue-${shape.label}.png`)
    });
    await page
      .getByRole('button', { name: 'Confirm issue', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Recover original issue' })
    ).toBeVisible();
    await page.reload();
    await page.getByText('Material record', { exact: true }).click();
    await page.getByRole('button', { name: 'Recover original issue' }).click();
    await expect(page.getByText('Issued 1 kit', { exact: true })).toBeVisible();
    await expect(page.getByText('Reserved 1 kit', { exact: true })).toHaveCount(
      0
    );
    await page
      .getByRole('button', { name: 'Record material use', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Confirm material use', exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: resolve(captures, `custody-use-${shape.label}.png`)
    });
    await page
      .getByRole('button', { name: 'Confirm material use', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Recover original material use' })
    ).toBeVisible();
    await page.reload();
    await page.getByText('Material record', { exact: true }).click();
    await page
      .getByRole('button', { name: 'Recover original material use' })
      .click();
    await expect(
      page.getByRole('button', { name: 'Recover original material use' })
    ).toHaveCount(0);
    expect(calls.map(({ action, readback }) => [action, readback])).toEqual([
      ['issue', undefined],
      ['issue', '1'],
      ['consume', undefined],
      ['consume', '1']
    ]);
    expect(calls.map((row) => row.body)).toEqual([{}, {}, {}, {}]);
    await expect(page.getByText('Used 1 kit', { exact: true })).toBeVisible();
    await expect(page.getByText('Issued 1 kit', { exact: true })).toHaveCount(
      0
    );
    await page.screenshot({
      path: resolve(captures, `native-use-recovered-${shape.label}.png`)
    });
    expect(f.errors).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true);
  });
}
