import { type Page, expect, test } from '@playwright/test';
import type {
  HealthEvidenceSnapshot,
  PreliminaryResults
} from '../../lib/types/MachineHealth';
import { nativeBaseline } from '../s0/guards';
import {
  HEALTH_SIGNALS,
  NOW,
  anomaly,
  openHealth,
  signalTable,
  sparkline,
  trendCard
} from './healthFixtures';

nativeBaseline();
test.use({ timezoneId: 'UTC' });

async function openSignalFilters(page: Page) {
  const toggle = page.getByRole('button', {
    name: 'Signal filters',
    exact: true
  });
  if (
    (await toggle.isVisible()) &&
    (await toggle.getAttribute('aria-expanded')) === 'false'
  ) {
    await toggle.click();
  }
}

for (const scheme of ['light', 'dark'] as const) {
  test(`compact Health keeps readings above the fold and stale is a real state (${scheme})`, async ({
    page
  }, info) => {
    const phone = info.project.name.includes('phone');
    await page.setViewportSize(
      phone ? { width: 390, height: 844 } : { width: 1440, height: 900 }
    );
    await openHealth(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      signals: [HEALTH_SIGNALS[0]],
      summary: {
        state: 'normal',
        active_anomaly_count: 0,
        anomaly_counts: { critical: 0, warning: 0, info: 0 }
      }
    });
    const root = page.getByTestId('pump-health');
    await expect(
      root.getByRole('heading', { name: 'Active anomalies', exact: true })
    ).toHaveCount(0);
    await expect(
      root.getByText('0 active anomalies', { exact: true })
    ).toBeVisible();
    const firstReading = signalTable(page).getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    });
    const firstBox = (await firstReading.boundingBox())!;
    expect(firstBox.y).toBeGreaterThanOrEqual(0);
    expect(firstBox.y + Math.min(firstBox.height, 24)).toBeLessThanOrEqual(
      page.viewportSize()!.height
    );
    const value = signalTable(page).getByRole('cell', {
      name: '812.5 cusec',
      exact: true
    });
    const valueBox = (await value.boundingBox())!;
    expect(valueBox.y).toBeGreaterThanOrEqual(0);
    expect(valueBox.y + valueBox.height).toBeLessThanOrEqual(
      page.viewportSize()!.height
    );
    if (phone) {
      const refresh = root.getByRole('button', {
        name: 'Refresh',
        exact: true
      });
      const search = root.getByRole('textbox', {
        name: 'Search signals',
        exact: true
      });
      expect(
        Math.abs(
          (await refresh.boundingBox())!.y - (await search.boundingBox())!.y
        )
      ).toBeLessThan(1);
      const row = signalTable(page).locator('tbody tr').first();
      await expect(row.locator('td').nth(3)).toBeHidden();
      const details = row.getByRole('button', {
        name: 'Details for Discharge flow',
        exact: true
      });
      await details.click();
      await expect(details).toHaveAttribute('aria-expanded', 'true');
      for (const column of [3, 4, 5, 6])
        await expect(row.locator('td').nth(column)).toBeVisible();
      await expect(row.locator('td').nth(3)).toContainText(
        'Warning below 600 cusec'
      );
      await expect(row.locator('td').nth(4)).toContainText('18 Oct 2026');
      await expect(row.locator('td').nth(5)).toHaveText('Good');
      await expect(
        row
          .locator('td')
          .nth(6)
          .getByRole('img', { name: 'Recent trend for Discharge flow' })
      ).toBeVisible();
      await details.click();
      await expect(
        page.getByRole('combobox', { name: 'Component filter', exact: true })
      ).toBeHidden();
      await openSignalFilters(page);
      await expect(
        page.getByRole('combobox', { name: 'Component filter', exact: true })
      ).toBeVisible();
      await expect(
        page.getByRole('combobox', { name: 'Order by', exact: true })
      ).toBeVisible();
    }
    await openHealth(page, {
      scheme,
      nativeTheme: true,
      compactChrome: true,
      signals: [
        {
          ...HEALTH_SIGNALS[0],
          name: 'Stale discharge flow',
          stale: true,
          state: 'warning'
        },
        {
          ...HEALTH_SIGNALS[0],
          id: 299,
          key: '/missing',
          name: 'Missing measurement',
          value: null,
          state: 'unknown'
        }
      ]
    });
    const stale = signalTable(page)
      .getByRole('row')
      .filter({ hasText: 'Stale discharge flow' })
      .getByRole('button', { name: 'Stale', exact: true });
    await expect(stale).toBeVisible();
    await stale.focus();
    await expect(page.getByRole('tooltip')).toHaveText(
      'Not assessed: stale last-known reading'
    );
    await expect(stale).toHaveAccessibleDescription(
      'Not assessed: stale last-known reading'
    );
    await expect(
      signalTable(page).locator('[role="img"][tabindex="0"]')
    ).toHaveCount(0);
  });
}

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
      page
        .getByTestId('health-summary')
        .getByText('Normal · 3/5', { exact: true })
    ).toBeVisible();
    const coverage = page.getByRole('button', {
      name: /Critical signals: not designated$/
    });
    await expect(coverage).toBeVisible();
    await coverage.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'No critical-signal designation is supplied.'
    );
    const table = signalTable(page);
    await expect(table.getByRole('columnheader')).toHaveCount(7);
    expect(await table.locator('th').allTextContents()).toEqual([
      'Signal',
      'Value',
      'State',
      'Limits',
      'Observed',
      'Quality',
      'Trend'
    ]);
    for (const state of ['Normal', 'Warning', 'Critical']) {
      const row = table
        .getByRole('row')
        .filter({ hasText: `Signal ${state.toLowerCase()}` });
      const badge = row
        .locator('.mantine-Badge-root')
        .filter({ hasText: state })
        .first();
      await expect(badge).toBeVisible();
      await expect(
        row.getByRole('button', { name: state, exact: true })
      ).toBeVisible();
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
    await expect(details).toHaveAttribute('aria-expanded', 'false');
    if (info.project.name.includes('phone'))
      await expect(normal.locator('td').nth(3)).toBeHidden();
    else
      await expect(
        normal.getByRole('cell', { name: /Warning below/ })
      ).toBeVisible();
    await details.click();
    await expect(details).toHaveAttribute('aria-expanded', 'true');
    await warning
      .getByRole('button', {
        name: 'Details for Signal warning',
        exact: true
      })
      .click();
    await expect(
      warning
        .locator('xpath=following-sibling::tr[1]')
        .getByRole('link', { name: 'Open owning work 818' })
    ).toHaveAttribute('href', '/maintenance/work-orders/818/');
    await expect(
      warning.locator('xpath=following-sibling::tr[1]')
    ).toContainText('1 active anomaly cites this signal');
    await expect(
      normal.locator('xpath=following-sibling::tr[1]')
    ).toContainText(
      '1 active machine anomaly does not name its signals; this signal may be affected.'
    );
    await expect(
      normal.getByRole('cell', {
        name: /Warning below 600 cusec · Critical below 400 cusec/
      })
    ).toBeVisible();
    await expect(normal.getByRole('cell').nth(6).getByRole('img')).toHaveCount(
      1
    );
    await expect(
      normal.getByRole('cell', { name: '812.5 cusec', exact: true })
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
    const unknown = table
      .getByRole('row')
      .filter({ hasText: 'Signal unknown' });
    await expect(
      unknown.getByRole('button', {
        name: 'State: Not assessed: no observation',
        exact: true
      })
    ).toHaveText('—');
    await expect(
      unknown.getByRole('button', {
        name: 'Value: No observation available',
        exact: true
      })
    ).toHaveText('—');
    await expect(unknown.getByText('Unknown', { exact: true })).toHaveCount(0);
    if (info.project.name.includes('phone')) await details.click();
    const graph = sparkline(page, 'Signal normal');
    await graph.scrollIntoViewIfNeeded();
    const box = (await graph.boundingBox())!;
    if (info.project.name.includes('phone')) {
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(390);
      await details.click();
      await openSignalFilters(page);
      await page
        .getByRole('button', { name: 'All columns table', exact: true })
        .click();
      await expect(
        page.getByRole('button', { name: 'Signal cards', exact: true })
      ).toBeVisible();
      await expect(
        table.getByRole('button', { name: /^Details for/ })
      ).toHaveCount(5);
      await expect(
        normal.getByRole('cell', { name: /Warning below/ })
      ).toBeVisible();
      await details.click();
      const sourceType = normal
        .locator('xpath=following-sibling::tr[1]')
        .locator('.mantine-Badge-label')
        .first();
      const sourceLabelSize = await sourceType.evaluate((el) => ({
        scroll: el.scrollWidth,
        client: el.clientWidth
      }));
      expect(sourceLabelSize.scroll).toBeLessThanOrEqual(
        sourceLabelSize.client
      );
      const allColumns = page.getByRole('region', {
        name: 'Signal readings; scroll for all columns',
        exact: true
      });
      await allColumns.screenshot({
        path: info.outputPath(`all-columns-${scheme}.png`)
      });
      await allColumns.evaluate((el) => {
        el.scrollLeft = el.scrollWidth;
      });
      await allColumns.screenshot({
        path: info.outputPath(`all-columns-context-${scheme}.png`)
      });
      await allColumns.evaluate((el) => {
        el.scrollLeft = 0;
      });
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
      'Warning below 600 cusec · Critical below 400 cusec'
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
    await page.getByTestId('health-summary').screenshot({
      path: info.outputPath(`coverage-${scheme}.png`)
    });
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
  const raw = signalTable(page)
    .getByRole('row')
    .filter({ hasText: 'Motor DE vibration (raw)' });
  await expect(
    raw.getByRole('cell', { name: '1834 (no unit)', exact: true })
  ).toBeVisible();
  const rawDetails = raw.getByRole('button', {
    name: 'Details for Motor DE vibration (raw)',
    exact: true
  });
  const rawInspect = raw.getByRole('button', {
    name: 'Inspect signal: Motor DE vibration (raw)',
    exact: true
  });
  await rawInspect.focus();
  await rawInspect.press('Shift+Tab');
  await expect(rawDetails).toBeFocused();
  const detailTooltip = page.getByRole('tooltip', {
    name: /^1834 \(no unit\)/
  });
  await expect(detailTooltip).toBeVisible();
  await expect(rawDetails).toHaveAttribute(
    'aria-describedby',
    (await detailTooltip.getAttribute('id'))!
  );
  await rawDetails.click();
  await expect(rawDetails).toHaveAttribute('aria-expanded', 'true');
  const stops = await raw
    .locator('button')
    .evaluateAll((nodes) =>
      nodes
        .filter((node) => node.tabIndex >= 0)
        .map((node) => node.getAttribute('aria-label'))
    );
  expect(stops).toEqual([
    'Details for Motor DE vibration (raw)',
    'Inspect signal: Motor DE vibration (raw)'
  ]);
  await expect(
    page.getByRole('region', {
      name: 'Signal evidence: Discharge flow',
      exact: true
    })
  ).toBeVisible();
  await expect(
    page.getByRole('region', {
      name: 'Signal evidence: Motor DE vibration (raw)',
      exact: true
    })
  ).toHaveCount(0);
  await expect(
    trendCard(page).getByRole('combobox', { name: 'Parameter' })
  ).toHaveValue('Discharge flow (cusec)');
  await rawDetails.click();
  await openSignalFilters(page);
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
  ).toHaveValue('Discharge flow (cusec)');
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
  await expect(retained).toContainText('380 m³/h');
  const retainedScroll = retained.getByRole('region', {
    name: 'Retained observations; scroll for all columns',
    exact: true
  });
  await expect(retainedScroll).toHaveAttribute('tabindex', '0');
  if (info.project.name.includes('phone')) {
    await retainedScroll.focus();
    await page.keyboard.press('ArrowRight');
    await expect
      .poll(() => retainedScroll.evaluate((el) => el.scrollLeft))
      .toBeGreaterThan(0);
  }
  await expect(retained).toContainText('Original source');
  await expect(retained).toContainText(snapshot.content_hash);
  await expect(retained).toContainText('System actor: detector');
  await expect(retained).toContainText('Captured for the repair raised for it');
  await retained.screenshot({
    path: info.outputPath('retained-versus-current.png')
  });
  await expect(
    signalTable(page).getByRole('row').filter({ hasText: 'Discharge flow' })
  ).toContainText('812.5 cusec');
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(retained).toContainText('380 m³/h');
  await retained
    .getByRole('button', { name: 'Return to current observations' })
    .click();
  await expect(page.getByTestId('destination')).not.toContainText('snapshot=');
  await expect(retained).not.toContainText('380 m³/h');
  await retained
    .getByRole('button', { name: 'Browse retained evidence' })
    .click();
  await retained
    .getByRole('button', { name: /^Retained: Fault-time discharge flow/ })
    .click();
  await expect(page.getByTestId('destination')).toContainText(
    `snapshot=${snapshot.id}`
  );
  await expect(retained).toContainText('380 m³/h');
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
  await expect(
    page.getByText('Showing 0 active anomaly records', { exact: false })
  ).toHaveCount(0);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`zero evaluated signals keep an Unknown condition (${scheme})`, async ({
    page
  }, info) => {
    await openHealth(page, {
      scheme,
      signals: HEALTH_SIGNALS.map((signal) => ({
        ...signal,
        limits: {},
        state: 'unknown'
      })),
      summary: { state: 'unknown' }
    });
    const condition = page.getByTestId('health-summary');
    await expect(
      condition
        .locator('.mantine-Badge-label')
        .getByText('Unknown', { exact: true })
    ).toBeVisible();
    await expect(
      condition
        .locator('.mantine-Badge-label')
        .getByText('Normal', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText(
        'Assessed on 0 of 6 signals · Critical signals: not designated',
        {
          exact: true
        }
      )
    ).toBeVisible();
    const limits = signalTable(page).getByRole('button', {
      name: 'Limits: No limits set · Limits on signal mapping; engineering approval not supplied',
      exact: true,
      includeHidden: true
    });
    await expect(limits).toHaveCount(6);
    for (const limit of await limits.all()) {
      await expect(limit).toHaveText('—');
      await expect(limit).toHaveAttribute('tabindex', '-1');
    }
    await expect(
      signalTable(page).getByRole('button', {
        name: 'State: Not assessed: limits not configured',
        exact: true
      })
    ).toHaveCount(6);
    await expect(
      signalTable(page).getByText('Unknown', { exact: true })
    ).toHaveCount(0);
    await condition.locator('../../..').screenshot({
      path: info.outputPath(`unknown-summary-${scheme}.png`)
    });
  });
}

for (const newest of ['future', 'expired', 'current'] as const) {
  test(`last observation freshness follows its own ${newest} row`, async ({
    page
  }, info) => {
    const newestAt = new Date(
      NOW + (newest === 'future' ? 10800000 : -20000)
    ).toISOString();
    await openHealth(page, {
      signals: [
        {
          ...HEALTH_SIGNALS[0],
          observedAt: newestAt,
          stale: newest !== 'current',
          state: newest === 'current' ? 'normal' : 'unknown',
          freshnessSeconds: newest === 'expired' ? 10 : 300
        },
        {
          ...HEALTH_SIGNALS[1],
          observedAt: new Date(NOW - 30000).toISOString(),
          stale: newest === 'current',
          freshnessSeconds: newest === 'current' ? 10 : 300
        }
      ],
      summary: {
        last_observed_at: newestAt,
        state: newest === 'current' ? 'normal' : 'unknown'
      }
    });
    const observation = page
      .getByTestId('health-summary')
      .getByRole('button')
      .last();
    await expect(observation.locator('[data-tone="stale"]')).toHaveCount(
      newest === 'current' ? 0 : 1
    );
    if (newest === 'future')
      await expect(observation).toContainText(
        'Stale (server flag) · 180 min ahead of your clock · freshness budget 300 s'
      );
    if (newest === 'expired')
      await expect(observation).toContainText(
        'Stale · 20 s old · freshness budget 10 s'
      );
    if (newest === 'current')
      await expect(observation).toContainText('just now');
    await observation.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      newest === 'future'
        ? 'Last observation: 18 Oct 2026 12:00:00'
        : 'Last observation: 18 Oct 2026 08:59:40'
    );
    await observation.screenshot({
      path: info.outputPath(`newest-${newest}.png`)
    });
  });
}

