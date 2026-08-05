import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures.js';
import { adminuser, readeruser } from '../defaults.js';
import { doCachedLogin } from '../login.js';

const TARGET_PK = 907;

function userPayload(overrides: Record<string, unknown> = {}) {
  return {
    pk: TARGET_PK,
    username: 'closeout-tech',
    first_name: '',
    last_name: '',
    email: '',
    is_active: true,
    is_staff: false,
    is_superuser: false,
    groups: [],
    ...overrides
  };
}

function permissionsPayload(captureGranted: boolean) {
  return {
    user: TARGET_PK,
    username: 'closeout-tech',
    is_superuser: false,
    permissions: [
      {
        codename: 'capture_closeout',
        name: 'Can capture closeout narratives',
        granted_direct: captureGranted,
        via_groups: [],
        effective: captureGranted
      },
      {
        codename: 'review_closeout',
        name: 'Can review closeout proposals',
        granted_direct: false,
        via_groups: ['maintenance-leads'],
        effective: true
      },
      {
        codename: 'reconcile_closeout_parts',
        name: 'Can reconcile closeout part usage',
        granted_direct: false,
        via_groups: [],
        effective: false
      },
      {
        codename: 'verify_closeout',
        name: 'Can verify completed closeouts',
        granted_direct: false,
        via_groups: [],
        effective: false
      },
      {
        codename: 'amend_closeout',
        name: 'Can amend completed closeouts',
        granted_direct: false,
        via_groups: [],
        effective: false
      },
      {
        codename: 'view_closeout_audit',
        name: 'Can view closeout audit surfaces',
        granted_direct: false,
        via_groups: [],
        effective: false
      }
    ]
  };
}

async function mockUser(page: Page) {
  await page.route(
    (url) => url.pathname === `/api/user/${TARGET_PK}/`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(userPayload())
      });
    }
  );
}

test('staff admin can grant a direct closeout permission', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, {
    user: adminuser,
    url: `core/user/${TARGET_PK}/`
  });
  await mockUser(page);

  let granted = false;
  const posts: Record<string, unknown>[] = [];
  await page.route(
    (url) => url.pathname === `/api/tasks/closeout/permissions/${TARGET_PK}/`,
    async (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON();
        posts.push(body);
        granted = Boolean(body.granted);
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ...permissionsPayload(granted),
            changed: true
          })
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(permissionsPayload(granted))
      });
    }
  );
  await page.reload();

  await page
    .getByRole('tab', { name: 'Closeout Permissions', exact: true })
    .click();
  await expect(page.getByTestId('closeout-permissions-table')).toBeVisible();
  await expect(
    page.getByText('Can capture closeout narratives', { exact: true })
  ).toBeVisible();
  // The group-conferred grant renders its origin badge.
  await expect(
    page.getByText('maintenance-leads', { exact: true })
  ).toBeVisible();

  const row = page.getByTestId('closeout-perm-capture_closeout');
  const toggle = row.getByRole('switch');
  await expect(toggle).not.toBeChecked();
  await toggle.click();

  await expect(toggle).toBeChecked();
  await expect
    .poll(() => posts)
    .toEqual([{ codename: 'capture_closeout', granted: true }]);
  await expect(row.getByText('Granted', { exact: true })).toBeVisible();
});

test('non-staff viewers do not see the panel', async ({ browser }) => {
  const page = await doCachedLogin(browser, {
    user: readeruser,
    url: `core/user/${TARGET_PK}/`
  });
  await mockUser(page);
  await page.reload();

  await page.getByRole('tab', { name: 'User Details', exact: true }).waitFor();
  await expect(
    page.getByRole('tab', { name: 'Closeout Permissions', exact: true })
  ).toHaveCount(0);
});
