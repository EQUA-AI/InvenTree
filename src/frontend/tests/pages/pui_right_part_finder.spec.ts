import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures.js';
import { navigate } from '../helpers.js';
import { doCachedLogin } from '../login.js';

/**
 * Browser coverage for the Right-Part Finder (part verification) pages.
 *
 * The backend feature flag AIMMS_RPF_ENABLED is off in the test server, so
 * every /api/part/verification/ endpoint returns 404 there. All RPF API
 * traffic is therefore mocked with page.route, using payloads that match the
 * implemented serializer fields. These tests prove the frontend wiring:
 * navigation, table rendering, panel content and text-based (not color-only)
 * status indicators.
 */

const FAR_FUTURE = '2030-01-01T00:00:00Z';

const STALE_REASON =
  'Source drift: machine nameplate model changed after confirmation';

// Session 1: review_required, with eligible + excluded candidates
const SESSION_REVIEW = {
  pk: 1,
  reference: 'PVS-000001',
  purpose: 'installed_replacement',
  state: 'review_required',
  revision: 1,
  policy_key: 'rpf-core',
  policy_version: 1,
  requested_part_name: 'Motor 5HP TEFC',
  eligible_count: 2,
  considered_count: 3,
  universe_complete: true,
  stale_reason: '',
  expires_at: FAR_FUTURE,
  updated_at: '2026-07-18T09:30:00Z'
};

// Session 2: was confirmed, then went stale after source drift
const SESSION_STALE = {
  pk: 2,
  reference: 'PVS-000002',
  purpose: 'job_kit_substitution',
  state: 'stale',
  revision: 2,
  policy_key: 'rpf-core',
  policy_version: 1,
  requested_part_name: 'Bearing 6205-2RS',
  eligible_count: 1,
  considered_count: 2,
  universe_complete: true,
  stale_reason: STALE_REASON,
  expires_at: FAR_FUTURE,
  updated_at: '2026-07-18T08:00:00Z'
};

const REQUIREMENTS: Record<number, any[]> = {
  1: [
    {
      pk: 101,
      key: 'electrical.voltage',
      operator: 'range_within',
      value: { min: '440', max: '480' },
      unit: 'V',
      hard_constraint: true,
      resolution: 'accepted',
      blocker_code: ''
    },
    {
      pk: 102,
      key: 'electrical.phase',
      operator: 'eq',
      value: '3',
      unit: '',
      hard_constraint: true,
      resolution: 'accepted',
      blocker_code: ''
    }
  ],
  2: [
    {
      // Missing hard fact: nameplate voltage must be re-collected
      pk: 201,
      key: 'electrical.voltage',
      operator: 'range_within',
      value: null,
      unit: 'V',
      hard_constraint: true,
      resolution: 'missing',
      blocker_code: 'NAMEPLATE_REQUIRED'
    },
    {
      pk: 202,
      key: 'mechanical.seal_type',
      operator: 'eq',
      value: '2RS',
      unit: '',
      hard_constraint: true,
      resolution: 'accepted',
      blocker_code: ''
    }
  ]
};

// Survivors first (ordered by rank), then exclusions
const CANDIDATES: Record<number, any[]> = {
  1: [
    {
      pk: 11,
      candidate_name: 'Motor 5HP TEFC',
      candidate_ipn: 'MTR-001',
      eligible: true,
      rank: 1,
      rank_value: '78.500000',
      retrieval_tiers: ['requested'],
      hard_conflicts: [],
      missing_attributes: []
    },
    {
      pk: 12,
      candidate_name: 'Motor 5HP TEFC Gen2',
      candidate_ipn: 'MTR-002',
      eligible: true,
      rank: 2,
      rank_value: '64.250000',
      retrieval_tiers: ['related'],
      hard_conflicts: [],
      missing_attributes: []
    },
    {
      pk: 13,
      candidate_name: 'Motor 5HP ODP',
      candidate_ipn: 'MTR-003',
      eligible: false,
      rank: null,
      rank_value: null,
      retrieval_tiers: ['related'],
      hard_conflicts: [
        { attribute: 'electrical.phase', reason_code: 'PHASE_CONFLICT' }
      ],
      missing_attributes: []
    }
  ],
  2: [
    {
      pk: 14,
      candidate_name: 'Bearing 6205-2RS',
      candidate_ipn: 'BRG-001',
      eligible: true,
      rank: 1,
      rank_value: '81.000000',
      retrieval_tiers: ['requested'],
      hard_conflicts: [],
      missing_attributes: []
    },
    {
      pk: 15,
      candidate_name: 'Bearing 6205-ZZ',
      candidate_ipn: 'BRG-002',
      eligible: false,
      rank: null,
      rank_value: null,
      retrieval_tiers: ['ipn'],
      hard_conflicts: [],
      missing_attributes: [
        {
          attribute: 'mechanical.seal_type',
          reason_code: 'CANDIDATE_ATTRIBUTE_MISSING'
        }
      ]
    }
  ]
};

