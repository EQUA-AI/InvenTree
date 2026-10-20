import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockWorkList, openWorkList } from '../s1/workListFixtures';
import { continuityWork } from '../s4/continuityFixtures';
import { openExecutionPanel } from '../s4/workExecutionFixtures';
import { closeoutWork } from '../s6/closeoutFixtures';
import { phoneAssistant } from '../s7/phoneCompanionFixtures';
import { humanNames, unclippedBadges } from './workAccessibilityAssertions';
import {
  filename,
  textZoom,
  workAttachment,
  workScenario
} from './workAccessibilityFixtures';

nativeBaseline();

test('D4 Escape dismisses the attachment dropdown before the observation draft', async ({
  page
}) => {
  const fixture = await closeoutWork(page);
  fixture.setOverview(workScenario());
  await workAttachment(page);
  await openExecutionPanel(page, 'diagnose', 'light', false, 'editor');
  const draft = page.getByTestId('native-observation-401');
  const trigger = draft.getByRole('button', {
    name: 'Record observation manually',
    exact: true
  });
  await trigger.click();
  await draft
    .getByLabel('Observed fact', { exact: true })
    .fill('Synthetic retained draft.');
  const attachment = draft.getByRole('combobox', {
    name: 'Existing Work attachment (optional)',
    exact: true
  });
  await expect(attachment).toBeEnabled();
  await attachment.click();
  await expect(
    page.getByRole('option', {
      name: new RegExp(filename.replaceAll('.', '\\.'))
    })
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(attachment).toHaveAttribute('aria-expanded', 'false');
  await expect(attachment).toBeFocused();
  await expect(draft.getByLabel('Observed fact', { exact: true })).toHaveValue(
    'Synthetic retained draft.'
  );
  await expect(
    draft.getByRole('button', { name: 'Hide manual observation', exact: true })
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`D5 wrapped badge text survives the root's rounded clip ${scheme}`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width: 1100, height: 800 });
    const fixture = await closeoutWork(page);
    fixture.setOverview(workScenario());
    await openExecutionPanel(page, 'work', scheme);
    const badge = page
      .getByRole('main')
      .locator('.mantine-Badge-root')
      .filter({ hasText: 'Lifecycle:' })
      .first();
    await badge.evaluate((node) => {
      node.style.maxWidth = '8em';
    });
    await textZoom(page, true);
    expect(
      await badge
        .locator('.mantine-Badge-label')
        .evaluate(
          (node) =>
            node.getBoundingClientRect().height /
            Number.parseFloat(getComputedStyle(node).lineHeight)
        )
    ).toBeGreaterThan(1.5);
    await unclippedBadges(page.getByRole('main'));
    await badge.screenshot({
      path: info.outputPath(`wrapped-badge-${scheme}.png`)
    });
  });
}

for (const width of [1000, 1100])
  for (const surface of ['work', 'list'] as const) {
    test(`D6 saved wide assistant preserves ${surface} width and uses a modal overlay at ${width}`, async ({
      page
    }, info) => {
      await page.setViewportSize({ width, height: 800 });
      await page.addInitScript(() =>
        localStorage.setItem('ai-chat-drawer-width', '900')
      );
      if (surface === 'work') {
        await closeoutWork(page);
        await phoneAssistant(page);
        await openExecutionPanel(page, 'closeout');
      } else {
        await mockWorkList(page);
        await phoneAssistant(page);
        await openWorkList(page);
      }
      const main = page.getByRole('main', { includeHidden: true });
      const trigger =
        surface === 'work'
          ? page.getByRole('button', {
              name: 'Ask about this work',
              exact: true
            })
          : page.getByTestId('open-ai-chat');
      await trigger.click();
      const drawer = page.getByRole('dialog', {
        name: 'AI Assistant',
        exact: true
      });
      await expect(drawer).toBeVisible();
      await expect(main).toHaveAttribute('data-assistant-layout', 'overlay');
      expect((await main.boundingBox())!.width).toBeGreaterThanOrEqual(600);
      expect((await drawer.boundingBox())!.width).toBeLessThanOrEqual(
        width - 32
      );
      expect(
        await main.evaluate((node) => Boolean(node.closest('[inert]')))
      ).toBe(true);
      await page.screenshot({
        path: info.outputPath(`wide-assistant-${surface}-${width}.png`)
      });
      const resize = page.getByRole('separator', {
        name: 'Resize chat panel',
        exact: true
      });
      const available = await main.evaluate(
        (node) => node.parentElement!.clientWidth
      );
      await resize.focus();
      await page.keyboard.press('Home');
      await expect(resize).toHaveAttribute('aria-valuenow', '340');
      const reserved = available - 340 >= 600;
      await expect(main).toHaveAttribute(
        'data-assistant-layout',
        reserved ? 'reserved' : 'overlay'
      );
      await expect(
        page.getByRole(reserved ? 'complementary' : 'dialog', {
          name: 'AI Assistant',
          exact: true
        })
      ).toBeVisible();
      // The native parent's allocated width includes its horizontal insets.
      // The visible Work surface still stays near 600px, rather than 36/136px.
      expect((await main.boundingBox())!.width).toBeGreaterThanOrEqual(
        reserved ? 560 : 600
      );
      expect(
        await main.evaluate((node) => Boolean(node.closest('[inert]')))
      ).toBe(!reserved);
      await page.getByTestId('close-ai-chat').click();
      await expect(trigger).toBeFocused();
      await expect(main).not.toHaveAttribute('data-assistant-layout');
    });
  }

