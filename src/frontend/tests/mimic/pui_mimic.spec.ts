import { expect, test } from '@playwright/test';

function fixture(unit: string | null, stale = false, enabled = true) {
  const point = (pointer: string, value: number | string | null) => ({
    pointer,
    label: pointer,
    group: 'Motor',
    value: enabled && !stale ? value : null,
    unit: typeof value === 'number' ? 'MW' : '',
    quality: 'good',
    observed_at: '2026-09-13T12:00:00Z',
    age_seconds: stale ? 500 : 2,
    reason: !enabled ? 'disabled' : stale ? 'stale' : null,
    condition: 'unknown',
    thresholds_configured: false
  });
  return {
    station: 17,
    name: 'Fixture station',
    generated_at: '2026-09-13T12:00:02Z',
    enabled,
    source: { pk: 1, name: 'Fixture source' },
    last_poll_at: null,
    last_error_code: '',
    layout: {
      version: 1,
      review_status: 'provisional',
      elements: [
        {
          id: 'pump-power',
          pointer: '/pd/{pump}/pmw',
          view: 'unit',
          role: 'value',
          label: 'Power',
          x: 370,
          y: 120
        }
      ]
    },
    station_points: {},
    bays: ['P1', 'P17'].map((key) => ({
      key,
      name: key,
      active: true,
      state: stale ? 'stale' : 'running',
      points: {}
    })),
    selected_unit: unit,
    points: unit ? { [`/pd/${unit}/pmw`]: point(`/pd/${unit}/pmw`, 7.5) } : {},
    totals: {
      power: {
        value: null,
        unit: 'MW',
        derived: true,
        reason: 'incomplete',
        contributors: []
      }
    },
    alarms: [],
    unconfigured_thresholds: 1
  };
}

test('sparse bays bind the selected pointer and never invent totals or thresholds', async ({
  page
}) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: fixture(new URL(route.request().url()).searchParams.get('unit'))
    })
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'P17: Running', exact: true }).click();
  await expect(page.locator('g[data-point="/pd/P17/pmw"]')).toContainText(
    '7.5 MW'
  );
  await expect(
    page.getByText('No threshold configured', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Missing contributors', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'P2: Running', exact: true })
  ).toHaveCount(0);
});

test('stale and disabled readings remain explicitly unavailable', async ({
  page
}) => {
  let enabled = true;
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: fixture(
        new URL(route.request().url()).searchParams.get('unit'),
        true,
        enabled
      )
    })
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'P17: Stale', exact: true }).click();
  await expect(page.locator('g[data-point="/pd/P17/pmw"]')).toContainText(
    'Unavailable'
  );
  await expect(page.getByText('7.5 MW', { exact: true })).toHaveCount(0);
  enabled = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByText(
      'Polling is disabled for this station. Live values are unavailable.',
      { exact: true }
    )
  ).toBeVisible();
});

test('request failure hides cached readings and hidden views stop polling', async ({
  page
}) => {
  let failed = false;
  let requests = 0;
  await page.route('**/api/machine-health/station/17/mimic/**', (route) => {
    requests++;
    return failed
      ? route.fulfill({ status: 503, json: {} })
      : route.fulfill({
          json: fixture(new URL(route.request().url()).searchParams.get('unit'))
        });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'P17: Running', exact: true }).click();
  await expect(page.locator('g[data-point="/pd/P17/pmw"]')).toContainText(
    '7.5 MW'
  );
  failed = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByText(
      'Live readings are unavailable. Refresh to retry; cached values are hidden.',
      { exact: true }
    )
  ).toBeVisible();
  await expect(page.locator('g[data-point="/pd/P17/pmw"]')).toHaveCount(0);
  await page.locator('#root').evaluate((node) => {
    node.style.display = 'none';
  });
  await page.waitForTimeout(250);
  const before = requests;
  await page.waitForTimeout(5500);
  expect(requests).toBe(before);
});
