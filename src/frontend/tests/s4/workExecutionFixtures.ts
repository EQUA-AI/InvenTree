import { type Page, expect } from '@playwright/test';

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
  scheme: 'light' | 'dark' = 'light'
) {
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    role: 'viewer',
    work_access: 'reader',
    scheme,
    entry: `/maintenance/work-orders/73/?work_panel=${panel}`
  });
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
