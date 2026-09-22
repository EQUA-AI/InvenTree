import { expect, test } from '@playwright/test';

const token = 'a'.repeat(32);
const sessionPath = '**/api/auth/v1/auth/session';
const loginPath = '**/api/auth/v1/auth/login';

test.beforeEach(async ({ page }) => {
  await page.goto('/playwright/auth-csrf.html');
  await page.getByLabel('login-username', { exact: true }).fill('test-user');
  await page
    .getByLabel('login-password', { exact: true })
    .fill('test-password');
});

test('existing CSRF cookie survives anonymous state cleanup and login', async ({
  page,
  context
}) => {
  await context.addCookies([
    { name: 'csrftoken', value: token, url: 'http://127.0.0.1:5173' }
  ]);
  let bootstraps = 0;
  await page.route(sessionPath, (route) => {
    bootstraps++;
    return route.abort();
  });
  await page.route(loginPath, (route) =>
    route.fulfill({
      status: 400,
      json: { detail: 'Diagnostic login response' }
    })
  );
  await page.getByRole('button', { name: 'Clear anonymous state' }).click();
  const request = page.waitForRequest(loginPath);
  await page.getByRole('button', { name: 'Log In', exact: true }).click();
  const headers = await (await request).allHeaders();
  expect(headers.cookie).toContain(`csrftoken=${token}`);
  expect(headers['x-csrftoken']).toBe(token);
  expect(bootstraps).toBe(0);
  await expect(page.getByText('Diagnostic login response')).toBeVisible();
  await expect(page.locator('.mantine-Notification-root')).toHaveCount(1);
  expect(
    (await context.cookies()).find((c) => c.name === 'csrftoken')?.value
  ).toBe(token);
});

test('anonymous 401 bootstrap sets a cookie and permits MFA login flow', async ({
  page
}) => {
  await page.route(sessionPath, (route) =>
    route.fulfill({
      status: 401,
      headers: { 'Set-Cookie': `csrftoken=${token}; Path=/; SameSite=Lax` },
      json: { meta: { is_authenticated: false } }
    })
  );
  await page.route(loginPath, (route) =>
    route.fulfill({
      status: 401,
      json: { data: { flows: [{ id: 'mfa_authenticate', is_pending: true }] } }
    })
  );
  const request = page.waitForRequest(loginPath);
  await page.getByRole('button', { name: 'Log In', exact: true }).click();
  const headers = await (await request).allHeaders();
  expect(headers.cookie).toContain(`csrftoken=${token}`);
  expect(headers['x-csrftoken']).toBe(token);
  await expect(page.getByTestId('destination')).toHaveText('/mfa');
  await expect(page.locator('.mantine-Notification-root')).toHaveCount(0);
});

test('a rejected login without an MFA flow shows one error and unlocks retry', async ({
  page,
  context
}) => {
  await context.addCookies([
    { name: 'csrftoken', value: token, url: 'http://127.0.0.1:5173' }
  ]);
  await page.route(loginPath, (route) =>
    route.fulfill({ status: 401, json: { data: {} } })
  );
  await page.getByRole('button', { name: 'Log In', exact: true }).click();
  await expect(page.getByText('Check your input and try again.')).toBeVisible();
  await expect(page.locator('.mantine-Notification-root')).toHaveCount(1);
  await expect(
    page.getByRole('button', { name: 'Log In', exact: true })
  ).toBeEnabled();
  await expect(page.getByTestId('destination')).toHaveText('/login');
});

for (const failure of ['timeout', 'network', 'no-cookie', 'server-error']) {
  test(`${failure} during CSRF setup blocks login and allows retry`, async ({
    page
  }) => {
    let posts = 0;
    await page.route(loginPath, (route) => {
      posts++;
      return route.fulfill({
        status: 400,
        json: { detail: 'Retry reached credential validation' }
      });
    });
    await page.route(sessionPath, async (route) => {
      if (failure === 'timeout') return; // Leave pending until Axios aborts.
      if (failure === 'network') return route.abort();
      return route.fulfill({
        status: failure === 'server-error' ? 503 : 401,
        json: { meta: { is_authenticated: false } }
      });
    });
    await page.getByRole('button', { name: 'Log In', exact: true }).click();
    await expect(
      page.getByText(/Could not prepare a secure login/)
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Log In', exact: true })
    ).toBeEnabled();
    await expect(page.locator('.mantine-Notification-root')).toHaveCount(1);
    expect(posts).toBe(0);
    await page.unroute(sessionPath);
    await page.route(sessionPath, (route) =>
      route.fulfill({
        status: 401,
        headers: { 'Set-Cookie': `csrftoken=${token}; Path=/; SameSite=Lax` },
        json: { meta: { is_authenticated: false } }
      })
    );
    await page.getByRole('button', { name: 'Log In', exact: true }).click();
    await expect(
      page.getByText('Retry reached credential validation')
    ).toBeVisible();
    await expect(page.locator('.mantine-Notification-root')).toHaveCount(1);
    expect(posts).toBe(1);
  });
}
