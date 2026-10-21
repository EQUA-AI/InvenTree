import { type Locator, type Page, expect } from '@playwright/test';
import type {
  InvestigationRequest,
  TimeAssignment
} from '../../lib/types/WorkExecution';

import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';

import { workOrder } from '../s0/workFixtures';
import { mockWorkList } from '../s1/workListFixtures';

export const recordedAt = '2026-10-18T12:00:00Z';

export interface ApiRequest {
  method: string;
  path: string;
}

/**
 * The real Work page in the production layout, served one synthetic overview
 * for Work 73. Every request is recorded so a spec can prove that opening a
 * panel reads and never writes.
 */
export async function mockExecutionWork(
  page: Page,
  overview: WorkOrderOverview = workOrder()
): Promise<ApiRequest[]> {
  await mockWorkList(page);
  const requests: ApiRequest[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/'))
      requests.push({ method: request.method(), path });
  });
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ json: overview })
  );
  return requests;
}

export async function openExecutionPanel(
  page: Page,
  panel = 'work',
  scheme: 'light' | 'dark' = 'light',
  partReader = false,
  role: 'viewer' | 'editor' | 'supervisor' = 'viewer'
) {
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    role,
    work_access: 'reader',
    scheme,
    entry: `/maintenance/work-orders/73/?work_panel=${panel}`
  });
  if (partReader) params.set('part_access', 'reader');
  await page.goto(`/playwright/s0-native.html?${params}`);
  await expect(page.getByRole('heading', { name: /^WO-73: / })).toBeVisible();
}

export function panelRegion(page: Page, name: string) {
  return page.getByRole('tabpanel', { name, exact: true });
}

/** Scroll the workflow tabs to the top and capture the visible workspace. */
export async function capturePanel(page: Page, path: string, fullPage = true) {
  if (fullPage) {
    // The production header is sticky; a full-page capture starts at the top.
    await page.evaluate(() => window.scrollTo(0, 0));
  } else {
    await page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .evaluate((element) => element.scrollIntoView({ block: 'start' }));
    await page.evaluate(() => window.scrollBy(0, -80));
  }
  await page.screenshot({ path, animations: 'disabled', fullPage });
}

export async function expectNoPageOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
}

export function onlyReads(requests: ApiRequest[]) {
  return requests.every((request) =>
    ['GET', 'OPTIONS'].includes(request.method)
  );
}

export { workOrder };

/**
 * What the production assistant drawer reads once it opens, so "Continue
 * investigation" can be followed into the one existing assistant.
 */
export async function mockAssistantOpen(page: Page) {
  await page.route('**/api/approvals/count/**', (route) =>
    route.fulfill({ json: { count: 0 } })
  );
  await page.route('**/api/aichat/proposals/**', (route) =>
    route.fulfill({ json: { results: [] } })
  );
  // A UI library stylesheet names a web font on its CDN; nothing external
  // is fetched in this harness.
  await page.route('https://cdn.svar.dev/**', (route) =>
    route.fulfill({ status: 404, body: '' })
  );
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({
      json: {
        threads: [],
        shared_threads: [],
        has_more: false,
        capabilities: { thread_scope: true, work_focus: true }
      }
    })
  );
}

export const WORK_LABEL = 'WO-73 · Synthetic job 73';

export function savedRequest(id = 401): InvestigationRequest {
  return {
    id,
    work_order: 73,
    schema_version: 1,
    version: 3,
    purpose: 'Synthetic decision about retained observation',
    next_check: 'Review the recorded measurement',
    priority: 1,
    required_artifacts: [],
    status: 'cannot_perform',
    owner: null,
    owner_name: null,
    next_checkpoint: null,
    source_finding: null,
    source_proposal: null,
    blocked_reason: 'Accessible point not available in the recorded window',
    resolution: '',
    created_by: 9,
    updated_by: 9,
    created_at: '2026-10-20T08:00:00Z',
    updated_at: '2026-10-20T08:00:00Z',
    artifacts: []
  };
}

/** Preserve the native sticky header without covering the section in evidence. */
export async function captureProcessSection(
  page: Page,
  section: Locator,
  path: string
) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: true, animations: 'disabled' });
  await section.evaluate((element) =>
    element.scrollIntoView({ block: 'start' })
  );
  await page.evaluate(() => window.scrollBy(0, -96));
  await page.screenshot({
    path: path.replace('.png', '-viewport.png'),
    animations: 'disabled'
  });
}

/** Complete native projection; absence of records does not invent totals. */
export function timeAssignment(
  overrides: Partial<TimeAssignment> = {}
): TimeAssignment {
  return {
    rule: 'time_assignment.v1',
    observed_at: '2026-10-20T08:00:00Z',
    assigned_to: null,
    legacy_assignee: null,
    assignment_event_at: null,
    elapsed_assignment_seconds: null,
    assignment_reason: null,
    assignment_recorded_by: null,
    events: [],
    attributed_intervals: { available: true },
    coverage: 'unknown',
    correction: { available: true },
    assignment_factors: { available: false, reason: 'no_provider' },
    accepted_handover: { available: false, reason: 'no_provider' },
    ...overrides
  };
}

/** Open the native contextual disclosure before operating its actual controls. */
export async function revealWorkContext(
  page: Page,
  label:
    | 'Continuity and handover'
    | 'Assignment and person-work time'
    | 'Investigation and preparation summary'
) {
  // A disclosure that needs action appends its status to the control's
  // accessible name ("Continuity and handover Handover awaiting your
  // acceptance"), so match the title as the name's exact start.
  const control = panelRegion(page, 'Work').getByRole('button', {
    name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(\\s|$)`)
  });
  if ((await control.getAttribute('aria-expanded')) === 'false')
    await control.click();
}
