import { expect, test } from '@playwright/test';

const session = '**/api/auth/v1/auth/session';
const profile = '**/api/user/me/**';
const user = {
  pk: 42,
  username: 'existing-account',
  profile: {},
  roles: {},
  permissions: {}
};

test.beforeEach(async ({ page, context }) => {
  await context.addCookies([
    { name: 'csrftoken', value: 'a'.repeat(32), url: 'http://127.0.0.1:5173' }
  ]);
  await page.route('**/api/**', (route) => route.fulfill({ json: [] }));
  await page.route(profile, (route) => route.fulfill({ json: user }));
  await page.route(session, (route) =>
    route.fulfill({
      json: { meta: { is_authenticated: true }, data: { user: { id: 42 } } }
    })
  );
});

test('409 restores the existing account without deleting its session', async ({
  page
}) => {
  let deletes = 0;
  await page.route(session, (route) => {
    if (route.request().method() === 'DELETE') deletes++;
    return route.fulfill({
      json: { meta: { is_authenticated: true }, data: { user: { id: 42 } } }
    });
  });
  await page.route('**/api/auth/v1/auth/login', (route) =>
    route.fulfill({ status: 409, json: {} })
  );
  await page.goto('/playwright/auth-csrf.html');
  await page
    .getByLabel('login-username', { exact: true })
    .fill('entered-account');
  await page.getByLabel('login-password', { exact: true }).fill('password');
  await page.getByRole('button', { name: 'Log In', exact: true }).click();
  await expect(page.getByTestId('destination')).toHaveText('/home');
  await expect(page.getByTestId('session-state')).toContainText('"user":42');
  expect(deletes).toBe(0);
  await expect(page.getByText(/conflicting session/)).toHaveCount(0);
});

test('a three-second profile response restores the session without requesting credentials', async ({
  page
}) => {
  await page.route(profile, async (route) => {
    await new Promise((r) => setTimeout(r, 3000));
    await route.fulfill({ json: user });
  });
  await page.goto('/playwright/auth-csrf.html?restore=1&timeout=5000');
  await expect(page.getByTestId('destination')).toHaveText('/home', {
    timeout: 10000
  });
  await expect(page.getByLabel('login-password', { exact: true })).toHaveCount(
    0
  );
});

for (const failure of ['timeout', 'offline', 'server-error', 'malformed']) {
  test(`${failure} keeps session recovery retryable without a new login`, async ({
    page
  }) => {
    let posts = 0;
    page.on('request', (r) => {
      if (r.method() === 'POST' && r.url().endsWith('/auth/login')) posts++;
    });
    await page.route(profile, (route) => {
      if (failure === 'timeout') return;
      if (failure === 'offline') return route.abort();
      return route.fulfill({
        status: failure === 'server-error' ? 503 : 200,
        json: {}
      });
    });
    await page.goto('/playwright/auth-csrf.html?restore=1');
    await expect(page.getByText('Could not verify your session')).toBeVisible({
      timeout: 25000
    });
    await expect(page.getByTestId('destination')).toHaveText('/logged-in');
    await expect(
      page.getByLabel('login-password', { exact: true })
    ).toHaveCount(0);
    await page.unroute(profile);
    await page.route(profile, (route) => route.fulfill({ json: user }));
    await page.getByRole('button', { name: 'Retry session check' }).click();
    await expect(page.getByTestId('destination')).toHaveText('/home');
    expect(posts).toBe(0);
  });
}

test('refresh and a new tab restore slow session and profile reads', async ({
  page,
  context
}) => {
  test.setTimeout(45000);
  await page.goto('/playwright/auth-csrf.html?restore=1');
  await expect(page.getByTestId('destination')).toHaveText('/home');
  // Context routes also apply to a newly opened tab, which starts with no
  // in-memory user state. Each read exceeds the old five-second timeout.
  await page.unrouteAll();
  await context.route('**/api/**', (route) => route.fulfill({ json: [] }));
  for (const [url, json] of [
    [session, { meta: { is_authenticated: true } }],
    [profile, user]
  ] as const) {
    await context.route(url, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 6000));
      await route.fulfill({ json });
    });
  }
  await page.reload();
  await expect(page.getByTestId('destination')).toHaveText('/home', {
    timeout: 16000
  });
  const tab = await context.newPage();
  await tab.goto('/playwright/auth-csrf.html?restore=1');
  await expect(tab.getByTestId('destination')).toHaveText('/home', {
    timeout: 16000
  });
  await expect(tab.getByTestId('session-state')).toContainText('"user":42');
  await expect(tab.getByText('Could not verify your session')).toHaveCount(0);
});