const DECISIONS: Record<number, any[]> = {
  1: [],
  2: [
    {
      pk: 31,
      kind: 'confirmed',
      decided_at: '2026-07-17T15:00:00Z',
      valid_until: '2026-07-18T15:00:00Z',
      reason: 'Nameplate match verified on site',
      selected_part: 87
    }
  ]
};

// Not fetched by the current pages, but mocked for completeness so any
// readiness probe never hits the (disabled) real backend.
const READINESS: Record<number, any> = {
  1: { session: 1, state: 'review_required', revision: 1, usable: false },
  2: {
    session: 2,
    state: 'stale',
    revision: 2,
    usable: false,
    blockers: [{ code: 'RPF_SESSION_STALE', message: STALE_REASON }]
  }
};

/**
 * Install mocks for all Right-Part Finder API routes.
 */
async function mockRpfApi(
  page: Page,
  failedChild?: 'requirements' | 'candidates' | 'decisions'
) {
  await page.route(
    (url) => url.pathname.startsWith('/api/part/verification/'),
    async (route) => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      const json = async (body: unknown, status = 200) => {
        await route.fulfill({
          status: status,
          contentType: 'application/json',
          body: JSON.stringify(body)
        });
      };

      // InvenTreeTable probes OPTIONS for column metadata
      if (request.method() === 'OPTIONS') {
        await json({ actions: { GET: {} } });
        return;
      }

      if (pathname === '/api/part/verification/sessions/') {
        await json({ count: 2, results: [SESSION_REVIEW, SESSION_STALE] });
        return;
      }

      const match = pathname.match(
        /^\/api\/part\/verification\/sessions\/(\d+)\/(?:(requirements|candidates|decisions|readiness)\/)?$/
      );

      if (match) {
        const pk = Number.parseInt(match[1], 10);
        const child = match[2];
        const session = [SESSION_REVIEW, SESSION_STALE].find(
          (record) => record.pk === pk
        );

        if (session) {
          switch (child) {
            case undefined:
              await json(session);
              return;
            case 'requirements':
              if (failedChild === child) {
                await json({ detail: 'Service unavailable.' }, 503);
                return;
              }
              await json(REQUIREMENTS[pk] ?? []);
              return;
            case 'candidates':
              if (failedChild === child) {
                await json({ detail: 'Service unavailable.' }, 503);
                return;
              }
              await json(CANDIDATES[pk] ?? []);
              return;
            case 'decisions':
              if (failedChild === child) {
                await json({ detail: 'Service unavailable.' }, 503);
                return;
              }
              await json(DECISIONS[pk] ?? []);
              return;
            case 'readiness':
              await json(READINESS[pk] ?? {});
              return;
          }
        }
      }

      await json({ code: 'RPF_NOT_FOUND', detail: 'Not found.' }, 404);
    }
  );
}

test('index page lists verification sessions', async ({ browser }) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockRpfApi(page);

  // The main navigation exposes the Right-Part Finder tab
  await expect(
    page.getByRole('tab', { name: 'Right-Part Finder' })
  ).toBeVisible();

  await navigate(page, 'part-verification/index/');

  // Both session references render in the sessions table
  await expect(page.getByRole('cell', { name: 'PVS-000001' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'PVS-000002' })).toBeVisible();

  // States render as text labels, never as raw codes or color alone
  await expect(
    page.getByRole('cell', { name: 'Review Required', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('cell', { name: 'Stale', exact: true })
  ).toBeVisible();
});

test('detail overview shows state text and stale indicator', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockRpfApi(page);
  await navigate(page, 'part-verification/index/');

  // Clicking the stale session row navigates to its detail page
  await page.getByRole('cell', { name: 'PVS-000002' }).click();
  await page.waitForURL(/part-verification\/2/);

  // Overview panel shows the state as text
  await expect(page.getByText('PVS-000002').first()).toBeVisible();
  await expect(page.getByText('Stale', { exact: true }).first()).toBeVisible();

  // The stale indicator is asserted by its text content, not its color
  await expect(page.getByText('Not ready for use')).toBeVisible();
  await expect(
    page.getByText(
      'This session is stale and must be re-evaluated before its result is used.'
    )
  ).toBeVisible();
  await expect(page.getByText(STALE_REASON).first()).toBeVisible();
});

