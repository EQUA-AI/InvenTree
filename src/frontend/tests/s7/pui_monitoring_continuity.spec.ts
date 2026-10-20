import { type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { openHealth } from '../s3/healthFixtures';
import {
  mockFaultHistory,
  occurrences,
  openFaultHistory
} from '../s3/machineWorkFixtures';
import {
  closeoutWork,
  containment,
  openCloseout,
  values
} from '../s6/closeoutFixtures';

nativeBaseline();

const summary = {
  configured: true,
  state: 'warning',
  signal_count: 2,
  stale_signal_count: 0,
  degraded_data: false,
  last_observed_at: '2026-10-20T08:10:00Z',
  active_anomaly_count: 1,
  anomaly_counts: { info: 0, warning: 1, critical: 0 },
  sources: [
    {
      source_id: 5,
      name: 'Synthetic SCADA',
      source_type: 'scada',
      active: true,
      healthy: true,
      last_success_at: '2026-10-20T08:10:00Z',
      last_error_at: null,
      last_error_code: '',
      freshness_threshold_seconds: 300,
      mapped_tag_count: 2
    }
  ]
};
const deviation = {
  ...occurrences[0],
  id: 903,
  external_id: 'NEW-903',
  first_observed_at: '2026-10-20T08:05:00Z',
  last_observed_at: '2026-10-20T08:10:00Z',
  work: [
    {
      ...occurrences[0].work[0],
      id: 99,
      reference: 'WO-99',
      title: 'Synthetic job 99'
    }
  ],
  snapshot_count: 1,
  snapshots: [
    {
      ...occurrences[1].snapshots[0],
      id: 'a3f1c2d4-0000-4000-8000-000000000003',
      signal_label: 'Discharge pressure',
      unit: 'bar',
      binding: 101,
      content_hash: 'd'.repeat(64),
      captured_at: '2026-10-20T08:06:00Z',
      window_start: '2026-10-20T08:05:00Z',
      window_end: '2026-10-20T08:06:00Z',
      quality: 'uncertain',
      stale: true
    }
  ]
};
async function monitoring(
  page: Page,
  options: {
    healthStatus?: number;
    occurrenceStatus?: number;
    unconfigured?: boolean;
    disabled?: boolean;
    empty?: boolean;
    longIdentity?: boolean;
  } = {}
) {
  const reads: URL[] = [];
  await page.route('**/api/machine-health/machines/18/health/', (route) => {
    reads.push(new URL(route.request().url()));
    return route.fulfill({
      status: options.healthStatus ?? 200,
      json: options.unconfigured
        ? {
            ...summary,
            configured: false,
            state: 'unknown',
            signal_count: 0,
            stale_signal_count: 0,
            active_anomaly_count: 0,
            anomaly_counts: { info: 0, warning: 0, critical: 0 },
            sources: [],
            last_observed_at: null
          }
        : options.disabled
          ? {
              ...summary,
              state: 'unknown',
              degraded_data: true,
              sources: [
                {
                  ...summary.sources[0],
                  active: false,
                  healthy: false,
                  last_success_at: null
                }
              ]
            }
          : summary
    });
  });
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/?*',
    (route) => {
      const url = new URL(route.request().url());
      reads.push(url);
      expect(Object.fromEntries(url.searchParams)).toEqual({
        status: 'active',
        limit: '25',
        offset: '0'
      });
      return route.fulfill({
        status: options.occurrenceStatus ?? 200,
        json: {
          rule: 'fault_occurrences.v1',
          count: options.empty ? 0 : 1,
          limit: 25,
          offset: 0,
          results: options.empty
            ? []
            : [
                options.longIdentity
                  ? {
                      ...deviation,
                      title: 'LongSourceTitle'.repeat(30),
                      external_id: 'LongSourceAlarm'.repeat(30)
                    }
                  : deviation
              ]
        }
      });
    }
  );
  return reads;
}
async function capture(page: Page, path: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: true });
  const current = page
    .getByTestId('current-monitoring')
    .filter({ visible: true });
  if (await current.count()) {
    // Keep the real native header above the facts in viewport-size evidence.
    await current.evaluate((element) =>
      element.scrollIntoView({ block: 'start' })
    );
    await page.evaluate(() => window.scrollBy(0, -220));
    await page.screenshot({
      path: path.replace('.png', '-configuration-detail.png')
    });
    await current
      .getByText('Recorded active deviations', { exact: true })
      .evaluate((element) => element.scrollIntoView({ block: 'start' }));
    await page.evaluate(() => window.scrollBy(0, -220));
    await page.screenshot({
      path: path.replace('.png', '-deviation-detail.png')
    });
    await page.evaluate(() => window.scrollTo(0, 0));
  }
}

