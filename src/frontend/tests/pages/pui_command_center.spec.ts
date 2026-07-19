import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';

/**
 * Browser coverage for the Risk Radar / Command Center page, driven entirely
 * against mocked API routes (the live backend keeps the feature flags off,
 * so the real endpoints return 404).
 */

const AS_OF = '2026-07-18T10:00:00Z';

function makeFinding(
  pk: number,
  severity: string,
  title: string,
  overrides: Record<string, any> = {}
) {
  return {
    pk: pk,
    scope_key: 'c1',
    rule_code: 'RR-RTS-BLOCKED',
    rule_version: 3,
    category: 'safety',
    severity: severity,
    severity_factors: { downtime_hours: 26 },
    source_model: 'repairpacket',
    source_id: 11,
    title: title,
    summary: 'Packet is blocked at the return-to-service gate.',
    state: 'open',
    owner: null,
    owner_username: null,
    first_seen: '2026-07-17T08:00:00Z',
    last_seen: AS_OF,
    condition_started_at: '2026-07-17T08:00:00Z',
    source_as_of: '2026-07-18T09:50:00Z',
    due_at: '2026-07-19T08:00:00Z',
    due_breached: false,
    age_hours: 26,
    snooze_until: null,
    dismiss_recheck_at: null,
    reopen_count: 0,
    version: 3,
    action_links: [
      {
        label: 'RP-0001',
        target_kind: 'repair_packet',
        target_id: 11,
        route: '/repair/packets/11/'
      }
    ],
    ...overrides
  };
}

const SUMMARY = {
  as_of: AS_OF,
  scope: 'c1',
  stale: false,
  freshness: [
    {
      rule: 'rts_blocked',
      enabled: true,
      gate: null,
      last_complete: AS_OF,
      last_status: 'success',
      degraded: false,
      source_disabled: false,
      dormant: false
    }
  ],
  source_freshness: [
    { source: 'packets', as_of: AS_OF, degraded: false },
    { source: 'job_kits', as_of: AS_OF, degraded: true }
  ],
  headline: { critical: 2, high: 1, medium: 0, low: 0 },
  by_category: { safety: 2, flow: 1 },
  queue: [],
  flow: {
    packets: { draft: 1, in_progress: 2, closed_7d: 3 },
    work_orders: { source_disabled: true }
  },
  aging: {
    approvals_in_review: { p50_hours: 4, max_hours: 12 },
    shortages_open: { source_disabled: true }
  },
  return_to_service: []
};

async function mockRiskRadarApi(page: Page) {
  await page.route(
    (url) => url.pathname === '/api/repair/risk-scopes/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scopes: ['c1'],
          authorization_fingerprint: 'auth-c1'
        })
      });
    }
  );
  await page.route(
    (url) => url.pathname === '/api/repair/command-center/summary/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(SUMMARY)
      });
    }
  );
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scope: 'c1',
          as_of: AS_OF,
          source_freshness: [
            { source: 'packets', as_of: AS_OF, degraded: false },
            { source: 'job_kits', as_of: AS_OF, degraded: true }
          ],
          count: 2,
          results: [
            makeFinding(101, 'critical', 'Packet blocked at RTS gate'),
            makeFinding(102, 'high', 'Approval overdue', {
              rule_code: 'RR-APPROVAL-AGING',
              category: 'flow',
              due_breached: true,
              age_hours: 60
            })
          ]
        })
      });
    }
  );
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/101/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...makeFinding(101, 'critical', 'Packet blocked at RTS gate'),
          evidence: { packet: 'RP-0001', gate: 'RTS' },
          events: [
            {
              pk: 1,
              event_type: 'created',
              actor: null,
              actor_username: null,
              reason: null,
              metadata: {},
              created_at: '2026-07-17T08:00:00Z'
            }
          ]
        })
      });
    }
  );
}

test('command center renders summary and finding queue from the API', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'command-center' });
  await mockRiskRadarApi(page);
  // The page mounted during login, before the mocks existed; reload so the
  // queries fire against the mocked routes.
  await page.reload();

  // Headline severity tiles: icon + text label + mocked count
  await expect(page.getByTestId('headline-critical')).toContainText('Critical');
  await expect(page.getByTestId('headline-critical')).toContainText('2');
  await expect(page.getByTestId('headline-high')).toContainText('High');

  // Blocked work-order source must be called out, never rendered as zero
  await expect(page.getByText('Source disabled').first()).toBeVisible();
  await expect(page.getByText('job_kits: degraded')).toBeVisible();

  // Queue rows render with severity text labels (never color-only)
  await expect(page.getByTestId('finding-row-101')).toContainText('Critical');
  await expect(page.getByTestId('finding-row-101')).toContainText(
    'Packet blocked at RTS gate'
  );
  await expect(page.getByTestId('finding-row-102')).toContainText('High');
  await expect(page.getByTestId('finding-row-102')).toContainText(
    'Approval overdue'
  );
});

