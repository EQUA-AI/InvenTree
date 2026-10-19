import { expect, test } from '@playwright/test';

import { nativeBaseline } from './guards';
import { mockMachine } from './machineFixtures';

nativeBaseline();

test('native Details fields and machine placement remain available with the Move dialog', async ({
  page
}) => {
  await mockMachine(page);
  await page.goto(
    '/playwright/s0-native.html?surface=machine&entry=/machines/machine/18/details/'
  );
  for (const label of [
    'Name',
    'Active record',
    'Legacy location label',
    'Manufacturer',
    'Model',
    'Serial Number',
    'Description'
  ]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(
    page.getByText('Synthetic description retained in native Details')
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Ask about this machine' })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Create repair', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Start repair', exact: true })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Move machine', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Move machines' });
  await expect(dialog.getByLabel('Reason for move')).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Review move' })
  ).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(dialog).toBeHidden();
});

test('viewer keeps QR access while barcode mutations and Move stay hidden', async ({
  page
}) => {
  await mockMachine(page, { canChange: false });
  await page.route('**/api/barcode/generate/', (route) =>
    route.fulfill({ json: { barcode: 'S0-synthetic-QR' } })
  );
  await page.goto(
    '/playwright/s0-native.html?surface=machine&role=viewer&scheme=dark&entry=/machines/machine/18/details/'
  );
  await expect(page.getByText('Serial Number', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Move machine', exact: true })
  ).toHaveCount(0);
  await page
    .getByRole('button', { name: 'action-menu-barcode-actions', exact: true })
    .click();
  await expect(
    page.getByRole('menuitem', {
      name: 'action-menu-barcode-actions-link-barcode',
      exact: true
    })
  ).toHaveCount(0);
  await expect(
    page.getByRole('menuitem', {
      name: 'action-menu-barcode-actions-unlink-barcode',
      exact: true
    })
  ).toHaveCount(0);
  await page
    .getByRole('menuitem', {
      name: 'action-menu-barcode-actions-view-barcode',
      exact: true
    })
    .click();
  await expect(
    page.getByRole('dialog').getByRole('img', { name: 'QR Code', exact: true })
  ).toBeVisible();
});

test('an editor with an unlinked machine can reach Link Barcode', async ({
  page
}) => {
  await mockMachine(page);
  await page.goto(
    '/playwright/s0-native.html?surface=machine&entry=/machines/machine/18/details/'
  );
  await page
    .getByRole('button', { name: 'action-menu-barcode-actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', {
      name: 'action-menu-barcode-actions-link-barcode',
      exact: true
    })
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Link Barcode', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('dialog').getByText('Scan or enter barcode data')
  ).toBeVisible();
});

test('an editor with a linked machine can reach Unlink Barcode', async ({
  page
}) => {
  await mockMachine(page, { linked: true });
  await page.goto(
    '/playwright/s0-native.html?surface=machine&entry=/machines/machine/18/details/'
  );
  await page
    .getByRole('button', { name: 'action-menu-barcode-actions', exact: true })
    .click();
  await expect(
    page.getByRole('menuitem', {
      name: 'action-menu-barcode-actions-link-barcode',
      exact: true
    })
  ).toHaveCount(0);
  await page
    .getByRole('menuitem', {
      name: 'action-menu-barcode-actions-unlink-barcode',
      exact: true
    })
    .click();
  await expect(
    page
      .getByRole('dialog')
      .getByText('This will remove the link to the associated barcode')
  ).toBeVisible();
  await expect(
    page
      .getByRole('dialog')
      .getByRole('button', { name: 'Unlink Barcode', exact: true })
  ).toBeVisible();
});

test('the production layout preserves native Details and QR access with synthetic shell providers', async ({
  page
}, testInfo) => {
  const originalViewport =
    testInfo.project.name === 's0-desktop'
      ? { width: 1440, height: 900 }
      : { width: 390, height: 844 };
  await page.setViewportSize(originalViewport);
  await mockMachine(page, { canChange: false });
  await page.route('**/api/notifications/?*', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({ json: { threads: [], shared_threads: [] } })
  );
  await page.route('**/api/ai/voice/capability', (route) =>
    route.fulfill({ json: { enabled: false, voices: [] } })
  );
  await page.route('**/api/aichat/ui/capabilities/', (route) =>
    route.fulfill({
      json: {
        version: 1,
        risk_radar: false,
        command_center: false,
        maintenance_metrics: 1,
        backend_commit: 's0-synthetic'
      }
    })
  );
  await page.route('**/api/barcode/generate/', (route) =>
    route.fulfill({ json: { barcode: 'S0-synthetic-QR' } })
  );
  await page.goto(
    '/playwright/s0-native.html?surface=machine-shell&role=viewer&entry=/machines/machine/18/details/'
  );
  await expect(
    page.getByRole('button', { name: 'open-notifications', exact: true })
  ).toBeVisible();
  await expect(page.getByText('Serial Number', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Synthetic description retained in native Details')
  ).toBeVisible();
  const viewports =
    testInfo.project.name === 's0-desktop'
      ? [originalViewport, { width: 1024, height: 768 }]
      : [originalViewport];
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await expect(
      page.getByText('Serial Number', { exact: true })
    ).toBeVisible();
    const capture = testInfo.outputPath(`shell-details-${viewport.width}.png`);
    await page.screenshot({
      path: capture,
      fullPage: true,
      animations: 'disabled'
    });
    await testInfo.attach(`shell-details-${viewport.width}`, {
      path: capture,
      contentType: 'image/png'
    });
    const dimensions = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth
    }));
    expect(dimensions.documentWidth).toBeLessThanOrEqual(
      dimensions.viewportWidth
    );
    await testInfo.attach(`shell-dimensions-${viewport.width}`, {
      body: JSON.stringify({
        synthetic: true,
        surface: 'production layout with synthetic providers',
        ...dimensions
      }),
      contentType: 'application/json'
    });
  }
  await page.setViewportSize(originalViewport);
  await page
    .getByRole('button', { name: 'action-menu-barcode-actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', {
      name: 'action-menu-barcode-actions-view-barcode',
      exact: true
    })
    .click();
  await expect(
    page.getByRole('dialog').getByRole('img', { name: 'QR Code', exact: true })
  ).toBeVisible();
});