test('retained recorder uses the human name and states missing provenance', async ({
  page
}, info) => {
  await openHealth(page, {
    snapshots: [
      {
        ...snapshot,
        captured_by_name: 'Recorded Technician',
        system_actor: 'analysis-worker'
      }
    ],
    entrySearch: `?snapshot=${snapshot.id}`
  });
  const retained = page.getByRole('region', {
    name: 'Retained evidence',
    exact: true
  });
  await expect(retained).toContainText('Captured by Recorded Technician');
  await expect(retained).toContainText('System actor: analysis-worker');
  await retained.screenshot({ path: info.outputPath('retained-recorder.png') });
  await page.route(
    '**/api/machine-health/machines/18/health/snapshots/**',
    (route) =>
      route.fulfill({
        json: {
          count: 1,
          results: [{ ...snapshot, captured_by_name: null, system_actor: '' }]
        }
      })
  );
  await page.reload();
  await expect(retained).toContainText('Recorder not recorded');
});

test('preliminary analysis citation opens its exact retained incident', async ({
  page
}, info) => {
  await openHealth(page, {
    anomalies: [anomaly()],
    snapshots: [snapshot],
    nativeTheme: true,
    compactChrome: true
  });
  const results: PreliminaryResults = {
    status: 'available',
    authority: 'derived',
    authority_source: null,
    likely_cause: 'Fixture observation only',
    failure_mode: null,
    confidence: 0.5,
    confidence_label: 'Fixture',
    alternatives: [],
    evidence: [
      {
        snapshot_id: snapshot.id,
        observation: 'Fault-time flow was retained',
        relation: 'supports',
        stale: false
      }
    ],
    confirm_tests: [],
    data_window: {
      start: snapshot.window_start,
      end: snapshot.window_end,
      snapshot_count: 1
    },
    freshness: { stale: false, stale_signal_count: 0 },
    quality: { summary: 'good', bad_signal_count: 0 },
    provider: 'fixture',
    model_or_rule_version: 'fixture',
    generated_at: '2026-10-18T09:01:02Z',
    verified_by_user: false,
    verified_at: null,
    amendments: [],
    schema_version: 1
  };
  await page.route(
    '**/api/machine-health/machines/18/health/anomalies/44/preliminary-analysis/',
    (route) => route.fulfill({ json: { preliminary_results: results } })
  );
  await page
    .getByRole('button', {
      name: 'Analyze Discharge flow below warning limit',
      exact: true
    })
    .click();
  await expect(
    page.getByText('Generated 18 Oct 2026 09:01:02 by fixture', { exact: true })
  ).toBeVisible();
  await page
    .getByText('Generated 18 Oct 2026 09:01:02 by fixture', { exact: true })
    .screenshot({ path: info.outputPath('preliminary-generated-instant.png') });
  const citation = page.getByRole('link', {
    name: 'Snapshot 9ae91736',
    exact: true
  });
  await expect(citation).toHaveAttribute(
    'href',
    `/machines/machine/18/health/?snapshot=${snapshot.id}`
  );
  await citation.click();
  const retained = page.getByRole('region', {
    name: 'Retained evidence',
    exact: true
  });
  await expect(retained).toContainText(snapshot.content_hash);
  await expect(retained).toContainText('380 m³/h');
  await retained.screenshot({
    path: info.outputPath('diagnosis-exact-citation.png')
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 designated critical coverage preserves unknown evidence (${scheme})`, async ({
    page
  }, info) => {
    const designation = {
      critical: true,
      version: 2,
      reason: 'Engineer reviewed critical flow',
      recorded_by: 'Synthetic engineer',
      recorded_at: '2026-10-18T08:00:00Z'
    };
    await openHealth(page, {
      scheme,
      signals: [
        { ...HEALTH_SIGNALS[0], criticalDesignation: designation },
        {
          ...HEALTH_SIGNALS[0],
          id: 299,
          key: '/critical/missing',
          name: 'Missing critical measurement',
          value: null,
          state: 'unknown',
          criticalDesignation: designation
        }
      ],
      summary: { state: 'normal' }
    });
    await expect(
      page.getByText(/Critical signals: 1 of 2 assessed/)
    ).toBeVisible();
    await page
      .getByRole('button', {
        name: /Critical signals: 1 of 2 assessed$/
      })
      .focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'Missing critical measurement: no observation'
    );
    await expect(page.getByRole('tooltip')).toContainText(
      'Unassessed or missing evidence remains unknown'
    );
    await page.getByRole('button', { name: /Inspect.*Discharge flow/ }).click();
    await expect(
      page.getByText('Engineer reviewed critical flow · Synthetic engineer', {
        exact: false
      })
    ).toBeVisible();
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0', {
      timeout: 60000
    });
    const sourceTable = page.getByRole('table', { name: 'Source connections' });
    const sourceType = sourceTable.getByText('SCADA', { exact: true });
    expect(
      await sourceType.evaluate(
        (node) => node.scrollWidth <= node.clientWidth + 1
      )
    ).toBe(true);
    const connected = sourceTable.getByText(/^Succeeded /);
    await expect(connected).toBeVisible();
    expect(
      await connected.evaluate(
        (node) => node.scrollWidth <= node.clientWidth + 1
      )
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath(`o15-critical-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  });
}

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 review remapped critical evidence and identify inactive bindings (${scheme})`, async ({
    page
  }, info) => {
    const decision = {
      critical: true,
      version: 1,
      reason: 'Required before remap',
      recorded_by: 'Synthetic engineer',
      recorded_at: '2026-10-18T08:00:00Z'
    };
    const batch: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.endsWith('/trends/') && url.searchParams.has('bindings'))
        batch.push(url.searchParams.get('bindings')!);
    });
    await openHealth(page, {
      scheme,
      signals: [
        HEALTH_SIGNALS[0],
        {
          ...HEALTH_SIGNALS[0],
          id: 299,
          key: '/changed/meaning',
          name: 'Remapped critical flow',
          criticalDesignation: {
            ...decision,
            applies_current: false,
            review_required: true
          }
        },
        {
          ...HEALTH_SIGNALS[0],
          id: 398,
          name: 'Retired critical flow',
          active: false,
          value: null,
          state: 'unknown',
          criticalDesignation: { ...decision, applies_current: true }
        }
      ]
    });
    await expect(
      page.getByText(/Critical signals: 0 of 2 assessed/)
    ).toBeVisible();
    await page
      .getByRole('button', {
        name: /Critical signals: 0 of 2 assessed$/
      })
      .focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'Remapped critical flow: review required'
    );
    await expect(page.getByRole('tooltip')).toContainText(
      'Retired critical flow: review required'
    );
    await expect(
      page.getByText('Designated critical coverage', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText(
        'Inactive binding — retained for critical coverage review',
        { exact: true }
      )
    ).toBeVisible();
    await page
      .getByRole('button', {
        name: 'Inspect signal: Remapped critical flow',
        exact: true
      })
      .click();
    await expect(
      page.getByText('Designation review required — binding meaning changed', {
        exact: true
      })
    ).toBeVisible();
    await expect(
      page.getByText(/Required before remap · Synthetic engineer/)
    ).toBeVisible();
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0', {
      timeout: 60000
    });
    expect(batch.length).toBeGreaterThan(0);
    expect(batch.every((ids) => !ids.split(',').includes('398'))).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true);
    await expect(signalTable(page).getByRole('row')).toHaveCount(4);
    await expect(
      signalTable(page)
        .getByRole('row')
        .filter({ hasText: 'Retired critical flow' })
    ).toContainText('Inactive binding — retained for critical coverage review');
    const evidence = page.locator('#health-signal-evidence');
    await expect(
      evidence.getByText(/Selected history window, read from the source:/)
    ).toBeVisible({ timeout: 60000 });
    await expect(
      evidence.getByText(
        'No snapshot of this signal in the latest 50 machine snapshots.',
        { exact: true }
      )
    ).toBeVisible({ timeout: 60000 });
    await page.screenshot({
      path: info.outputPath(`o15-review-critical-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  });
}

test('a capped anomaly read remains visibly limited and cannot establish absence', async ({
  page
}) => {
  await openHealth(page, {
    compactChrome: true,
    nativeTheme: true,
    signals: [HEALTH_SIGNALS[0]],
    summary: {
      active_anomaly_count: 201,
      anomaly_counts: { critical: 0, warning: 201, info: 0 }
    },
    anomalies: Array.from({ length: 200 }, (_, index) =>
      anomaly({
        pk: 1000 + index,
        title: `Capped anomaly ${index + 1}`,
        signals: []
      })
    )
  });
  await expect(
    page.getByText('Showing the first 200 active anomalies', { exact: true })
  ).toBeVisible();
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'The active anomaly list is incomplete.' })
  ).toBeVisible();
  await expect(
    page.getByText('0 active anomalies', { exact: true })
  ).toHaveCount(0);
});