test('candidates panel shows eligibility text and no confirm controls', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockRpfApi(page);
  await navigate(page, 'part-verification/1/candidates');

  // Eligible candidate row: ELIGIBLE text label plus its rank
  const eligibleRow = page.getByRole('row').filter({ hasText: 'MTR-001' });
  await expect(
    eligibleRow.getByText('Eligible', { exact: true })
  ).toBeVisible();
  await expect(
    eligibleRow.getByRole('cell', { name: '1', exact: true })
  ).toBeVisible();

  // Excluded candidate row: EXCLUDED text label plus the conflict code
  const excludedRow = page.getByRole('row').filter({ hasText: 'MTR-003' });
  await expect(
    excludedRow.getByText('Excluded', { exact: true })
  ).toBeVisible();
  await expect(excludedRow.getByText('PHASE_CONFLICT')).toBeVisible();

  // The read-only slice must not offer any confirm affordance - neither on
  // the excluded row nor anywhere else on the page
  await expect(
    excludedRow.getByRole('button', { name: /confirm/i })
  ).toHaveCount(0);
  await expect(page.getByRole('button', { name: /confirm/i })).toHaveCount(0);
});

test('requirements panel shows requirement key and blocker code', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockRpfApi(page);
  await navigate(page, 'part-verification/2/requirements');

  // The missing hard fact renders its key, constraint kind, resolution and
  // stable blocker code as text
  const row = page.getByRole('row').filter({ hasText: 'electrical.voltage' });
  await expect(row.getByText('NAMEPLATE_REQUIRED')).toBeVisible();
  await expect(row.getByText('Hard', { exact: true })).toBeVisible();
  await expect(row.getByText('missing', { exact: true })).toBeVisible();

  // The accepted requirement carries no blocker code
  const acceptedRow = page
    .getByRole('row')
    .filter({ hasText: 'mechanical.seal_type' });
  await expect(acceptedRow.getByText('NAMEPLATE_REQUIRED')).toHaveCount(0);
});

test('failed child query is distinct from an empty result', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockRpfApi(page, 'requirements');
  await navigate(page, 'part-verification/1/requirements');

  await expect(page.getByText('Unable to load records')).toBeVisible({
    timeout: 15000
  });
  await expect(page.getByText('No records found')).toHaveCount(0);
});

test('keyboard path reaches the candidates table region', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'home' });
  await mockRpfApi(page);
  await navigate(page, 'part-verification/1/');

  // Wait for the detail page (index route redirects to the overview panel).
  // First load may lazily compile the page chunk, so allow extra time.
  await expect(page.getByText('PVS-000001').first()).toBeVisible({
    timeout: 15000
  });
  await expect(page.getByRole('tab', { name: 'Overview' })).toBeVisible();

  // Report the focused panel tab (if any): the roving tabindex of the panel
  // tablist is the keyboard entry point for the candidates region
  const focusedPanelTab = async (): Promise<string | null> => {
    return await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el.getAttribute('role') !== 'tab') {
        return null;
      }
      const list = el.closest('[role="tablist"]');
      if (
        list?.getAttribute('aria-label') !==
        'panel-tabs-part-verification-detail'
      ) {
        return null;
      }
      return el.textContent ?? '';
    });
  };

  // Tab from the top of the document until the panel tablist takes focus
  let focused: string | null = null;
  for (let i = 0; i < 60 && focused == null; i++) {
    await page.keyboard.press('Tab');
    focused = await focusedPanelTab();
  }
  expect(focused).not.toBeNull();

  // The panel tablist is vertical: arrow down to the Candidates tab, which
  // activates it and reveals the candidates table region
  for (let i = 0; i < 8 && !(focused ?? '').includes('Candidates'); i++) {
    await page.keyboard.press('ArrowDown');
    await page.waitForTimeout(100);
    focused = await focusedPanelTab();
  }
  expect(focused).toContain('Candidates');

  // Focus controls the (now visible) candidates region, which holds the
  // table. The tabpanel takes its accessible name from the Candidates tab
  // (aria-labelledby wins over the custom aria-label).
  const panel = page.getByRole('tabpanel', { name: 'Candidates' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('table')).toBeVisible();
  await expect(panel.getByText('MTR-001')).toBeVisible();
});
