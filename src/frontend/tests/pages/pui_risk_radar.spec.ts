import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';

const AS_OF = '2026-08-04T12:00:00Z';

function makeFinding(pk: number, title = `Finding ${pk}`) {
  return {
    pk,
    scope_key: 'c1',
    rule_code: 'PACKET_STALLED',
    rule_version: 1,
    category: 'operations',
    severity: 'medium',
    severity_factors: {},
    source_model: 'repairpacket',
    source_id: pk,
    title,
    summary: 'A maintenance condition needs review.',
    state: 'open',
    owner: null,
    owner_username: null,
    first_seen: AS_OF,
    last_seen: AS_OF,
    condition_started_at: AS_OF,
    source_as_of: AS_OF,
    due_at: null,
    due_breached: false,
    age_hours: 1,
    snooze_until: null,
    dismiss_recheck_at: null,
    reopen_count: 0,
    version: 3
  };
}

async function mockScope(page: Page) {
  await page.route(
    (url) => url.pathname === '/api/repair/risk-scopes/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scopes: ['c1'],
          authorization_fingerprint: 'risk-test-fingerprint'
        })
      });
    }
  );
}

async function openRiskRadar(page: Page) {
  await page.reload();
  await page.getByRole('tab', { name: 'Risk Radar', exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Risk Radar', exact: true }).click();
  await page.getByText('Risk Radar', { exact: true }).last().waitFor();
}

test('Risk Radar paginates the complete server-backed queue', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'maintenance/board' });
  await mockScope(page);

  const firstPage = Array.from({ length: 50 }, (_, index) =>
    makeFinding(index + 1)
  );
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/',
    async (route) => {
      const offset = Number(
        new URL(route.request().url()).searchParams.get('offset')
      );
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scope: 'c1',
          as_of: AS_OF,
          source_freshness: [],
          count: 51,
          results: offset === 50 ? [makeFinding(51)] : firstPage
        })
      });
    }
  );

  await openRiskRadar(page);
  await expect(page.getByTestId('risk-finding-row')).toHaveCount(50);
  await page
    .getByTestId('risk-radar-pagination')
    .getByText('2', { exact: true })
    .click();

  await expect(page.getByTestId('risk-finding-row')).toHaveCount(1);
  await expect(page.getByText('Finding 51', { exact: true })).toBeVisible();
});

test('Risk Radar retains dismissal text and blocks retry after a stale conflict', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'maintenance/board' });
  await mockScope(page);
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scope: 'c1',
          as_of: AS_OF,
          source_freshness: [],
          count: 1,
          results: [makeFinding(7, 'Dismiss me')]
        })
      });
    }
  );

  let dismissBody: Record<string, unknown> | null = null;
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/7/dismiss/',
    async (route) => {
      dismissBody = route.request().postDataJSON();
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          code: 'FINDING_STATE_CONFLICT',
          detail: 'The finding changed; close this dialog and review it again.',
          correlation_id: 'dismiss-test',
          current_version: 4
        })
      });
    }
  );

  await openRiskRadar(page);
  await page.getByRole('button', { name: 'risk-finding-actions-7' }).click();
  await page.getByRole('menuitem', { name: 'Dismiss' }).click();

  const dialog = page.getByRole('dialog', { name: 'Dismiss finding' });
  const reason = dialog.getByRole('textbox', { name: 'Reason' });
  const confirm = dialog.getByRole('button', { name: 'Dismiss' });
  await expect(confirm).toBeDisabled();
  await reason.fill('   ');
  await expect(confirm).toBeDisabled();

  // Exercise the insecure-origin fallback even though Playwright's localhost
  // test origin is treated as secure by browsers.
  await page.evaluate(() => {
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      configurable: true,
      value: undefined
    });
  });
  await reason.fill('  Known duplicate  ');
  await confirm.click();

  await expect(dialog).toBeVisible();
  await expect(reason).toHaveValue('  Known duplicate  ');
  await expect(
    dialog.getByText(
      'The finding changed; close this dialog and review it again.'
    )
  ).toBeVisible();
  await expect(confirm).toBeDisabled();
  expect(dismissBody).toMatchObject({
    expected_version: 3,
    reason: 'Known duplicate'
  });
  expect(`${dismissBody?.idempotency_key}`).toMatch(/^risk-/);
});

test('Risk Radar reports a queued recheck and refreshes after dispatch', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'maintenance/board' });
  await mockScope(page);

  let listRequests = 0;
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/',
    async (route) => {
      listRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scope: 'c1',
          as_of: AS_OF,
          source_freshness: [],
          count: 1,
          results: [makeFinding(9, 'Recheck me')]
        })
      });
    }
  );

  const recheckBodies: Record<string, unknown>[] = [];
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/9/recheck/',
    async (route) => {
      recheckBodies.push(route.request().postDataJSON());
      if (recheckBodies.length === 1) {
        await route.abort('failed');
        return;
      }
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ queued: true, correlation_id: 'recheck-test' })
      });
    }
  );

  await openRiskRadar(page);
  const requestsBeforeRecheck = listRequests;
  await page.getByRole('button', { name: 'risk-finding-actions-9' }).click();
  await page.getByRole('menuitem', { name: 'Recheck' }).click();

  await expect(page.getByText('Recheck failed')).toBeVisible();
  await expect.poll(() => listRequests).toBeGreaterThan(requestsBeforeRecheck);
  const requestsBeforeSuccessfulRetry = listRequests;
  await page.getByRole('button', { name: 'risk-finding-actions-9' }).click();
  await page.getByRole('menuitem', { name: 'Recheck' }).click();

  await expect(page.getByText('Recheck queued')).toBeVisible();
  await expect.poll(() => recheckBodies).toHaveLength(2);
  expect(recheckBodies[1]).toMatchObject({ expected_version: 3 });
  expect(`${recheckBodies[1].idempotency_key}`).toMatch(
    /^(risk-|[0-9a-f-]{36}$)/
  );
  expect(recheckBodies[1].idempotency_key).toBe(
    recheckBodies[0].idempotency_key
  );
  await expect
    .poll(() => listRequests, { timeout: 6000 })
    .toBeGreaterThan(requestsBeforeSuccessfulRetry);
});
