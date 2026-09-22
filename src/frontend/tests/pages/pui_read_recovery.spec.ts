import { expect, test } from '@playwright/test';

test('closed proposals do not poll; a session error stops polling and explicit retry recovers', async ({
  page
}) => {
  let requests = 0;
  let status = 403;
  await page.route('**/api/aichat/proposals/', (route) => {
    requests++;
    return route.fulfill({
      status,
      json:
        status === 200
          ? { results: [] }
          : { detail: 'Authentication credentials were not provided.' }
    });
  });
  await page.clock.install();
  await page.goto('/playwright/maintenance-widgets.html?mode=proposals');
  await page.clock.fastForward(120_000);
  expect(requests).toBe(0);
  await page.getByRole('button', { name: 'Open proposals' }).click();
  await expect(
    page.getByText('Your session has expired. Sign in again, then retry.')
  ).toBeVisible();
  await page.clock.fastForward(120_000);
  expect(requests).toBe(1);
  status = 200;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByTestId('read-error-notice')).toHaveCount(0);
  expect(requests).toBe(2);
  await page.getByRole('button', { name: 'Close proposals' }).click();
  await page.clock.fastForward(120_000);
  expect(requests).toBe(2);
});

test('unsupported metrics stop polling and do not pretend the result is zero', async ({
  page
}) => {
  let requests = 0;
  await page.route('**/api/aichat/ui/maintenance-metrics/**', (route) => {
    requests++;
    return route.fulfill({ status: 404, json: { detail: 'Not found' } });
  });
  await page.clock.install();
  await page.goto('/playwright/maintenance-widgets.html?metric=open');
  await expect(
    page.getByText('This feature is unavailable on the selected server.')
  ).toBeVisible();
  await page.clock.fastForward(180_000);
  expect(requests).toBe(1);
  await page
    .getByRole('button', { name: /View contributing records/ })
    .count()
    .then((count) => expect(count).toBe(0));
});

test('revoking a widget role displays unavailable instead of a pending spinner', async ({
  page
}) => {
  await page.route('**/api/aichat/ui/maintenance-metrics/**', (route) =>
    route.fulfill({
      json: { id: 'open', version: 1, state: 'unavailable', complete: false }
    })
  );
  await page.goto('/playwright/maintenance-widgets.html?metric=open');
  await page.getByRole('button', { name: 'Revoke role' }).click();
  await expect(
    page.getByText('Unavailable for your current role or maintenance scope.')
  ).toBeVisible();
  await expect(
    page.getByTestId('maintenance-open').locator('.mantine-Loader-root')
  ).toHaveCount(0);
});

test('disabled Risk Radar is discovered without requests to its forbidden routes', async ({
  page
}) => {
  let requests = 0;
  await page.route('**/api/aichat/ui/capabilities/', (route) =>
    route.fulfill({ json: { version: 1, risk_radar: false } })
  );
  await page.route('**/api/repair/risk-scopes/', (route) => {
    requests++;
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto('/playwright/maintenance-widgets.html?mode=risk');
  await expect(page.getByText('Risk unavailable')).toBeVisible();
  expect(requests).toBe(0);
});
