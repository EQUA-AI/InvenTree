import { expect, test } from '@playwright/test';

import { loadDrawerFixture, prepareDrawerFixture } from './ai_drawer_harness';

test('selecting a conversation closes its dropdown', async ({ page }) => {
  await prepareDrawerFixture(page);
  await loadDrawerFixture(page);
  await page.getByRole('button', { name: 'select-ai-chat-thread' }).click();
  const menu = page.getByRole('menu');
  await expect(menu).toBeVisible();
  await menu.getByText('Server conversation', { exact: true }).click();
  await expect(menu).not.toBeVisible();
});

// Supervisor QA: real drawer rendering with fixture APIs, not authenticated E2E.
test('composer controls remain reachable without horizontal overflow', async ({
  page
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await prepareDrawerFixture(page);
  await loadDrawerFixture(page);
  const expectedScheme = await page.evaluate(() =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  );
  await expect(page.locator('html')).toHaveAttribute(
    'data-mantine-color-scheme',
    expectedScheme
  );
  const composer = page.getByTestId('ai-chat-composer');
  await expect(composer).toBeVisible();
  await expect(
    composer.getByRole('textbox', { name: 'Message', exact: true })
  ).toBeVisible();
  const mic = composer.getByRole('button', {
    name: 'Start voice session',
    exact: true
  });
  await expect(mic).toBeVisible();
  const bounds = await mic.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.width).toBeGreaterThanOrEqual(44);
  expect(bounds!.height).toBeGreaterThanOrEqual(44);
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    page.viewportSize()!.width
  );
  await expect(
    composer.getByRole('button', { name: 'send-ai-chat-message' })
  ).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1
  );
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
});