const visibleContinuity = (page: Page) =>
  page.getByTestId('monitoring-continuity').filter({ visible: true });

for (const scheme of ['light', 'dark'] as const) {
  test(`current configured monitoring and sourced deviations survive maintenance completion, ${scheme}`, async ({
    page
  }, info) => {
    const { commands } = await closeoutWork(page, { completed: true });
    const reads = await monitoring(page);
    await openCloseout(page, scheme);
    expect(reads).toHaveLength(0);
    const disclosure = visibleContinuity(page);
    await disclosure.locator('summary').click();
    await expect(disclosure).toContainText('Current configured monitoring');
    await expect(disclosure).toContainText('before, during and after');
    await expect(disclosure).toContainText('NEW-903');
    await expect(disclosure).toContainText('20 Oct 2026,');
    await expect(disclosure).toContainText('Asia/Riyadh');
    await expect(disclosure).toContainText('Quality at capture: Uncertain');
    await expect(disclosure).toContainText('Stale at capture');
    await expect(disclosure).toContainText(
      'Relationship to prior Work unconfirmed'
    );
    await expect(
      disclosure.getByRole('link', {
        name: 'Open exact occurrence and retained evidence'
      })
    ).toHaveAttribute(
      'href',
      '/machines/machine/18/fault-history/?occurrence=903'
    );
    await expect(
      disclosure.getByRole('link', { name: /WO-99/ })
    ).toHaveAttribute(
      'href',
      '/maintenance/work-orders/99/?work_panel=diagnose'
    );
    await expect(page.getByTestId('closeout-account')).toContainText(
      'Corrected recorded result'
    );
    await page.getByText('Original completed account', { exact: true }).click();
    await expect(page.getByTestId('prepared-closeout')).not.toContainText(
      'Accepted by'
    );
    await expect(page.getByTestId('closeout-account')).toContainText(
      values.result
    );
    await expect(page.getByTestId('closeout-account')).toContainText(
      'Synthetic Supervisor'
    );
    expect(commands).toHaveLength(0);
    await containment(page);
    await capture(
      page,
      info.outputPath(`monitoring-after-closeout-${scheme}.png`)
    );
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await visibleContinuity(page).locator('summary').click();
    await expect(visibleContinuity(page)).toContainText(
      'Factual corrections use attributed amendments'
    );
    await expect(visibleContinuity(page)).toContainText(
      'reopened or linked Work flow'
    );
    expect(commands).toHaveLength(0);
  });
}

test('monitoring configuration is available during open work without a closure trigger', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  await monitoring(page);
  await openCloseout(page);
  await page.getByRole('tab', { name: 'Work', exact: true }).click();
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('Current configured monitoring');
  await expect(disclosure).toContainText('No watch period is inferred');
  expect(commands).toHaveLength(0);
  await capture(page, info.outputPath('monitoring-during-work.png'));
});

