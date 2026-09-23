import { expect, test } from '@playwright/test';

test('missing page assets offer an explicit reload without discarding a draft automatically', async ({
  page,
  context
}) => {
  await context.addCookies([
    {
      name: 'sessionid',
      value: 'fixture-session',
      url: 'http://127.0.0.1:5173',
      httpOnly: true
    }
  ]);
  await page.goto('/playwright/chunk-recovery.html?return=kept#location');
  await expect(
    page.getByText('Page resources could not be loaded')
  ).toBeVisible();
  await page
    .getByLabel('Unsaved draft')
    .fill('Keep this until I choose to reload');
  await expect(page.getByTestId('loads')).toHaveText('1');
  await expect(page.getByText(/Unsaved changes may be lost/)).toBeVisible();
  await expect(page.getByText(/INVE-E17/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Reload page', exact: true }).click();
  await expect(page.getByTestId('loads')).toHaveText('2');
  await expect(page).toHaveURL(/\?return=kept#location$/);
  expect(
    (await context.cookies()).find((cookie) => cookie.name === 'sessionid')
      ?.value
  ).toBe('fixture-session');
  await expect(
    page.getByRole('button', { name: 'Reload page', exact: true })
  ).toBeVisible();
});

test('ordinary component exceptions keep the diagnostic fallback', async ({
  page
}) => {
  await page.goto(
    '/playwright/chunk-recovery.html?error=ordinary-render-error'
  );
  await expect(
    page.getByText('INVE-E17: Error rendering component: layout')
  ).toBeVisible();
  await expect(
    page.getByText('ordinary-render-error', { exact: true })
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reload page' })).toHaveCount(
    0
  );
});
