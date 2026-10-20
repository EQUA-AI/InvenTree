import { type Page, expect, test } from '@playwright/test';

import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';

import { nativeBaseline } from '../s0/guards';
import { workOrder } from '../s0/workFixtures';
import { mockWorkList } from './workListFixtures';

nativeBaseline();

async function mockPanelWork(
  page: Page,
  overview: WorkOrderOverview = workOrder()
) {
  await mockWorkList(page);
  const requests: { method: string; path: string }[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/'))
      requests.push({ method: request.method(), path });
  });
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ json: overview })
  );
  await page.route('**/api/tasks/work-orders/73/**', (route) =>
    // R6 P22: no material readout here; Parts shows the native table.
    route.request().url().includes('/materials/')
      ? route.fulfill({ status: 404, json: { detail: 'Not found.' } })
      : route.fulfill({
          json: route.request().url().includes('/readiness/')
            ? {
                ready: false,
                blockers: [
                  {
                    code: 'SYNTHETIC_BLOCKER',
                    message: 'Synthetic missing verification',
                    blocking: true
                  }
                ],
                warnings: [],
                evaluated_at: '2026-10-18T12:00:00Z',
                lifecycle_version: 7,
                policy_version: 1,
                snapshot_hash: 'a'.repeat(64)
              }
            : []
        })
  );
  return requests;
}

async function openWork(
  page: Page,
  panel = 'work',
  scheme = 'light',
  id = '73'
) {
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    role: 'viewer',
    work_access: 'reader',
    scheme,
    entry: `/maintenance/work-orders/${id}/?work_panel=${panel}`
  });
  await page.goto(`/playwright/s0-native.html?${params}`);
}

function packetWork(): WorkOrderOverview {
  return {
    ...workOrder(),
    lifecycle_status: 'completed',
    structured_closeout: {
      id: 301,
      cause: 'Recorded cause remains qualified',
      action: 'Recorded inspection',
      result: 'Recorded result',
      verification_summary: 'Retained verification account only',
      downtime_minutes: null,
      follow_up_required: true,
      follow_up: 'Review independent operating restriction',
      completed_at: '2026-10-18T12:00:00Z',
      verified_at: null,
      amended: false,
      amendment_count: 0
    },
    repair_packet: {
      id: 901,
      reference: 'RP-901',
      status: 'closed',
      criticality: 'high',
      fault_summary: 'Synthetic retained fault',
      symptom: 'Synthetic intermittent vibration',
      production_impact: 'Not verified as a service outage',
      generation_status: 'complete',
      diagnosis: { likely_cause: 'Synthetic preliminary bearing concern' },
      diagnosis_is_preliminary: true,
      diagnosis_status: 'available',
      findings: [
        {
          id: 101,
          finding_key: 'SYN-01',
          category: 'observation',
          observation: 'Retained zero measurement',
          value: 0,
          unit: 'mm/s',
          evidence_source: 'Synthetic immutable snapshot',
          snapshot_id: '12345678-synthetic',
          observed_at: '2026-10-18T11:00:00Z',
          verification: 'verified'
        }
      ],
      approved_scope: {
        id: 201,
        version: 3,
        verified_cause: 'Approved recorded scope basis',
        scope_lines: [
          { sequence: 1, action: 'Inspect the recorded bearing position' }
        ],
        failure_codes: ['SYN-CODE'],
        crew_size: 2,
        planned_elapsed_minutes: 60,
        approved_at: '2026-10-18T11:30:00Z',
        approval_note:
          'Synthetic approved plan, not proof of physical execution'
      },
      gates: [
        {
          id: 401,
          name: 'Synthetic independent lockout',
          gate_type: 'loto',
          status: 'pending',
          is_blocking: true,
          is_mandatory: true,
          requires_photo: true,
          requires_second_person: true
        }
      ]
    },
    events: [
      {
        id: 501,
        event_type: 'updated',
        from_status: 'verifying',
        to_status: 'completed',
        reason: 'Retained audit reason',
        created_at: '2026-10-18T12:00:00Z'
      }
    ]
  };
}