for (const status of [403, 500]) {
  test(`unavailable monitoring ${status} keeps deviations separate from repair and healthy verdicts`, async ({
    page
  }, info) => {
    await closeoutWork(page, { completed: true });
    await monitoring(page, { healthStatus: status, occurrenceStatus: status });
    await openCloseout(page, 'dark');
    const disclosure = visibleContinuity(page);
    await disclosure.locator('summary').click();
    await expect(disclosure).toContainText(
      status === 403
        ? 'Monitoring unavailable in the current scope'
        : 'Monitoring could not be read'
    );
    await expect(disclosure).toContainText(
      status === 403
        ? 'Deviation records unavailable'
        : 'Deviation records could not be read'
    );
    await expect(disclosure).not.toContainText('Showing 0');
    await expect(disclosure).not.toContainText('Connected source');
    await containment(page);
    await capture(
      page,
      info.outputPath(`monitoring-unavailable-${status}-dark.png`)
    );
  });
}

test('successful empty deviations and unconfigured monitoring stay unknown', async ({
  page
}, info) => {
  await closeoutWork(page);
  await monitoring(page, { empty: true, unconfigured: true });
  await openCloseout(page);
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText(
    'No health source configured — condition unknown'
  );
  await expect(disclosure).toContainText(
    'Showing 0 of 0 active occurrence records'
  );
  await expect(disclosure).toContainText(
    'Latest observation: Time unavailable'
  );
  await capture(page, info.outputPath('monitoring-unconfigured.png'));
});

test('a disabled source has no invented source success or watch duration', async ({
  page
}, info) => {
  await closeoutWork(page);
  await monitoring(page, { disabled: true });
  await openCloseout(page, 'dark');
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('Disabled');
  await expect(disclosure).toContainText(
    'Last source success: Time unavailable'
  );
  await expect(disclosure).toContainText('Data degraded');
  await expect(disclosure).not.toContainText('Connected source');
  await capture(page, info.outputPath('monitoring-disabled-dark.png'));
});

test('Health keeps current observations distinct from retained occurrence evidence', async ({
  page
}, info) => {
  await openHealth(page, { scheme: 'dark' });
  await expect(
    page.getByText(
      'Configured proactive monitoring applies before, during and after maintenance and Closeout.',
      { exact: false }
    )
  ).toBeVisible();
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('health-continuity-dark.png')
  });
  await mockFaultHistory(page);
  await openFaultHistory(page, { query: '?occurrence=901', scheme: 'dark' });
  const retained = page.getByTestId('occurrence-evidence');
  await expect(retained).toContainText('118.4');
  await expect(retained).toContainText('Synthetic SCADA');
  await expect(retained).toContainText('Evidence retained at the time');
  await expect(retained).toContainText('Retained snapshot');
  await containment(page);
  await capture(page, info.outputPath('fault-retained-continuity-dark.png'));
});

test('a refused refresh hides previously read source and deviation facts', async ({
  page
}, info) => {
  await closeoutWork(page, { completed: true });
  await monitoring(page);
  await openCloseout(page, 'dark');
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('NEW-903');
  await expect(disclosure).toContainText('Connected source');
  await monitoring(page, { healthStatus: 403, occurrenceStatus: 403 });
  await disclosure
    .getByRole('button', {
      name: 'Refresh monitoring and deviations',
      exact: true
    })
    .click();
  await expect(disclosure).toContainText(
    'Monitoring unavailable in the current scope'
  );
  await expect(disclosure).toContainText('Deviation records unavailable');
  await expect(disclosure).not.toContainText('NEW-903');
  await expect(disclosure).not.toContainText('Connected source');
  await capture(page, info.outputPath('monitoring-refused-refresh-dark.png'));
});

test('long source identities remain readable within the phone and desktop', async ({
  page
}, info) => {
  await closeoutWork(page);
  await monitoring(page, { longIdentity: true });
  await openCloseout(page);
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('LongSourceAlarm'.repeat(30));
  await containment(page);
  await capture(page, info.outputPath('monitoring-long-source.png'));
});
