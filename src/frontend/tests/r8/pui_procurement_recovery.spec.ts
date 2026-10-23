import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';

const endpoint = '**/api/order/po/*/receive/';
const captureRoot = resolve(
  import.meta.dirname,
  '../../../../data/devscratch/r8-w2c/captures'
);

const modes = [
  { name: 'desktop-light', width: 1440, height: 900, scheme: 'light' },
  { name: 'desktop-dark', width: 1440, height: 900, scheme: 'dark' },
  { name: 'phone', width: 390, height: 844, scheme: 'light' }
];
for (const mode of modes) {
  test(`receipt array reload recovery ${mode.name}`, async ({ page }) => {
    await page.setViewportSize(mode);
    const requests: unknown[] = [];
    await page.route(endpoint, async (route) => {
      requests.push(route.request().postDataJSON());
      if (requests.length === 1) return route.abort('failed'); // result lost after a server-side commit
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify([{ pk: 91, batch: 'R8-FIXTURE-A' }])
      });
    });
    await page.goto(`/playwright/r8-recovery.html?scheme=${mode.scheme}`);
    await page.getByRole('button', { name: 'Receive', exact: true }).click();
    await expect(
      page.getByText('Outcome unconfirmed. Recover the original operation.')
    ).toBeVisible();
    await page.screenshot({
      path: resolve(captureRoot, `recovery-pending-${mode.name}.png`),
      animations: 'disabled'
    });
    await page.reload();
    await page.getByRole('button', { name: 'Edit fixture batch' }).click();
    await expect(
      page.getByRole('button', { name: 'Receive', exact: true })
    ).toBeDisabled();
    await page.getByRole('button', { name: 'Recover receipt' }).click();
    await expect(page.getByText('Received stock 91')).toBeVisible();
    expect(requests).toHaveLength(2);
    expect(requests[1]).toEqual(requests[0]);
    await expect(
      page.getByRole('button', { name: 'Receive', exact: true })
    ).toBeEnabled();
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter((key) =>
          key.startsWith('aimms-procurement:')
        )
      )
    ).toHaveLength(0);
    await page.screenshot({
      path: resolve(captureRoot, `recovery-saved-${mode.name}.png`),
      animations: 'disabled'
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true);
  });
}

test('scope drift retains original receipt and refuses to adopt it into another Work', async ({
  page
}) => {
  const requests: unknown[] = [];
  await page.route(endpoint, async (route) => {
    requests.push(route.request().postDataJSON());
    await route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: '{"detail":"Scope changed"}'
    });
  });
  await page.goto('/playwright/r8-recovery.html');
  await page.getByRole('button', { name: 'Receive', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Recover receipt' })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Change scope' }).click();
  await expect(
    page.getByRole('button', { name: 'Recover receipt' })
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Receive', exact: true })
  ).toBeEnabled();
  await page.getByRole('button', { name: 'Change scope' }).click();
  await page.getByRole('button', { name: 'Recover receipt' }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual(requests[0]);
  await expect(
    page.getByRole('button', { name: 'Receive', exact: true })
  ).toBeDisabled();
});

test('malformed success retains intent, malformed storage blocks native writes', async ({
  page
}) => {
  let count = 0;
  await page.route(endpoint, async (route) => {
    count++;
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: '{"version":1}'
    });
  });
  await page.goto('/playwright/r8-recovery.html');
  await page.getByRole('button', { name: 'Receive', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Recover receipt' })
  ).toBeVisible();
  await expect(page.getByText('Record saved.')).toHaveCount(0);
  await page.evaluate(() => {
    const key = Object.keys(localStorage).find((key) =>
      key.startsWith('aimms-procurement:')
    )!;
    localStorage.setItem(key, '{');
  });
  await page.reload();
  await expect(
    page.getByText('Retained operation unavailable. Recording is blocked.')
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Receive', exact: true })
  ).toBeDisabled();
  expect(count).toBe(1);
});

test('a completed old-scope response cannot clear the original intent or update the new Work', async ({
  page
}) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let called = false;
  await page.route(endpoint, async (route) => {
    called = true;
    await gate;
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: '[{"pk":91}]'
    });
  });
  await page.goto('/playwright/r8-recovery.html');
  await page.getByRole('button', { name: 'Receive', exact: true }).click();
  await expect.poll(() => called).toBe(true);
  await page.getByRole('button', { name: 'Change scope' }).click();
  release();
  await expect(page.getByText('Received stock 91')).toHaveCount(0);
  await page.getByRole('button', { name: 'Change scope' }).click();
  await expect(
    page.getByRole('button', { name: 'Recover receipt' })
  ).toBeVisible();
});

for (const family of ['document', 'use', 'purchase']) {
  for (const mode of modes) {
    test(`${family} recovery through reload ${mode.name}`, async ({ page }) => {
      await page.setViewportSize(mode);
      const calls: { body: unknown; headers: Record<string, string> }[] = [];
      const path =
        family === 'document'
          ? '**/api/order/po/3826/recorded-evidence/'
          : family === 'use'
            ? '**/api/tasks/work-orders/3826/job-kit/allocations/13/consume/'
            : '**/api/approvals/fixture-approval/approve/';
      await page.route(path, async (route) => {
        calls.push({
          body: route.request().postDataJSON(),
          headers: route.request().headers()
        });
        if (calls.length === 1) return route.abort('failed');
        const body = calls[0].body as Record<string, unknown>;
        const result =
          family === 'document'
            ? {
                pk: 29,
                order_id: 3826,
                operation_key: body.operation_key,
                state: 'recorded_not_sent',
                supplier_email_sent: false,
                attachment_state: 'verified'
              }
            : family === 'use'
              ? { id: 13, status: 'consumed', stock_tracking_id: 27 }
              : {
                  id: 'fixture-approval',
                  action_type: 'purchase_order',
                  status: 'succeeded'
                };
        await route.fulfill({
          status: family === 'document' ? 201 : 200,
          json: result
        });
      });
      await page.goto(
        `/playwright/r8-recovery.html?family=${family}&scheme=${mode.scheme}`
      );
      await page.getByRole('button', { name: 'Record operation' }).click();
      await expect(
        page.getByRole('button', { name: 'Recover operation' })
      ).toBeVisible();
      await page.reload();
      await page.getByRole('button', { name: 'Recover operation' }).click();
      await expect(page.locator('output')).toHaveText(
        family === 'document'
          ? 'recorded_not_sent'
          : family === 'use'
            ? 'consumed'
            : 'succeeded'
      );
      expect(calls).toHaveLength(2);
      expect(calls[1].body).toEqual(calls[0].body);
      expect(calls[0].headers['x-aimms-reconcile-only']).toBeUndefined();
      expect(calls[1].headers['x-aimms-reconcile-only']).toBe(
        family === 'use' ? '1' : undefined
      );
      await page.screenshot({
        path: resolve(captureRoot, `${family}-saved-${mode.name}.png`),
        animations: 'disabled'
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth
        )
      ).toBe(true);
    });
  }
}