test('the owning work exposes seven addressable workflow panels and keeps Attachments a resource', async ({
  page
}) => {
  await mockWorkList(page);
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    role: 'viewer',
    work_access: 'reader',
    entry: '/maintenance/work-orders/73/'
  });
  await page.goto(`/playwright/s0-native.html?${params}`);
  await expect(
    page.getByRole('tablist', { name: 'Work workflow', exact: true })
  ).toBeVisible();
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await expect(tabs.getByRole('tab')).toHaveCount(7);
  for (const name of [
    'Work',
    'Diagnose',
    'Plan & Safety',
    'Parts',
    'Verify & Return',
    'Closeout',
    'History'
  ]) {
    await expect(tabs.getByRole('tab', { name, exact: true })).toBeVisible();
  }
  await expect(
    tabs.getByRole('tab', { name: 'Attachments', exact: true })
  ).toHaveCount(0);
});

test('native record fields remain reachable and each native section has a workflow home', async ({
  page
}) => {
  const requests = await mockPanelWork(page);
  await openWork(page);
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await page
    .getByRole('button', { name: 'Record details', exact: true })
    .click();
  for (const label of [
    'Board stage',
    'Type',
    'Assignee',
    'Estimated effort',
    'Scheduled start',
    'Scheduled end',
    'Due date',
    'Actual start',
    'Actual completion',
    'Reference',
    'Created',
    'Last updated'
  ])
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  for (const [panel, heading] of [
    ['Work', 'Board cards'],
    ['Plan & Safety', 'Dependencies'],
    ['Parts', 'Parts for this job'],
    ['History', 'Activity']
  ]) {
    await tabs.getByRole('tab', { name: panel, exact: true }).click();
    await expect(
      page.getByRole('heading', { name: heading, exact: true })
    ).toBeVisible();
  }
  await tabs.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(
    page.getByText('Standalone completion is not enabled for this work order.')
  ).toBeVisible();
  // Only the Parts panel's own read-only material readout, once.
  expect(
    requests.filter((request) =>
      request.path.includes('/api/tasks/work-orders/')
    )
  ).toEqual([{ method: 'GET', path: '/api/tasks/work-orders/73/materials/' }]);
  expect(
    requests.every((request) => ['GET', 'OPTIONS'].includes(request.method))
  ).toBe(true);
});

test('native standalone closeout mounts only on first visit and retains its local draft across views', async ({
  page
}, info) => {
  const requests = await mockPanelWork(page, {
    ...workOrder(),
    canonical_commands_enabled: true
  });
  await openWork(page);
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  expect(
    requests.filter((request) =>
      request.path.includes('/api/tasks/work-orders/')
    )
  ).toHaveLength(0);
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await tabs.getByRole('tab', { name: 'Closeout', exact: true }).click();
  const capture = page.getByTestId('closeout-capture-disclosure');
  await expect(capture).not.toHaveAttribute('open');
  await capture
    .getByText('Edit or add to the capture', { exact: true })
    .click();
  await expect(capture).toHaveAttribute('open');
  const narrative = page.getByRole('textbox', {
    name: 'What did you do?',
    exact: true
  });
  await narrative.fill(
    'Synthetic unsaved local draft — preserve on panel navigation'
  );
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  expect(
    requests.filter((request) =>
      request.path.includes('/api/tasks/work-orders/')
    )
  ).toHaveLength(4);
  await tabs.getByRole('tab', { name: 'History', exact: true }).click();
  await expect(narrative).toBeHidden();
  await tabs.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(capture).toHaveAttribute('open');
  await expect(narrative).toBeVisible();
  await expect(narrative).toHaveValue(
    'Synthetic unsaved local draft — preserve on panel navigation'
  );
  expect(
    requests.filter((request) =>
      request.path.includes('/api/tasks/work-orders/')
    )
  ).toHaveLength(4);
  expect(
    requests.every((request) => ['GET', 'OPTIONS'].includes(request.method))
  ).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('retained-capture-draft.png'),
    fullPage: true
  });
});

test('direct-linked Parts and native browser history select views without changing work identity', async ({
  page
}) => {
  const requests = await mockPanelWork(page);
  await openWork(page, 'parts');
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await expect(
    tabs.getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.getByRole('heading', { name: 'Parts for this job', exact: true })
  ).toBeVisible();
  await tabs.getByRole('tab', { name: 'History', exact: true }).click();
  await page.getByRole('button', { name: 'Fixture back', exact: true }).click();
  await expect(
    tabs.getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.getByRole('heading', { name: 'WO-73: Synthetic job 73', exact: true })
  ).toBeVisible();
  expect(
    requests.filter((request) => request.path.endsWith('/73/overview/'))
  ).toHaveLength(1);
});

