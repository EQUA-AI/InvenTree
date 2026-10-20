import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  accepted,
  closeoutWork,
  containment,
  openCloseout,
  story,
  values
} from '../s6/closeoutFixtures';
import {
  openPacketCloseout,
  packetCloseoutWork
} from './packetReceiptFixtures';

nativeBaseline();

for (const scheme of ['light', 'dark'] as const) {
  test(`only the prepared account hosts the final action, ${scheme}`, async ({
    page
  }, info) => {
    const { commands, reads } = await closeoutWork(page);
    await openCloseout(page, scheme);
    const account = page.getByTestId('closeout-account');
    const action = account.getByRole('button', {
      name: 'Update maintenance record',
      exact: true
    });
    await expect(action).toBeEnabled();
    await expect(
      page.getByTestId('closeout-capture-disclosure')
    ).not.toHaveAttribute('open', '');
    await expect(account.getByTestId('work-record-update')).toHaveCount(1);
    await expect(account).toContainText(values.action);
    const initialReadings = reads.filter((path) =>
      path.endsWith('/readings/')
    ).length;
    await page
      .getByRole('tab', { name: 'Verify & Return', exact: true })
      .click();
    await expect(page.getByTestId('verification-return')).toBeVisible();
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeHidden();
    await expect(
      page.getByRole('heading', { name: 'Closeout readings', exact: true })
    ).toBeVisible();
    await page
      .getByTestId('verification-return')
      .getByRole('link', { name: 'Open Closeout', exact: true })
      .click();
    await expect(action).toBeEnabled();
    expect(reads.filter((path) => path.endsWith('/readings/'))).toHaveLength(
      initialReadings + 1
    );
    await page.getByRole('tab', { name: 'Work', exact: true }).click();
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeHidden();
    await page
      .getByTestId('closeout-link')
      .filter({ visible: true })
      .getByRole('link', { name: 'Open Closeout', exact: true })
      .click();
    await expect(action).toBeEnabled();
    await account.getByText('Read report', { exact: true }).click();
    await expect(account.getByTestId('work-record-update')).toHaveCount(1);
    expect(commands).toHaveLength(0);
    await account.getByText('Read report', { exact: true }).click();
    await action.scrollIntoViewIfNeeded();
    await containment(page);
    await page.screenshot({
      path: info.outputPath(`account-action-${scheme}.png`)
    });
  });
}

test('an unfinished capture keeps the original narrative and locks the account action', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page, {
    captures: [{ ...story, status: 'open' }],
    ready: false
  });
  await openCloseout(page, 'dark');
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeDisabled();
  const recovery = page.getByTestId('closeout-capture-disclosure');
  await expect(recovery).toHaveAttribute('open', '');
  await expect(
    recovery.getByRole('textbox', {
      name: 'Saved capture narrative',
      exact: true
    })
  ).toHaveValue(story.narrative);
  await expect(page.getByTestId('dictate-closeout')).toHaveCount(0);
  await expect(page.getByTestId('closeout-capture-lock')).toBeVisible();
  expect(commands).toHaveLength(0);
  await recovery.scrollIntoViewIfNeeded();
  await containment(page);
  await page.screenshot({ path: info.outputPath('capture-locked-dark.png') });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`packet finalization stays with its own Closeout contract, ${scheme}`, async ({
    page
  }, info) => {
    const { commands, reads } = await packetCloseoutWork(page);
    await openPacketCloseout(page, scheme, async () => {
      await expect(
        page.getByRole('link', { name: 'Open Work Closeout', exact: true })
      ).toBeVisible();
      await containment(page);
      await page.screenshot({
        path: info.outputPath(`packet-navigation-${scheme}.png`),
        fullPage: true
      });
    });
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeEnabled();
    expect(reads).toContain('/api/repair/packets/901/close/');
    expect(reads.some((path) => path.endsWith('/readiness/'))).toBe(false);
    expect(commands).toHaveLength(0);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await containment(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`packet-closeout-${scheme}.png`),
      fullPage: true
    });
  });
}

test('panel navigation retains the pending operation and its actual result in the account', async ({
  page
}, info) => {
  const { commands, setOverview } = await closeoutWork(page);
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const recordedAt = '2026-10-19T09:17:00Z';
  await page.route('**/api/tasks/work-orders/73/complete/', async (route) => {
    const body = route.request().postDataJSON();
    commands.push({ path: '/complete/', method: 'POST', body });
    await gate;
    setOverview({
      lifecycle_status: 'completed',
      lifecycle_version: 8,
      actual_completed_at: recordedAt,
      structured_closeout: {
        ...accepted!,
        ...values,
        amended: false,
        amendment_count: 0,
        original: { ...values },
        follow_up_required: false,
        follow_up: '',
        completed_by: 11,
        completed_by_name: 'S0 synthetic fixture',
        completed_at: recordedAt
      }
    });
    await route.fulfill({
      json: {
        work_order_id: 73,
        command: 'complete_work_order',
        event_id: 501,
        idempotency_key: body.idempotency_key,
        lifecycle_status: 'completed',
        lifecycle_version: 8,
        metadata: { closeout_id: 301 }
      }
    });
  });
  await page.route('**/api/tasks/work-orders/73/events/', (route) =>
    route.fulfill({
      json: {
        results: [
          {
            id: 501,
            event_type: 'COMPLETED',
            idempotency_key: commands[0]?.body.idempotency_key,
            created_at: recordedAt
          }
        ]
      }
    })
  );
  await openCloseout(page, 'dark');
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Updating…', exact: true })
  ).toBeDisabled();
  await page.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  await expect(page.getByTestId('work-record-update')).toBeHidden();
  await page
    .getByTestId('verification-return')
    .getByRole('link', { name: 'Open Closeout', exact: true })
    .click();
  const status = page
    .getByTestId('closeout-account')
    .getByTestId('work-record-update');
  await expect(
    status.getByRole('button', { name: 'Updating…', exact: true })
  ).toBeDisabled();
  await expect(status.getByText('Updating…', { exact: true })).toHaveCSS(
    'opacity',
    '1'
  );
  expect(commands).toHaveLength(1);
  await status.screenshot({
    path: info.outputPath('account-pending-dark.png')
  });
  release!();
  await expect(status).toContainText('Record updated');
  await expect(status).toContainText('19 Oct 2026,');
  await expect(page.getByTestId('closeout-account')).toContainText(
    'Accepted account'
  );
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(commands).toHaveLength(1);
  await containment(page);
  await status.screenshot({
    path: info.outputPath('account-confirmed-dark.png')
  });
});
