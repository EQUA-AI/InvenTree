import { expect, test } from '@playwright/test';

import { nativeBaseline } from './guards';
import { boardCards, mockWork, repairPacket, workOrder } from './workFixtures';

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

test('a work-owned packet opens its native closeout form with no standalone completion command', async ({
  page
}) => {
  await page.route('**/api/repair/packets/901/', (route) =>
    route.fulfill({ json: repairPacket() })
  );
  await page.goto(
    '/playwright/s0-native.html?surface=packet&entry=/repair/packets/901/'
  );
  await page.getByRole('button', { name: 'Close Repair', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(
    dialog.getByText(
      "This completes work order WO-73 and writes one permanent record to the machine's maintenance history."
    )
  ).toBeVisible();
  for (const label of [
    'Verified cause',
    'Action taken',
    'Result',
    'Verification',
    'Downtime (minutes)'
  ]) {
    await expect(dialog.getByLabel(label)).toBeVisible();
  }
  await expect(
    page.getByRole('button', { name: 'Close Packet', exact: true })
  ).toHaveCount(0);
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
  await page.route('**/api/repair/packets/901/', (route) =>
    route.fulfill({ json: repairPacket() })
  );
  const commands: unknown[] = [];
  await page.route('**/api/repair/packets/901/close/', (route) => {
    commands.push(route.request().postDataJSON());
    return route.fulfill({
      status: 409,
      json: { code: 'STALE_VERSION', detail: 'Synthetic stale version' }
    });
  });
  await page.goto(
    '/playwright/s0-native.html?surface=packet&entry=/repair/packets/901/'
  );
  await page.getByRole('button', { name: 'Close Repair', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Action taken').fill('Synthetic action');
  await dialog.getByLabel('Result', { exact: false }).fill('Synthetic result');
  await dialog
    .getByLabel('Verification', { exact: false })
    .fill('Synthetic verification');
  await dialog
    .getByRole('button', { name: 'Close repair', exact: true })
    .click();
  await expect(
    page.getByText('Could not close the repair', { exact: true })
  ).toBeVisible();
  await expect(dialog.getByLabel('Action taken')).toHaveValue(
    'Synthetic action'
  );
  expect(commands).toEqual([
    expect.objectContaining({
      expected_version: 7,
      closeout: expect.objectContaining({
        action: 'Synthetic action',
        result: 'Synthetic result',
        verification_summary: 'Synthetic verification'
      })
    })
  ]);
});
