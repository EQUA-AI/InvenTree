import { expect, test } from '@playwright/test';

import { nativeBaseline } from './guards';
import { boardCards, mockWork, repairPacket, workOrder } from './workFixtures';

import {
  openPacketCloseout,
  packetCloseoutWork
} from '../s7/packetReceiptFixtures';

nativeBaseline();

for (const card of boardCards) {
  test(`card ${card.id} opens its owning work order ${card.work_order}`, async ({
    page
  }) => {
    const reads = await mockWork(page);
    await page.goto('/playwright/s0-native.html?surface=work');
    const title = page.getByText(card.title, { exact: true });
    const cardContainer = page
      .locator('.mantine-Card-root')
      .filter({ has: title });
    await cardContainer
      .getByRole('button', { name: 'Open work order detail', exact: true })
      .click();
    await expect(page.getByTestId('destination')).toHaveText(
      `/maintenance/work-orders/${card.work_order}/`
    );
    await expect(
      page.getByRole('heading', {
        name: `WO-${card.work_order}: Synthetic job ${card.work_order}`
      })
    ).toBeVisible();
    expect(reads.map((url) => url.pathname)).toEqual([
      `/api/kanban/work-orders/${card.work_order}/overview/`
    ]);
  });
}

test('native standalone work keeps its fields and disables command reads when the capability is off', async ({
  page
}) => {
  await mockWork(page);
  await page.goto(
    '/playwright/s0-native.html?surface=work&entry=/maintenance/work-orders/73/'
  );
  await expect(
    page.getByRole('heading', { name: 'WO-73: Synthetic job 73' })
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Record details', exact: true })
    .click();
  for (const label of [
    'Board stage',
    'Type',
    'Assignee',
    'Estimated effort',
    'Scheduled start',
    'Scheduled end',
    'Due date',
    'Actual start',
    'Actual completion',
    'Reference',
    'Created',
    'Last updated'
  ]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
  for (const [panel, title] of [
    ['Work', 'Board cards'],
    ['Parts', 'Parts for this job'],
    ['Plan & Safety', 'Dependencies'],
    ['History', 'Activity']
  ]) {
    await page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .getByRole('tab', { name: panel, exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: title, exact: true })
    ).toBeVisible();
  }
  await page.getByRole('button', { name: /^Attachments/ }).click();
  await expect(
    page.getByRole('heading', { name: 'Work attachments', exact: true })
  ).toBeVisible();
  await page
    .getByRole('tablist', { name: 'Work workflow', exact: true })
    .getByRole('tab', { name: 'Closeout', exact: true })
    .click();
  await expect(page.getByText('Narrative', { exact: true })).toHaveCount(0);
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
});

test('standalone command capability mounts the native closeout reads', async ({
  page
}) => {
  await mockWork(page, { ...workOrder(), canonical_commands_enabled: true });
  const reads: string[] = [];
  await page.route('**/api/tasks/work-orders/73/**', (route) => {
    const url = new URL(route.request().url());
    reads.push(url.pathname);
    return route.fulfill({
      json: url.pathname.endsWith('/readiness/')
        ? {
            ready: false,
            blockers: [
              {
                code: 'SYNTHETIC_BLOCKER',
                message: 'Synthetic missing verification',
                blocking: true
              }
            ],
            warnings: []
          }
        : []
    });
  });
  await page.goto(
    '/playwright/s0-native.html?surface=work&entry=/maintenance/work-orders/73/'
  );
  await page
    .getByRole('tablist', { name: 'Work workflow', exact: true })
    .getByRole('tab', { name: 'Closeout', exact: true })
    .click();
  const capture = page.getByTestId('closeout-capture-disclosure');
  await expect(capture).not.toHaveAttribute('open');
  await capture
    .getByText('Edit or add to the capture', { exact: true })
    .click();
  await expect(page.getByText('Narrative', { exact: true })).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  expect(reads.sort()).toEqual([
    '/api/tasks/work-orders/73/closeout/captures/',
    '/api/tasks/work-orders/73/closeout/part-usage/',
    '/api/tasks/work-orders/73/closeout/readings/',
    '/api/tasks/work-orders/73/readiness/'
  ]);
});

test('a denied overview shows scope recovery without rendering work data', async ({
  page
}) => {
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Synthetic access denial' } })
  );
  await page.goto(
    '/playwright/s0-native.html?surface=work&role=viewer&entry=/maintenance/work-orders/73/'
  );
  await expect(
    page.getByText(
      'This work order does not exist or is outside your access scope.'
    )
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Return to the work-order board' })
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Board cards', exact: true })
  ).toHaveCount(0);
});

test('a work-owned packet opens the shared prepared Closeout without a modal', async ({
  page
}) => {
  const { commands } = await packetCloseoutWork(page);
  await openPacketCloseout(page);
  await expect(page.getByTestId('closeout-account')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeEnabled();
  await expect(
    page.getByRole('button', { name: 'Close Packet', exact: true })
  ).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(commands).toHaveLength(0);
});

test('a packet without a work order keeps its distinct Close Packet action', async ({
  page
}) => {
  await page.route('**/api/repair/packets/901/', (route) =>
    route.fulfill({ json: repairPacket(false) })
  );
  await page.goto(
    '/playwright/s0-native.html?surface=packet&entry=/repair/packets/901/'
  );
  await expect(
    page.getByRole('button', { name: 'Close Packet', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Close Repair', exact: true })
  ).toHaveCount(0);
});

test('packet closeout sends its own versioned contract and retains text after rejection', async ({
  page
}) => {
  const { commands } = await packetCloseoutWork(page, { captures: [] });
  await page.route('**/api/repair/packets/901/close/', (route) => {
    if (route.request().method() === 'GET') return route.fallback();
    commands.push({
      path: '/api/repair/packets/901/close/',
      body: route.request().postDataJSON(),
      method: 'POST'
    });
    return route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
  });
  await openPacketCloseout(page);
  await page
    .getByRole('button', {
      name: 'What work was actually done, and why?',
      exact: true
    })
    .click();
  await page
    .getByRole('textbox', { name: 'Action taken', exact: true })
    .fill('Synthetic action');
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill('Synthetic result');
  await page
    .getByRole('textbox', { name: 'Verification summary', exact: true })
    .fill('Synthetic verification');
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
    'data-update-status',
    'stale'
  );
  await expect(
    page.getByRole('textbox', { name: 'Action taken', exact: true })
  ).toHaveValue('Synthetic action');
  expect(commands).toEqual([
    expect.objectContaining({
      path: '/api/repair/packets/901/close/',
      body: expect.objectContaining({
        expected_version: 7,
        work_order_id: 73,
        idempotency_key: expect.any(String),
        closeout: expect.objectContaining({
          action: 'Synthetic action',
          result: 'Synthetic result',
          verification_summary: 'Synthetic verification'
        })
      })
    })
  ]);
});