test('packet work keeps qualified evidence safety and completion authority without claiming service return', async ({
  page
}, testInfo) => {
  const requests = await mockPanelWork(page, {
    ...packetWork(),
    canonical_commands_enabled: true
  });
  await openWork(page, 'diagnose', 'dark');
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await expect(page.getByText('0 mm/s', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Synthetic immutable snapshot', { exact: true })
  ).toBeVisible();
  await tabs.getByRole('tab', { name: 'Plan & Safety', exact: true }).click();
  const plan = page.getByRole('tabpanel', {
    name: 'Plan & Safety',
    exact: true
  });
  await expect(plan.getByText('Version 3', { exact: true })).toBeVisible();
  await expect(
    plan.getByText('Synthetic independent lockout', { exact: true }).first()
  ).toBeVisible();
  await expect(
    plan.getByText('Preliminary results', { exact: true })
  ).toBeVisible();
  const safetyWarning = plan.getByRole('alert').filter({
    hasText:
      'This repair cannot start until these gates are satisfied through the safety flow.'
  });
  for (const summary of [
    safetyWarning,
    plan.getByText('1 blocking gate(s)', { exact: true })
  ]) {
    await expect(summary).toBeVisible();
    expect(
      await summary.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.left >= 0 && bounds.right <= window.innerWidth;
      })
    ).toBe(true);
  }
  await tabs.evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -80));
  await page.screenshot({
    path: testInfo.outputPath('work-plan-safety.png'),
    animations: 'disabled'
  });
  await tabs.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  await expect(
    page.getByText('Retained verification account only', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText(/does not establish permission to operate/)
  ).toBeVisible();
  await tabs.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(
    page.getByRole('link', { name: 'Open RP-901', exact: true })
  ).toHaveAttribute('href', '/repair/packets/901/closeout/');
  await expect(page.getByText('Narrative', { exact: true })).toHaveCount(0);
  expect(
    requests.filter((request) =>
      request.path.includes('/api/tasks/work-orders/')
    )
  ).toHaveLength(0);
  await tabs.getByRole('tab', { name: 'History', exact: true }).click();
  await expect(
    page.getByText('Retained audit reason', { exact: true })
  ).toBeVisible();
  await tabs.evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -80));
  await page.screenshot({
    path: testInfo.outputPath('work-panels-packet-dark.png'),
    animations: 'disabled'
  });
  expect(
    requests.every((request) => ['GET', 'OPTIONS'].includes(request.method))
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
});

test('keyboard navigation and long identity stay within the phone workspace', async ({
  page
}, testInfo) => {
  await mockPanelWork(page, {
    ...workOrder(),
    title: `Synthetic long work ${'REFERENCE-WITHOUT-SPACES-'.repeat(6)}`
  });
  await openWork(page);
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await tabs.getByRole('tab', { name: 'Work', exact: true }).focus();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await expect(
    tabs.getByRole('tab', { name: 'History', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.getByRole('heading', { name: 'Activity', exact: true })
  ).toBeVisible();
  await tabs.evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -80));
  await page.screenshot({
    path: testInfo.outputPath('work-panels-long-identity.png'),
    animations: 'disabled'
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
});

test('an unknown panel has explicit recovery rather than a lifecycle or completion side effect', async ({
  page
}) => {
  const requests = await mockPanelWork(page, {
    ...workOrder(),
    canonical_commands_enabled: true
  });
  await openWork(page, 'complete');
  await expect(
    page.getByRole('alert').filter({ hasText: 'Work panel unavailable' })
  ).toBeVisible();
  expect(
    requests.filter((request) =>
      request.path.includes('/api/tasks/work-orders/')
    )
  ).toHaveLength(0);
  await page.getByRole('link', { name: 'Return to Work', exact: true }).click();
  await expect(
    page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .getByRole('tab', { name: 'Work', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  expect(
    requests.every((request) => ['GET', 'OPTIONS'].includes(request.method))
  ).toBe(true);
});
