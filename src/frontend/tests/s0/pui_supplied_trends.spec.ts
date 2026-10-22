import { type Page, expect, test } from '@playwright/test';

import type { SignalTrend } from '../../lib/types/MachineHealth';
import { nativeBaseline } from './guards';

nativeBaseline();

function trend(bindingId = 100): SignalTrend {
  return {
    binding_id: bindingId,
    available: true,
    display_name: 'Active Power',
    source_name: 'Synthetic historian',
    source_id: 1,
    source_type: 'historian',
    signal_kind: 'power',
    unit: 'MW',
    window_start: '2026-10-18T12:00:00Z',
    window_end: '2026-10-18T12:01:00Z',
    max_samples: 120,
    limits: { max_window_seconds: 21600, max_samples: 120 },
    samples: [
      { observed_at: '2026-10-18T12:00:00Z', value: 0, quality: 'good' },
      { observed_at: '2026-10-18T12:01:00Z', value: 1, quality: 'good' }
    ]
  };
}

async function rowRequests(page: Page) {
  const requests: string[] = [];
  await page.route(
    '**/api/machine-health/machines/18/health/trend/**',
    (route) => {
      requests.push(route.request().url());
      return route.fulfill({ json: trend() });
    }
  );
  return requests;
}

test('pending and partial batches never start per-row trend reads', async ({
  page
}, testInfo) => {
  const requests = await rowRequests(page);
  let releaseBatch!: () => void;
  const pendingBatch = new Promise<void>((resolve) => {
    releaseBatch = resolve;
  });
  await page.route(
    '**/api/machine-health/machines/18/health/trends/**',
    async (route) => {
      await pendingBatch;
      await route.fulfill({ json: { results: [trend()] } });
    }
  );
  await page.goto('/playwright/s0-native.html?surface=signals');
  await expect(page.getByRole('row')).toHaveCount(4);
  await expect(page.getByTestId('fixture-fetching')).toHaveText('1');
  expect(requests).toEqual([]);
  releaseBatch();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  if (testInfo.project.name.includes('phone')) {
    // Phone cards keep three facts; the Trend column is in the all-columns view.
    await page
      .getByRole('button', { name: 'Signal filters', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'All columns table', exact: true })
      .click();
  }
  await expect(
    page.getByRole('img', { name: 'Recent trend for Active Power' })
  ).toBeVisible();
  const missing = page.getByRole('button', { name: /^Not returned:/ });
  await expect(missing).toHaveCount(2);
  await expect(missing.first()).toHaveText('—');
  await missing.first().focus();
  await expect(page.getByRole('tooltip')).toContainText('completed trend read did not return this signal');
  await expect(
    page.getByRole('cell', { name: '0 MW', exact: true })
  ).toBeVisible();
  expect(requests).toEqual([]);
});

test('a failed batch keeps current readings without falling back to row reads', async ({
  page
}) => {
  const requests = await rowRequests(page);
  await page.route(
    '**/api/machine-health/machines/18/health/trends/**',
    (route) =>
      route.fulfill({
        status: 503,
        json: { detail: 'Synthetic source unavailable' }
      })
  );
  await page.goto('/playwright/s0-native.html?surface=signals');
  // A failed read says so; it is not reported as an absence of trend (P09).
  await expect(page.getByText('Read failed', { exact: true })).toHaveCount(3);
  await expect(page.getByText('No trend', { exact: true })).toHaveCount(0);
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await expect(
    page.getByRole('cell', { name: '0 MW', exact: true })
  ).toBeVisible();
  expect(requests).toEqual([]);
});

test('an independent sparkline still reads its own binding', async ({
  page
}) => {
  const requests = await rowRequests(page);
  await page.goto('/playwright/s0-native.html?surface=standalone-trend');
  await expect(
    page.getByRole('img', { name: 'Recent trend for Active Power' })
  ).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(new URL(requests[0]).searchParams.get('binding')).toBe('100');
});
