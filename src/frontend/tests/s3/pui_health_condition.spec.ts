import { expect, test } from '@playwright/test';
import type { HealthEvidenceSnapshot } from '../../lib/types/MachineHealth';
import { nativeBaseline } from '../s0/guards';
import {
  HEALTH_SIGNALS,
  anomaly,
  openHealth,
  signalTable,
  sparkline,
  trendCard
} from './healthFixtures';

nativeBaseline();
test.use({ timezoneId: 'UTC' });

const snapshot: HealthEvidenceSnapshot = {
  id: '9ae91736-38b8-4d7e-aafe-d8f64e2263ed',
  machine: 18,
  binding: 201,
  source: 1,
  anomaly: 44,
  signal_label: 'Fault-time discharge flow',
  unit: 'm3/h',
  captured_at: '2025-10-18T09:01:00Z',
  window_start: '2025-10-18T09:00:00Z',
  window_end: '2025-10-18T09:00:00Z',
  samples: [{ observed_at: '2025-10-18T09:00:00Z', value: 380 }],
  statistics: { latest: 380 },
  quality: 'good',
  stale: false,
  reason: 'anomaly_repair',
  source_references: { source: 'Original source' },
  content_hash: 'retained-fixture-content-hash',
  system_actor: 'detector'
};

for (const scheme of ['light', 'dark'] as const) {
  test(`five states, missing evidence, canonical anomalies and native phone trends (${scheme})`, async ({
    page
  }, info) => {
    const signals = ['normal', 'warning', 'critical', 'offline', 'unknown'].map(
      (state, index) => ({
        ...HEALTH_SIGNALS[0],
        id: 201 + index,
        name: `Signal ${state}`,
        state: state as
          | 'normal'
          | 'warning'
          | 'critical'
          | 'offline'
          | 'unknown',
        value: index === 4 ? null : 812.5,
        stale: index === 3,
        component: {
          code: `SLOT:${index}`,
          name: `Component ${index}`,
          status: 'verified'
        }
      })
    );
    const requests = await openHealth(page, {
      scheme,
      signals,
      summary: {
        state: 'normal',
        active_anomaly_count: 2,
        anomaly_counts: { critical: 0, warning: 2, info: 0 }
      },
      anomalies: [
        anomaly({
          signals: [
            {
              binding_id: 202,
              display_name: 'renamed signal',
              unit: '',
              signal_kind: ''
            }
          ],
          work_order: 818
        }),
        anomaly({ pk: 45, title: 'Unassociated machine anomaly', signals: [] })
      ]
    });
    await expect(
      page.getByText('5 mapped · 3 usable now · 3 evaluated now', {
        exact: true
      })
    ).toBeVisible();
    await expect(
      page.getByText('Critical coverage unavailable', { exact: true })
    ).toBeVisible();
    const table = signalTable(page);
    await expect(table.getByRole('columnheader')).toHaveCount(10);
    expect(await table.locator('th').allTextContents()).toEqual([
      'Signal',
      'Value',
      'State',
      'Trend',
      'Observed',
      'Current condition',
      'Active anomalies',
      'Quality',
      'Source',
      'Limits'
    ]);
    for (const state of [
      'Normal',
      'Warning',
      'Critical',
      'Offline',
      'Unknown'
    ]) {
      const row = table
        .getByRole('row')
        .filter({ hasText: `Signal ${state.toLowerCase()}` });
      const badge = row
        .locator('.mantine-Badge-root')
        .filter({ hasText: state })
        .first();
      await expect(badge).toBeVisible();
      expect(
        await badge
          .locator('.mantine-Badge-label')
          .evaluate((el) => el.scrollWidth <= el.clientWidth)
      ).toBe(true);
    }
    const normal = table.getByRole('row').filter({ hasText: 'Signal normal' });
    const warning = table
      .getByRole('row')
      .filter({ hasText: 'Signal warning' });
    const details = normal.getByRole('button', {
      name: 'Details for Signal normal',
      exact: true
    });
    if (info.project.name.includes('phone')) {
      await expect(details).toHaveAttribute('aria-expanded', 'false');
      await expect(
        normal.getByRole('cell', { name: /Warning below/ })
      ).toHaveCount(0);
      await details.click();
      await expect(details).toHaveAttribute('aria-expanded', 'true');
      await warning
        .getByRole('button', {
          name: 'Details for Signal warning',
          exact: true
        })
        .click();
    }
    await expect(
      warning.getByRole('link', { name: 'Open owning work 818' })
    ).toHaveAttribute('href', '/maintenance/work-orders/818/');
    await expect(warning).toContainText('1 active anomaly cites this signal');
    await expect(normal).toContainText(
      '1 active machine anomaly does not name its signals; this signal may be affected.'
    );
    await expect(
      normal.getByRole('cell', {
        name: 'Warning below 600 m3/h · Critical below 400 m3/h',
        exact: true
      })
    ).toBeVisible();
    await expect(normal.getByRole('cell').nth(3).getByRole('img')).toHaveCount(
      1
    );
    await expect(
      normal.getByRole('cell', { name: '812.5 m3/h', exact: true })
    ).toBeVisible();
    if (info.project.name.includes('phone')) {
      await normal.screenshot({
        path: info.outputPath(`row-details-${scheme}.png`)
      });
      await details.click();
      await warning
        .getByRole('button', {
          name: 'Details for Signal warning',
          exact: true
        })
        .click();
    }
    await expect(
      table.getByRole('row').filter({ hasText: 'Signal unknown' })
    ).toContainText('Not assessed: no observation');
    const graph = sparkline(page, 'Signal normal');
    await graph.scrollIntoViewIfNeeded();
    const box = (await graph.boundingBox())!;
    if (info.project.name.includes('phone')) {
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(390);
      await page
        .getByRole('button', { name: 'All columns table', exact: true })
        .click();
      await expect(
        page.getByRole('button', { name: 'Signal cards', exact: true })
      ).toBeVisible();
      await page
        .getByRole('button', { name: 'Signal cards', exact: true })
        .click();
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    const readings = page.getByRole('region', {
      name: 'Signal readings; scroll for all columns',
      exact: true
    });
    await readings.screenshot({
      path: info.outputPath(`five-states-${scheme}.png`),
      animations: 'disabled'
    });
    if (!info.project.name.includes('phone')) {
      await readings.evaluate((el) => {
        el.scrollLeft = el.scrollWidth;
      });
      await readings.screenshot({
        path: info.outputPath(`row-context-${scheme}.png`)
      });
      await readings.evaluate((el) => {
        el.scrollLeft = 0;
      });
    }
    await normal
      .getByRole('button', {
        name: 'Inspect signal: Signal normal',
        exact: true
      })
      .click();
    const evidence = page.getByRole('region', {
      name: 'Signal evidence: Signal normal',
      exact: true
    });
    await expect(evidence.getByRole('heading')).toBeFocused();
    await expect(evidence).toContainText(
      'Warning below 600 m3/h · Critical below 400 m3/h'
    );
    await expect(evidence).toContainText(
      '1 active machine anomaly does not name its signals; this signal may be affected.'
    );
    await evidence.screenshot({
      path: info.outputPath(`bounded-evidence-${scheme}.png`)
    });
    await evidence
      .getByRole('button', { name: 'Close signal evidence' })
      .click();
    await page
      .locator('.mantine-Paper-root')
      .filter({
        has: page.getByText('Observation and coverage', { exact: true })
      })
      .last()
      .screenshot({ path: info.outputPath(`coverage-${scheme}.png`) });
    expect(requests.batch).toHaveLength(1);
    expect(requests.chart).toHaveLength(1);
  });
}

test('filter keeps component scope and selected chart instead of silently selecting another signal', async ({
  page
}) => {
  const requests = await openHealth(page, {
    signals: HEALTH_SIGNALS.map((row) => ({
      ...row,
      component:
        row.id === 203
          ? { code: 'BRG:1', name: 'Bearing', status: 'draft' }
          : null
    }))
  });
  await signalTable(page)
    .getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    })
    .click();
  await page.getByRole('combobox', { name: 'Component filter' }).click();
  await page.getByRole('option', { name: 'Bearing · BRG:1' }).click();
  await expect(signalTable(page).getByRole('row')).toHaveCount(2);
  await expect(
    page.getByText('The selected signal is outside this table filter.', {
      exact: false
    })
  ).toBeVisible();
  await expect(
    trendCard(page).getByRole('combobox', { name: 'Parameter' })
  ).toHaveValue('Discharge flow (m3/h)');
  await page
    .getByRole('textbox', { name: 'Search signals' })
    .fill('unmapped-name');
  await expect(
    page.getByText('No mapped signals match these filters.', { exact: false })
  ).toBeVisible();
  await expect(
    page.getByRole('combobox', { name: 'Component filter' })
  ).toHaveValue('Bearing · BRG:1');
  expect(requests.batch).toHaveLength(1);
});