for (const scheme of ['light', 'dark']) {
  test(`native panels retain keyboard navigation and long identity values in ${scheme} mode`, async ({
    page
  }, testInfo) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await mockMachine(page, { canChange: false, longValues: true });
    await page.route('**/api/attachment/**', (route) =>
      route.fulfill({
        json:
          route.request().method() === 'OPTIONS'
            ? { actions: {} }
            : { count: 0, results: [], next: null }
      })
    );
    await page.goto(
      `/playwright/s0-native.html?surface=machine&role=viewer&scheme=${scheme}&entry=/machines/machine/18/details/`
    );
    const details = page.getByRole('tab', { name: 'Details', exact: true });
    const attachments = page.getByRole('tab', {
      name: 'Attachments',
      exact: true
    });
    await expect(details).toHaveAttribute('aria-selected', 'true');
    await details.focus();
    await details.press('End');
    await expect(attachments).toBeFocused();
    await attachments.press('Enter');
    await expect(attachments).toHaveAttribute('aria-selected', 'true');
    await expect(
      page.getByText('No attachments found', { exact: true })
    ).toBeVisible();
    await expect(page.getByTestId('destination')).toHaveText(
      '/machines/machine/18/attachments'
    );
    await details.click();
    await expect(details).toHaveAttribute('aria-selected', 'true');
    await expect(
      page.getByText('Serial Number', { exact: true })
    ).toBeVisible();
    for (const width of [360, 390, 430, 1024]) {
      await page.setViewportSize({ width, height: 844 });
      await expect(
        page.getByText('Serial Number', { exact: true })
      ).toBeVisible();
      await expect(page.getByRole('tablist')).toHaveAttribute(
        'aria-orientation',
        width < 768 ? 'horizontal' : 'vertical'
      );
      const dimensions = await page.evaluate(() => ({
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth
      }));
      expect(dimensions.documentWidth).toBeLessThanOrEqual(
        dimensions.viewportWidth
      );
      await testInfo.attach(`long-identity-${scheme}-${width}`, {
        body: JSON.stringify({ synthetic: true, scheme, ...dimensions }),
        contentType: 'application/json'
      });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    const capture = testInfo.outputPath(`long-identity-${scheme}-390.png`);
    await page.screenshot({
      path: capture,
      fullPage: true,
      animations: 'disabled'
    });
    await testInfo.attach(`long-identity-${scheme}-390`, {
      path: capture,
      contentType: 'image/png'
    });
  });
}