test('finding drawer shows detail and issues acknowledge command', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'command-center' });
  await mockRiskRadarApi(page);

  let ackBody: any = null;
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/101/acknowledge/',
    async (route) => {
      ackBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          finding_id: 101,
          state: 'acknowledged',
          version: 4,
          owner_id: null,
          event_id: 9,
          event_type: 'acknowledged',
          replayed: false
        })
      });
    }
  );

  await page.reload();

  // Open the drawer from the queue row
  await page.getByTestId('finding-row-101').click();

  // Drawer shows rule code + version and the evidence keys
  await expect(page.getByTestId('finding-rule-code')).toHaveText(
    'RR-RTS-BLOCKED'
  );
  await expect(page.getByText('Version 3')).toBeVisible();
  await expect(page.getByTestId('finding-evidence')).toContainText('packet');
  await expect(page.getByTestId('finding-evidence')).toContainText('gate');

  // Acknowledge requires an explicit confirmation
  await page.getByTestId('finding-acknowledge').click();
  await page.getByTestId('finding-acknowledge-confirm').click();

  await expect.poll(() => ackBody != null).toBe(true);
  expect(ackBody.expected_version).toBe(3);
  expect(typeof ackBody.idempotency_key).toBe('string');
  expect(ackBody.idempotency_key.length).toBeGreaterThan(0);
});

test('finding drawer suppresses ungoverned action routes', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'command-center' });
  await mockRiskRadarApi(page);
  await page.route(
    (url) => url.pathname === '/api/repair/risk-findings/101/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...makeFinding(101, 'critical', 'Packet blocked at RTS gate', {
            action_links: [
              {
                label: 'Open packet',
                target_kind: 'repair_packet',
                target_id: 11,
                route: '/repair/packets/11/'
              },
              {
                label: 'External admin',
                target_kind: 'repair_packet',
                target_id: 11,
                route: 'https://example.invalid/admin'
              }
            ]
          }),
          evidence: { packet: 'RP-0001', gate: 'RTS' },
          events: []
        })
      });
    }
  );
  await page.reload();
  await page.getByTestId('finding-row-101').click();

  await expect(page.getByRole('button', { name: 'Open packet' })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'External admin' })
  ).toHaveCount(0);
  await expect(page.getByText('Source as of')).toBeVisible();
});

test('scope change closes a drawer from the previous scope', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'command-center' });
  await mockRiskRadarApi(page);
  await page.route(
    (url) => url.pathname === '/api/repair/risk-scopes/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scopes: ['c1', 'c2'],
          authorization_fingerprint: 'auth-two-scopes'
        })
      });
    }
  );
  await page.reload();
  await page.getByTestId('finding-row-101').click();
  await expect(page.getByTestId('finding-rule-code')).toBeVisible();

  await page.evaluate(() => {
    localStorage.setItem('risk-radar-scope', JSON.stringify('c2'));
    window.dispatchEvent(
      new CustomEvent('mantine-local-storage', {
        detail: { key: 'risk-radar-scope', value: 'c2' }
      })
    );
  });

  await expect(page.getByTestId('finding-rule-code')).toHaveCount(0);
});

test('malformed summary is unavailable instead of false-zero', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'command-center' });
  await mockRiskRadarApi(page);
  await page.route(
    (url) => url.pathname === '/api/repair/command-center/summary/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ scope: 'c1' })
      });
    }
  );
  await page.reload();

  await expect(page.getByText('Summary unavailable')).toBeVisible();
  await expect(page.getByTestId('headline-critical')).toHaveCount(0);
});

test('return-to-service finding is keyboard operable', async ({ browser }) => {
  const page = await doCachedLogin(browser, { url: 'command-center' });
  await mockRiskRadarApi(page);
  await page.route(
    (url) => url.pathname === '/api/repair/command-center/summary/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...SUMMARY,
          return_to_service: [
            {
              finding_id: 101,
              packet: 'RP-0001',
              code: 'WO_BLOCKED_SAFETY',
              reason_snapshot: 'Return to service blocked',
              source_as_of: AS_OF
            }
          ]
        })
      });
    }
  );
  await page.reload();

  await page.getByTestId('rts-finding-101').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('finding-rule-code')).toBeVisible();
});