test('retained exact entry preserves fault evidence beside a normal current assessment', async ({
  page
}, info) => {
  await openHealth(page, {
    snapshots: [snapshot],
    entrySearch: `?snapshot=${snapshot.id}`
  });
  const retained = page.getByRole('region', {
    name: 'Retained evidence',
    exact: true
  });
  await expect(retained).toContainText('Retained; not current');
  await expect(retained).toContainText('2025');
  await expect(retained).toContainText('380 m3/h');
  await expect(retained).toContainText('Original source');
  await expect(retained).toContainText(snapshot.content_hash);
  await retained.screenshot({
    path: info.outputPath('retained-versus-current.png')
  });
  await expect(
    signalTable(page).getByRole('row').filter({ hasText: 'Discharge flow' })
  ).toContainText('812.5 m3/h');
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(retained).toContainText('380 m3/h');
  await retained
    .getByRole('button', { name: 'Return to current observations' })
    .click();
  await expect(page.getByTestId('destination')).not.toContainText('snapshot=');
  await expect(retained).not.toContainText('380 m3/h');
  await retained
    .getByRole('button', { name: 'Browse retained evidence' })
    .click();
  await retained
    .getByRole('button', { name: /^Retained: Fault-time discharge flow/ })
    .click();
  await expect(page.getByTestId('destination')).toContainText(
    `snapshot=${snapshot.id}`
  );
  await expect(retained).toContainText('380 m3/h');
});

test('missing retained citation and failed current reads never become empty healthy success', async ({
  page
}) => {
  await openHealth(page, {
    snapshots: [],
    entrySearch: `?snapshot=${snapshot.id}`,
    signalsStatus: 503,
    anomaliesStatus: 503
  });
  await expect(
    page.getByRole('region', { name: 'Retained evidence', exact: true })
  ).toContainText('Retained snapshot unavailable');
  await expect(
    page.getByText('Signal coverage unavailable:', { exact: false })
  ).toBeVisible();
  await expect(
    page.getByText('Active anomalies unavailable', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('No signals are mapped', { exact: false })
  ).toHaveCount(0);
  await expect(
    page.getByText('No active anomalies are recorded', { exact: false })
  ).toHaveCount(0);
});