test('D7 an unrelated storage event cannot reuse a completed command focus target', async ({
  page
}) => {
  const fixture = await closeoutWork(page);
  fixture.setOverview(continuityWork(false, true));
  let writes = 0;
  await page.route('**/api/tasks/work-orders/73/hold/', (route) => {
    writes++;
    const body = route.request().postDataJSON();
    return route.fulfill({
      json: {
        work_order_id: 73,
        command: 'hold',
        event_id: 701,
        idempotency_key: body.idempotency_key,
        lifecycle_status: 'on_hold',
        lifecycle_version: body.expected_version + 1
      }
    });
  });
  await openExecutionPanel(page, 'work');
  await page
    .getByLabel('Reason for hold', { exact: true })
    .fill('Synthetic local hold.');
  await page
    .getByRole('button', { name: 'Hold work record', exact: true })
    .focus();
  await page.keyboard.press('Enter');
  const result = page.getByTestId('hold-resume-record');
  await expect(result.getByRole('alert')).toContainText('Hold recorded');
  await expect(result).toBeFocused();
  await page.evaluate(() => {
    (document.activeElement as HTMLElement).blur();
    const key = Object.keys(localStorage).find((key) =>
      key.startsWith('aimms-work-continuity:')
    )!;
    const saved = JSON.parse(localStorage.getItem(key)!);
    const value = JSON.stringify({
      status: 'uncertain',
      intent: saved.intent,
      message: 'Synthetic other-tab state change.'
    });
    localStorage.setItem(key, value);
    window.dispatchEvent(new StorageEvent('storage', { key, newValue: value }));
  });
  await expect(result).toContainText('Synthetic other-tab state change.');
  expect(
    await page.evaluate(() => document.activeElement === document.body)
  ).toBe(true);
  await expect(result).not.toBeFocused();
  expect(writes).toBe(1);
});

test('D8 command recovery is a non-landmark group with a single alert channel', async ({
  page
}) => {
  await closeoutWork(page);
  await page.route('**/api/tasks/work-orders/73/complete/', (route) => {
    const body = route.request().postDataJSON();
    return route.fulfill({
      json: {
        work_order_id: 73,
        command: 'complete_work_order',
        event_id: 501,
        idempotency_key: body.idempotency_key,
        lifecycle_status: 'completed',
        lifecycle_version: body.expected_version + 1,
        metadata: { closeout_id: 301 }
      }
    });
  });
  await openExecutionPanel(page, 'closeout');
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .focus();
  await page.keyboard.press('Enter');
  const result = page.getByTestId('work-record-update');
  await expect(result).toContainText('Record updated');
  await expect(result).toHaveAttribute('role', 'group');
  await expect(result).toBeFocused();
  await expect(
    page.getByRole('region', { name: 'Maintenance record update', exact: true })
  ).toHaveCount(0);
  await expect(result.getByRole('alert')).toHaveCount(1);
  expect(
    await result
      .getByRole('alert')
      .evaluate((node) => Boolean(node.closest('[aria-live]')))
  ).toBe(false);
});

test('D9 drawer scroll keeps the resize handle fixed and supports enlarged composer text', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const fixture = await closeoutWork(page);
  fixture.setOverview(workScenario());
  await phoneAssistant(page);
  await openExecutionPanel(page, 'work');
  await page
    .getByRole('button', { name: 'Ask about this work', exact: true })
    .click();
  await textZoom(page, true);
  const resize = page.getByRole('separator', {
    name: 'Resize chat panel',
    exact: true
  });
  const before = await resize.boundingBox();
  const body = page.getByTestId('ai-chat-scroll-content');
  await page.getByTestId('ai-chat-work-focus').locator('summary').click();
  await textZoom(page, true);
  const message = page.getByRole('textbox', { name: 'Message', exact: true });
  await message.fill('Synthetic readable composer text.');
  await message.focus();
  await body.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  expect(await body.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  expect(await resize.boundingBox()).toEqual(before);
  await humanNames(page.getByTestId('ai-chat-drawer'));
  await page.screenshot({
    path: info.outputPath('fixed-resize-handle-text200.png')
  });
  await resize.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(resize).not.toHaveAttribute('aria-valuenow', '440');
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('button', { name: 'Ask about this work', exact: true })
  ).toBeFocused();
});
