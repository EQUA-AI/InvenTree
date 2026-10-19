import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { closeoutWork, containment, openCloseout } from './closeoutFixtures';

nativeBaseline();
const receipt = (body: any) => ({
  work_order_id: 73,
  command: 'complete_work_order',
  event_id: 501,
  idempotency_key: body.idempotency_key,
  lifecycle_status: 'completed',
  lifecycle_version: body.expected_version + 1,
  metadata: { closeout_id: 301 }
});

test('one deliberate update is pending then confirmed with the original recorded time', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/tasks/work-orders/73/complete/', async (route) => {
    const body = route.request().postDataJSON();
    commands.push({ path: '/complete/', body, method: 'POST' });
    await gate;
    await route.fulfill({ json: receipt(body) });
  });
  await page.route('**/api/tasks/work-orders/73/events/', (route) =>
    route.fulfill({
      json: {
        results: [
          {
            id: 501,
            event_type: 'COMPLETED',
            idempotency_key: commands[0]?.body.idempotency_key,
            created_at: '2026-10-19T09:17:00Z'
          }
        ]
      }
    })
  );
  await openCloseout(page);
  const action = page.getByRole('button', {
    name: 'Update maintenance record',
    exact: true
  });
  await action.evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await expect(
    page.getByRole('button', { name: 'Updating…', exact: true })
  ).toBeDisabled();
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'pending'
  );
  expect(commands).toHaveLength(1);
  expect(commands[0].body).toMatchObject({
    expected_version: 7,
    capture_id: 12
  });
  release!();
  await expect(page.getByTestId('work-record-update')).toContainText(
    'Record updated'
  );
  await expect(page.getByTestId('work-record-update')).toContainText(
    '19 Oct 2026,'
  );
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'confirmed'
  );
  await expect(action).toHaveCount(0);
  await page.getByText('Read report', { exact: true }).click();
  expect(commands).toHaveLength(1);
  await containment(page);
  await page.screenshot({
    path: info.outputPath('record-updated.png'),
    fullPage: true
  });
});

test('uncertain timeout and refresh reconcile the exact original operation', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/api/tasks/work-orders/73/complete/', async (route) => {
    const body = route.request().postDataJSON();
    commands.push({ path: '/complete/', body, method: 'POST' });
    if (commands.length === 1) return route.abort('timedout');
    return route.fulfill({ json: receipt(body) });
  });
  await openCloseout(page, 'dark');
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Check original update', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toHaveCount(0);
  await containment(page);
  await page.screenshot({
    path: info.outputPath('uncertain-update-dark.png'),
    fullPage: true
  });
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Check original update', exact: true })
  ).toBeVisible();
  expect(commands).toHaveLength(1);
  await page
    .getByRole('button', { name: 'Check original update', exact: true })
    .click();
  await expect(page.getByTestId('work-record-update')).toContainText(
    'Record updated'
  );
  expect(commands).toHaveLength(2);
  expect(commands[1].body).toEqual(commands[0].body);
});

test('a refusal after an uncertain response never unlocks a new operation', async ({
  page
}) => {
  const { commands } = await closeoutWork(page);
  let refuseReplay: (() => void) | undefined;
  const replayGate = new Promise<void>((resolve) => {
    refuseReplay = resolve;
  });
  await page.route('**/api/tasks/work-orders/73/complete/', async (route) => {
    commands.push({
      path: '/complete/',
      body: route.request().postDataJSON(),
      method: 'POST'
    });
    if (commands.length === 1) return route.abort('timedout');
    await replayGate;
    return route.fulfill({
      status: 403,
      json: { code: 'PERMISSION_DENIED' }
    });
  });
  await openCloseout(page);
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'uncertain'
  );
  const refusedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname ===
        '/api/tasks/work-orders/73/complete/' &&
      response.status() === 403
  );
  await page
    .getByRole('button', { name: 'Check original update', exact: true })
    .click();
  // The first timeout already shows the same uncertain message. Wait for the
  // actual replay and its refusal, rather than matching that earlier state.
  await expect.poll(() => commands.length).toBe(2);
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'pending'
  );
  await expect(
    page.getByRole('button', { name: 'Checking update status…', exact: true })
  ).toBeDisabled();
  expect(commands[1].body).toEqual(commands[0].body);
  refuseReplay!();
  await refusedResponse;
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'uncertain'
  );
  await expect(page.getByTestId('work-record-update')).toContainText(
    'original update remains unconfirmed'
  );
  await expect(
    page.getByRole('button', {
      name: 'Use reviewed account for a new update',
      exact: true
    })
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toHaveCount(0);
  expect(commands).toHaveLength(2);
});

for (const kind of ['queued', 'wrong-key', 'server-error'] as const) {
  test(`${kind} never appears as a confirmed update`, async ({ page }) => {
    await closeoutWork(page);
    await page.route('**/api/tasks/work-orders/73/complete/', (route) => {
      const body = route.request().postDataJSON();
      return route.fulfill({
        status: kind === 'server-error' ? 502 : 200,
        json:
          kind === 'queued'
            ? { status: 'queued' }
            : { ...receipt(body), idempotency_key: 'unrelated-key' }
      });
    });
    await openCloseout(page);
    await page
      .getByRole('button', { name: 'Update maintenance record', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Check original update', exact: true })
    ).toBeVisible();
    await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
      'data-update-status',
      'uncertain'
    );
  });
}

test('confirmed refusal preserves account and retry uses the same identity', async ({
  page
}) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/api/tasks/work-orders/73/complete/', (route) => {
    const body = route.request().postDataJSON();
    commands.push({ path: '/complete/', body, method: 'POST' });
    return commands.length === 1
      ? route.fulfill({ status: 409, json: { code: 'READINESS_BLOCKED' } })
      : route.fulfill({ json: receipt(body) });
  });
  await openCloseout(page);
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'failed'
  );
  await page
    .getByRole('button', { name: 'Retry original update', exact: true })
    .click();
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'confirmed'
  );
  expect(commands[1].body).toEqual(commands[0].body);
});

test('stale version retains the original draft and requires latest-record review', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/api/tasks/work-orders/73/complete/', (route) => {
    commands.push({
      path: '/complete/',
      body: route.request().postDataJSON(),
      method: 'POST'
    });
    return route.fulfill({
      status: 409,
      json: { code: 'STALE_VERSION', current_version: 8 }
    });
  });
  await openCloseout(page);
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'stale'
  );
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', {
      name: 'Use reviewed account for a new update',
      exact: true
    })
  ).toHaveCount(0);
  await expect(page.getByTestId('work-record-update')).toContainText(
    'Retained draft version: 7'
  );
  await page.screenshot({
    path: info.outputPath('stale-update.png'),
    fullPage: true
  });
  expect(commands).toHaveLength(1);
});

test('completed elsewhere has no fresh completion action', async ({ page }) => {
  const { commands } = await closeoutWork(page, { completed: true });
  await openCloseout(page);
  await expect(page.getByTestId('work-record-update')).toContainText(
    'Record updated elsewhere'
  );
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toHaveCount(0);
  expect(commands).toEqual([]);
});