for (const endpoint of [session, profile]) {
  test(`a transient ${endpoint} failure recovers automatically`, async ({
    page
  }) => {
    let reads = 0;
    let mutations = 0;
    page.on('request', (request) => {
      if (
        ['POST', 'DELETE'].includes(request.method()) &&
        request.url().includes('/auth/')
      ) {
        mutations++;
      }
    });
    await page.route(endpoint, (route) => {
      reads++;
      return route.fulfill({
        status: reads === 1 ? 503 : 200,
        json: endpoint === session ? { meta: { is_authenticated: true } } : user
      });
    });
    await page.goto('/playwright/auth-csrf.html?restore=1');
    await expect(page.getByTestId('destination')).toHaveText('/home');
    expect(reads).toBe(2);
    expect(mutations).toBe(0);
  });
}

test('transient revalidation preserves identity; confirmed expiry clears it', async ({
  page
}) => {
  await page.goto('/playwright/auth-csrf.html?restore=1');
  await expect(page.getByTestId('destination')).toHaveText('/home');
  await page.route(session, (route) =>
    route.fulfill({ status: 503, json: {} })
  );
  await page.getByRole('button', { name: 'Recheck session' }).click();
  await expect(page.getByText('Could not verify your session')).toBeVisible();
  await expect(page.getByTestId('session-state')).toContainText('"user":42');
  await expect(page.getByText('Private account page')).toHaveCount(0);
  await page.route(session, (route) =>
    route.fulfill({ status: 401, json: { meta: { is_authenticated: false } } })
  );
  await page.getByRole('button', { name: 'Retry session check' }).click();
  await expect(page.getByTestId('destination')).toHaveText('/login');
  await expect(page.getByTestId('session-state')).not.toContainText(
    '"user":42'
  );
  await expect(
    page.getByLabel('login-password', { exact: true })
  ).toBeVisible();
});

for (const boundary of ['sign out', 'server change']) {
  test(`late profile cannot restore a user after ${boundary}`, async ({
    page
  }) => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let arrived!: () => void;
    const requested = new Promise<void>((resolve) => {
      arrived = resolve;
    });
    await page.route(profile, async (route) => {
      arrived();
      await held;
      await route.fulfill({ json: user });
    });
    await page.route(session, (route) =>
      route.fulfill({
        status: route.request().method() === 'DELETE' ? 401 : 200,
        json: {
          meta: { is_authenticated: route.request().method() !== 'DELETE' }
        }
      })
    );
    await page.goto('/playwright/auth-csrf.html?restore=1&timeout=5000');
    await requested;
    await page
      .getByRole('button', {
        name: boundary === 'sign out' ? 'Explicit sign out' : 'Change server'
      })
      .click();
    await expect(page.getByTestId('session-state')).toContainText(
      '"authenticated":false'
    );
    const completed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/user/me/'
    );
    release();
    await completed;
    await expect(page.getByTestId('session-state')).not.toContainText(
      '"user":42'
    );
    await expect(page.getByTestId('destination')).toHaveText('/login');
  });
}

for (const status of [401, 403]) {
  test(`profile ${status} rechecks the session without destroying it`, async ({
    page
  }) => {
    let deletes = 0;
    await page.route(session, (route) => {
      if (route.request().method() === 'DELETE') deletes++;
      return route.fulfill({ json: { meta: { is_authenticated: true } } });
    });
    await page.route(profile, (route) => route.fulfill({ status, json: {} }));
    await page.goto('/playwright/auth-csrf.html?restore=1');
    await expect(page.getByText('Could not verify your session')).toBeVisible();
    await expect(page.getByTestId('session-state')).toContainText(
      '"authenticated":true'
    );
    expect(deletes).toBe(0);
  });
}

test('an unconfirmed sign-out remains retryable without claiming success', async ({
  page
}) => {
  await page.goto('/playwright/auth-csrf.html?restore=1');
  await expect(page.getByTestId('destination')).toHaveText('/home');
  await page.route(session, (route) =>
    route.fulfill({ status: 503, json: {} })
  );
  await page.getByRole('button', { name: 'Explicit sign out' }).click();
  await expect(page.getByText('Sign out incomplete')).toBeVisible();
  await expect(page.getByTestId('session-state')).not.toContainText(
    '"user":42'
  );
  await expect(page.getByText('Private account page')).toHaveCount(0);
  await page.route(session, (route) =>
    route.fulfill({ status: 401, json: { meta: { is_authenticated: false } } })
  );
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByTestId('destination')).toHaveText('/login');
});

test('URL credential login waits for anonymous session restoration', async ({
  page
}) => {
  let sessionChecked = false;
  let loginAfterCheck = false;
  await page.route(session, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 250));
    sessionChecked = true;
    await route.fulfill({
      status: 401,
      json: { meta: { is_authenticated: false } }
    });
  });
  await page.route('**/api/auth/v1/auth/login', (route) => {
    loginAfterCheck = sessionChecked;
    return route.fulfill({ json: { meta: { is_authenticated: true } } });
  });
  await page.goto('/playwright/auth-csrf.html?auto=1');
  await expect(page.getByTestId('destination')).toHaveText('/home');
  expect(loginAfterCheck).toBe(true);
});
