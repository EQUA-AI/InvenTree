import { type Locator, expect, test } from '@playwright/test';

import { MACHINE, NOW, SIGNALS, mount } from './fixtures';

void MACHINE;

test('resolved TEST history stops at closeout while the current KPI returns to measured', async ({
  page
}) => {
  const signalRequests: URL[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith('/health/signals/')) signalRequests.push(url);
  });
  const options: Parameters<typeof mount>[1] = {
    testAlarm: {
      key: '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5',
      status: 'open',
      triggeredAt: new Date(NOW - 20 * 60000).toISOString()
    }
  };
  const seriesRequests = await mount(page, options);
  await expect(page.locator('[data-kpi="winding"]')).toContainText(
    'TEST alarm — Critical'
  );
  options.testAlarm!.status = 'resolved';
  options.testAlarm!.resolvedAt = new Date(NOW - 10 * 60000).toISOString();
  await page.reload();
  const winding = page.locator('[data-section="Motor winding temperature"]');
  await winding.scrollIntoViewIfNeeded();
  await expect(winding).toContainText(
    'Resolved TEST trace is a synthetic exercise'
  );
  await expect(winding).toContainText('TEST Winding 5: 159.5 °C (resolved)');
  await expect(page.getByTestId('performance-test-alarm')).toHaveCount(0);
  await expect(page.locator('[data-kpi="winding"]')).not.toContainText(
    'TEST alarm'
  );
  await expect(
    page.locator('[data-kpi="winding"] [data-kpi-value]')
  ).toHaveText('82');
  const testCurve = winding.locator(
    'path.recharts-line-curve[stroke-dasharray="6 4"]'
  );
  const measuredCurve = winding.locator(
    'path.recharts-line-curve:not([stroke-dasharray="6 4"])'
  );
  await expect(testCurve).toHaveCount(1);
  await expect.poll(() => seriesRequests.length).toBeGreaterThan(0);
  await expect
    .poll(() =>
      signalRequests.some(
        (request) =>
          request.searchParams.get('from') ===
            seriesRequests[0].searchParams.get('from') &&
          request.searchParams.get('to') ===
            seriesRequests[0].searchParams.get('to')
      )
    )
    .toBe(true);
  await expect
    .poll(async () =>
      measuredCurve.evaluateAll((paths) =>
        Math.max(
          0,
          ...paths.map((path) => {
            const box = (path as SVGGraphicsElement).getBBox();
            return box.x + box.width;
          })
        )
      )
    )
    .toBeGreaterThan(0);
  const testEnd = await testCurve.evaluate((path) => {
    const box = (path as SVGGraphicsElement).getBBox();
    return box.x + box.width;
  });
  const measuredEnd = await measuredCurve.evaluateAll((paths) =>
    Math.max(
      ...paths.map((path) => {
        const box = (path as SVGGraphicsElement).getBBox();
        return box.x + box.width;
      })
    )
  );
  expect(testEnd).toBeLessThan(measuredEnd - 5);
  await winding.screenshot({
    path: test.info().outputPath('resolved-test-history.png')
  });
});

test('a failed TEST history read remains visible as unavailable', async ({
  page
}) => {
  await mount(page, { failTestHistory: true });
  await expect(page.getByText('TEST history unavailable')).toBeVisible();
  await expect(
    page.getByText(
      'Resolved TEST annotations could not be read for this window.'
    )
  ).toBeVisible();
  await expect(
    page.locator('[data-kpi="winding"] [data-kpi-value]')
  ).toHaveText('82');
});

for (const status of ['open', 'acknowledged'] as const) {
  test(`a ${status} test is red on its chart without altering measured history`, async ({
    page
  }) => {
    const options: Parameters<typeof mount>[1] = {
      testAlarm: { key: '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5', status }
    };
    await mount(page, options);
    const winding = page.locator('[data-section="Motor winding temperature"]');
    await winding.scrollIntoViewIfNeeded();
    await expect(winding.getByTestId('performance-test-alarm')).toContainText(
      'TEST alarm — Critical'
    );
    await expect(winding.getByTestId('performance-test-alarm')).toContainText(
      '159.5'
    );
    await expect(winding.getByTestId('performance-test-alarm')).toContainText(
      'measured 79.6'
    );
    await expect(winding.getByTestId('performance-test-alarm')).toContainText(
      status === 'acknowledged' ? 'Acknowledged' : 'Open'
    );
    await expect(winding.locator('path[stroke-dasharray="6 4"]')).toHaveCount(
      1
    );
    await expect(
      winding.locator('svg text').filter({ hasText: 'TEST Winding 5: 159.5' })
    ).toHaveCount(1);
    await expect(page.locator('[data-kpi="winding"]')).toContainText(
      'TEST alarm — Critical'
    );
    await expect(
      page.locator('[data-kpi="winding"] [data-kpi-value]')
    ).toHaveText('159.5');
    await expect(
      page.locator('[data-kpi="winding"] [data-kpi-measured]')
    ).toContainText('79.6');
    await expect(
      page.locator('[data-kpi="winding"]').locator('..')
    ).toHaveAccessibleName(
      /Winding temperature.*TEST 159.5 °C.*Measured: 79.6 °C/
    );
    await expect(
      page.getByTestId('pump-performance').getByRole('alert')
    ).toHaveCount(1);
    await expect(winding.getByTestId('performance-test-alarm')).toHaveAttribute(
      'role',
      'status'
    );
    await expect(winding.getByTestId('performance-test-alarm')).toHaveAttribute(
      'aria-live',
      'polite'
    );
    await expect(
      page.getByTestId('performance-test-alarm').first()
    ).not.toContainText('measured 79.6');
    await expect(
      page.locator('[data-kpi="winding"] svg[viewBox="0 0 96 24"]')
    ).toHaveCount(0);
    await expect(page.locator('[data-kpi="winding"]')).not.toContainText('Δ');
    await expect(
      winding.getByTestId('sensor-comparison').first()
    ).toContainText('TEST');
    await expect(
      page
        .locator('[data-section="Electrical"]')
        .getByTestId('performance-test-alarm')
    ).toHaveCount(0);
    const measuredCurves = winding.locator(
      'path.recharts-line-curve:not([stroke-dasharray="6 4"])'
    );
    await expect(measuredCurves.first()).toHaveAttribute('d', /M/);
    const measuredSegments = await measuredCurves.evaluateAll((paths) =>
      paths.map((path) => (path.getAttribute('d')?.match(/M/g) ?? []).length)
    );
    await winding.screenshot({
      path: test.info().outputPath(`test-${status}-winding.png`)
    });
    options.testAlarm = undefined;
    await page.reload();
    await expect(page.locator('[data-kpi="winding"]')).toBeVisible();
    await expect(page.getByTestId('performance-test-alarm')).toHaveCount(0);
    await expect(winding).not.toContainText('TEST Winding 5:');
    await expect(measuredCurves.first()).toHaveAttribute('d', /M/);
    expect(
      await measuredCurves.evaluateAll((paths) =>
        paths.map((path) => (path.getAttribute('d')?.match(/M/g) ?? []).length)
      )
    ).toEqual(measuredSegments);
    await expect(page.locator('[data-kpi="winding"]')).not.toContainText(
      'TEST alarm'
    );
    await expect(
      page.locator('[data-kpi="winding"] [data-kpi-value]')
    ).toHaveText('82');
  });
}

test('real critical readings remain visible beside a test and hidden channels release their test scale', async ({
  page
}) => {
  await mount(page, {
    testAlarm: {
      key: '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5',
      status: 'open'
    },
    signalOverrides: { '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1': 146.2 }
  });
  const family = page.locator('[data-section="Motor winding temperature"]');
  await family.scrollIntoViewIfNeeded();
  await expect(family.getByText(/1 of 10 above critical/)).toBeVisible();
  const tile = page.locator('[data-kpi="winding"]');
  await expect(tile.getByText('Critical', { exact: true })).toBeVisible();
  await expect(tile).toContainText('TEST alarm — Critical');
  await family.getByText('Winding 5', { exact: true }).first().click();
  await expect(family.locator('path[stroke-dasharray="6 4"]')).toHaveCount(0);
  await expect(family.getByTestId('performance-test-alarm')).toBeVisible();
  await family.getByText('Winding 5', { exact: true }).first().click();
  await expect(family.locator('path[stroke-dasharray="6 4"]')).toHaveCount(1);
});

test('compatible unit aliases retain the primary TEST value on a family KPI', async ({
  page
}, info) => {
  const key = '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5';
  await mount(page, {
    specs: SIGNALS.map((signal) =>
      signal.key === key ? { ...signal, unit: '°C' } : signal
    ),
    testAlarm: { key, status: 'open' }
  });
  const tile = page.locator('[data-kpi="winding"]');
  await expect(tile.locator('[data-kpi-value]')).toHaveText('159.5');
  await expect(tile.locator('[data-kpi-measured]')).toContainText('79.6 °C');
  await tile.screenshot({
    path: info.outputPath('compatible-unit-test-kpi.png'),
    animations: 'disabled'
  });
});

test('core-only instrumentation has an accurately named thermal KPI and matching test label', async ({
  page
}) => {
  await mount(page, {
    specs: SIGNALS.filter((signal) => signal.key.includes('MOTOR_CORE_RTD')),
    testAlarm: {
      key: '/dex/PUMP1_MOTOR_CORE_RTD4_PROCESS_VALUE',
      status: 'acknowledged'
    }
  });
  const tile = page.locator('[data-kpi="core"]');
  await expect(tile).toContainText('Stator core temperature');
  await expect(tile).toContainText('TEST alarm — Critical');
  await expect(
    page.getByTestId('performance-test-alarm').first()
  ).toContainText('Stator core 4 (Motor Core RTD 4)');
});

test('coincident test traces share one label and retain both channel notices', async ({
  page
}) => {
  await mount(page, {
    testAlarms: [
      { key: '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1', status: 'open' },
      {
        key: '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5',
        status: 'acknowledged',
        triggeredAt: new Date(NOW - 90000).toISOString()
      }
    ]
  });
  const family = page.locator('[data-section="Motor winding temperature"]');
  await expect(family.getByTestId('performance-test-alarm')).toContainText(
    'Winding 1 (Motor Winding Temperature 1)'
  );
  await expect(family.getByTestId('performance-test-alarm')).toContainText(
    'Winding 5 (Motor Winding Temperature 5)'
  );
  await expect(family.locator('path[stroke-dasharray="6 4"]')).toHaveCount(2);
  await expect(
    family.locator('svg text').filter({ hasText: 'TEST 2 channels: 159.5' })
  ).toHaveCount(1);
});

test('unit mismatches and unusable measurements retain a notice without plotting misleading test history', async ({
  page
}) => {
  const key = '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5';
  await mount(page, {
    specs: [
      {
        key,
        name: 'Motor Winding Temperature 5',
        value: 3276.7,
        unit: 'degC',
        quality: 'bad',
        stale: true,
        limits: { critical_max: 145 }
      }
    ],
    testAlarm: { key, status: 'open', unit: 'rpm' }
  });
  const notice = page
    .locator('[data-section="Motor winding temperature"]')
    .getByTestId('performance-test-alarm');
  await expect(notice).toContainText('measured —');
  await expect(notice).toContainText('Measured observation is stale.');
  await expect(notice).toContainText('Units differ.');
  await expect(
    page.locator('[data-section="Motor winding temperature"]')
  ).toContainText('No measured history in this window.');
  await expect(page.locator('path[stroke-dasharray="6 4"]')).toHaveCount(0);
});

test('a newer exercise explains the history lag and gains a trace after Refresh', async ({
  page
}) => {
  const key = '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5';
  const options: Parameters<typeof mount>[1] = {
    specs: SIGNALS.filter((signal) => signal.key === key),
    historyLagMs: 20_000,
    testAlarm: {
      key,
      status: 'open',
      triggeredAt: new Date(NOW - 10_000).toISOString()
    }
  };
  const requests = await mount(page, options);
  const family = page.locator('[data-section="Motor winding temperature"]');
  await expect(family.getByTestId('performance-test-alarm')).toContainText(
    'Exercise began after this history window.'
  );
  await expect(family.locator('path[stroke-dasharray="6 4"]')).toHaveCount(0);
  const previous = requests.length;
  options.historyLagMs = 0;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect.poll(() => requests.length).toBeGreaterThan(previous);
  await expect(family.getByTestId('performance-test-alarm')).not.toContainText(
    'Exercise began after this history window.'
  );
  await expect(family.locator('path[stroke-dasharray="6 4"]')).toHaveCount(1);
});

test('the sole measured channel cannot be hidden along with its TEST trace', async ({
  page
}, info) => {
  const key = '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5';
  await mount(page, {
    specs: SIGNALS.filter((signal) => signal.key === key),
    testAlarm: { key, status: 'open' }
  });
  const family = page.locator('[data-section="Motor winding temperature"]');
  await family.scrollIntoViewIfNeeded();
  const measured = family.getByRole('checkbox', {
    name: 'Winding 5',
    exact: true
  });
  await expect(measured).toBeChecked();
  await expect(measured).toHaveAccessibleDescription(
    'At least one line must remain visible.'
  );
  await measured.focus();
  await expect(
    page.getByRole('tooltip', {
      name: 'At least one line must remain visible.',
      exact: true
    })
  ).toBeVisible();
  await family.screenshot({
    path: info.outputPath('last-visible-line-explained.png'),
    animations: 'disabled'
  });
  await family.getByText('Winding 5', { exact: true }).first().click();
  await expect(measured).toBeChecked();
  await expect(
    family.getByRole('checkbox', {
      name: 'TEST Winding 5: 159.5 °C',
      exact: true
    })
  ).toBeChecked();
  await expect(family.locator('path[stroke-dasharray="6 4"]')).toHaveCount(1);
});

test('the family names its tested sensor even when no current measurement is usable', async ({
  page
}) => {
  const key = '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5';
  await mount(page, {
    specs: [
      {
        key,
        name: 'Motor Winding Temperature 5',
        value: 3276.7,
        unit: 'degC',
        quality: 'bad',
        stale: true
      }
    ],
    testAlarm: { key, status: 'open' }
  });
  const family = page.locator('[data-section="Motor winding temperature"]');
  await expect(
    family.getByText('TEST alarm — Critical: Winding 5: 159.5 °C', {
      exact: true
    })
  ).toBeVisible();
});

test('the KPI TEST marker shares the measured scale rather than pinning to its top', async ({
  page
}, info) => {
  const key = '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5';
  await mount(page, {
    specs: SIGNALS.filter((signal) => signal.key === key),
    seriesOverrides: { [key]: 200 },
    testAlarm: { key, status: 'open' }
  });
  const tile = page.locator('[data-kpi="winding"]');
  const trace = tile.locator('[data-kpi-test-trace]');
  await expect(trace).toHaveAttribute('d', /M/);
  const coordinates = (await trace.getAttribute('d'))!
    .match(/[\d.]+/g)!
    .map(Number);
  expect(coordinates[1]).toBeGreaterThan(20);
  await expect(tile.locator('svg[viewBox="0 0 96 24"]')).toHaveAccessibleName(
    /Measured trend with a synthetic TEST value 159.5/
  );
  await tile.screenshot({
    path: info.outputPath('test-kpi-correct-scale.png'),
    animations: 'disabled'
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`phone test chart labels fit, remain legible and carry into fullscreen (${scheme})`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mount(page, {
      scheme,
      testAlarm: {
        key: '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5',
        status: 'open'
      }
    });
    const family = page.locator('[data-section="Motor winding temperature"]');
    await family.scrollIntoViewIfNeeded();
    const label = family
      .locator('svg text')
      .filter({ hasText: 'TEST Winding 5: 159.5' });
    await expect(label).toHaveCount(1);
    const labelBox = await label.boundingBox();
    const limitBox = await family
      .locator('svg text')
      .filter({ hasText: 'Critical above 145' })
      .first()
      .boundingBox();
    expect(labelBox).not.toBeNull();
    expect(limitBox).not.toBeNull();
    const overlapWidth =
      Math.min(labelBox!.x + labelBox!.width, limitBox!.x + limitBox!.width) -
      Math.max(labelBox!.x, limitBox!.x);
    const overlapHeight =
      Math.min(labelBox!.y + labelBox!.height, limitBox!.y + limitBox!.height) -
      Math.max(labelBox!.y, limitBox!.y);
    expect(overlapWidth <= 0 || overlapHeight <= 0).toBe(true);
    const title = family
      .getByTestId('performance-test-alarm')
      .getByText('TEST alarm — Critical', { exact: true });
    const contrast = await title.evaluate((element) => {
      const luminance = (color: string) =>
        color
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map(Number)
          .map((channel) => {
            const normalized = channel / 255;
            return normalized <= 0.04045
              ? normalized / 12.92
              : ((normalized + 0.055) / 1.055) ** 2.4;
          })
          .reduce(
            (total, channel, index) =>
              total + channel * [0.2126, 0.7152, 0.0722][index],
            0
          );
      let parent: Element | null = element;
      let background = 'rgb(255, 255, 255)';
      while (parent) {
        const color = getComputedStyle(parent).backgroundColor;
        if (color !== 'transparent' && color !== 'rgba(0, 0, 0, 0)') {
          background = color;
          break;
        }
        parent = parent.parentElement;
      }
      const foreground = luminance(getComputedStyle(element).color);
      const behind = luminance(background);
      return (
        (Math.max(foreground, behind) + 0.05) /
        (Math.min(foreground, behind) + 0.05)
      );
    });
    expect(contrast).toBeGreaterThanOrEqual(4.5);
    await family
      .getByTestId('performance-test-alarm')
      .locator('..')
      .screenshot({
        path: info.outputPath(`review-chart-${scheme}-phone.png`)
      });
    await family
      .getByRole('button', { name: 'Open full screen', exact: true })
      .first()
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.locator('path[stroke-dasharray="6 4"]')).toHaveCount(1);
    await expect(dialog).toContainText('TEST Winding 5: 159.5');
    await expect(dialog.locator('.recharts-wrapper')).toBeInViewport({
      ratio: 1
    });
    await page.mouse.move(0, 0);
    await dialog.screenshot({
      path: info.outputPath(`review-chart-fullscreen-${scheme}-phone.png`),
      animations: 'disabled'
    });
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  });
}

async function expectFactTooltip(target: Locator, name: string | RegExp) {
  await expect(target).toHaveAttribute('aria-describedby', /\S+/);
  const id = await target.getAttribute('aria-describedby');
  expect(id).toBeTruthy();
  // Use the target association to keep lookup local on the large heatmap page.
  const tooltip = target.page().locator(`[id="${id}"]`);
  await expect(tooltip).toHaveAttribute('role', 'tooltip');
  await expect(tooltip).toHaveAccessibleName(name);
  await expect(tooltip).toBeVisible();
}

let pageErrors: string[] = [];
test.beforeEach(({ page }) => {
  pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
});
test.afterEach(() => {
  expect(
    pageErrors,
    'The component harness must not hide runtime errors'
  ).toEqual([]);
});

test('the KPI strip shows fixture values and states what it cannot show', async ({
  page
}) => {
  await mount(page);
  const power = page.locator('[data-kpi="power"]');
  await expect(power).toContainText('8.42');
  await expect(power).toContainText('MW');
  await expect(page.locator('[data-kpi="speed"]')).toContainText('745');
  await expect(
    page.locator('[data-kpi="vibration"]').getByRole('button', {
      name: /no vibration channel with a confirmed role and unit/i
    })
  ).toHaveText('—');
  // Winding 4 is pegged at the over-range marker with quality bad: it is not
  // the highest sensor, and 3276.7 appears nowhere on the tile.
  const winding = page.locator('[data-kpi="winding"]');
  const windingLabel = winding.locator('p').first().locator(':scope > span');
  await expect(windingLabel).toHaveCount(1);
  await expect(windingLabel).toContainText('Highest of 10: Winding 11');
  await windingLabel.hover();
  await expectFactTooltip(windingLabel, /^Highest of 10:/);
  await expect(winding).not.toContainText('3,276.7');
  await expect(winding).toContainText('82');
  await expect(power.locator('time')).toContainText('20 s ago');
  await expect(power.locator('time')).not.toContainText('just now');
  await expect(page.locator('[data-kpi="status"]')).toContainText('Running');
  // The one sensor without a reading is reported as poor quality, so the
  // strip is not calm - and must not claim to be.
  await expect(
    page.getByRole('button', { name: /^2 with poor quality:/ })
  ).toBeVisible();
  await expect(page.getByText('No exceptions', { exact: true })).toHaveCount(0);
  await page.keyboard.press('Tab');
  const coverage = page.getByRole('button', {
    name: 'Exceptions: assessment coverage',
    exact: true
  });
  await coverage.focus();
  await expect(coverage).toBeFocused();
  await expectFactTooltip(coverage, /11 have configured limits/);
  await expect(coverage).toHaveAccessibleDescription(
    /11 have configured limits/
  );
});

test('families retain heatmaps and omit unconfirmed temperature rises', async ({
  page
}) => {
  await mount(page);
  await expect(
    page.getByText('Stator winding temperature', { exact: true })
  ).toBeVisible();
  await expect(page.getByText(/Highest now: Winding 11/)).toBeVisible();
  await expect(page.getByText(/Highest now: Winding 4/)).toHaveCount(0);
  await expect(page.getByText(/Spread .* \(calculated\)/)).toHaveCount(0);
  const winding = page.locator('[data-section="Motor winding temperature"]');
  await winding.scrollIntoViewIfNeeded();
  await expect(
    winding.getByRole('img', { name: 'Sensor heatmap', exact: true })
  ).toBeVisible({ timeout: 20_000 });
  await expect(
    page.getByText('Cooling water rise (outlet − inlet)')
  ).toHaveCount(0);
  await expect(
    page.getByText('Relationship: active power and shaft speed')
  ).toBeVisible();
  // Said twice on purpose: on the KPI tile and as the section's own state.
  await expect(page.locator('[data-kpi="vibration"]')).toContainText('—');
  await expect(
    page.getByText(
      'Unavailable · no vibration channel with a confirmed role and unit',
      { exact: true }
    )
  ).toHaveCount(1);
  // Absent families stay out of navigation; their mounted fallback must still explain why.
  await expect(
    page.locator('[data-section="Vibration and mechanical"]')
  ).toContainText(
    'Channels without a confirmed engineering role and unit cannot be used for vibration comparison.'
  );
  await expect(
    page.getByText(/Unclassified tags remain in Other signals/)
  ).toHaveCount(0);
  // Unusable current readings are named beside their quality; the tooltip
  // explains why they have no plotted point.
  const comparison = winding.getByTestId('sensor-comparison');
  const omitted = comparison
    .getByText('Winding 4', { exact: true })
    .locator('..');
  await expect(omitted).toBeVisible();
  const quality = omitted.getByRole('button', {
    name: 'Bad quality',
    exact: true
  });
  await page.keyboard.press('Tab');
  await quality.scrollIntoViewIfNeeded();
  await expect(quality).toBeInViewport();
  await quality.focus();
  await expect(quality).toBeFocused();
  await expectFactTooltip(quality, 'No usable current reading; not plotted');
  await expect(quality).toHaveAccessibleDescription(
    'No usable current reading; not plotted'
  );
  const plottedSensors = comparison
    .locator('svg text title')
    .filter({ hasText: /^Winding \d+$/ });
  await expect(plottedSensors).toHaveCount(10);
  await expect(plottedSensors).toHaveText(
    [11, 10, 9, 8, 7, 6, 5, 3, 2, 1].map((number) => `Winding ${number}`)
  );
  await expect(plottedSensors.filter({ hasText: /^Winding 4$/ })).toHaveCount(
    0
  );
  const healthyCells = winding.locator(
    '[data-heatmap-cell][data-limit-state="normal"]'
  );
  expect(await healthyCells.count()).toBeGreaterThan(0);
  const fills = await healthyCells.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute('fill'))
  );
  expect(
    fills.every(
      (fill) => fill !== null && fill.trim() !== '' && fill.trim() !== 'none'
    )
  ).toBe(true);
  expect(new Set(fills).size).toBe(1);
});

test('the time range control changes what is asked of the server', async ({
  page
}) => {
  const requests = await mount(page);
  // An hour is 720 snapshots, past the complete-read bound, so the default
  // window is sampled; fifteen minutes is read whole.
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Sampled: one reading every'
  );
  await page.mouse.move(0, 0);
  await page.getByText('15 min', { exact: true }).click();
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Every reading in the window'
  );
  await page.mouse.move(0, 0);
  const before = requests.length;
  await page.getByText('6 h', { exact: true }).click();
  await expect.poll(() => requests.length).toBeGreaterThan(before);
  const last = requests[requests.length - 1];
  const span =
    Date.parse(last.searchParams.get('to')!) -
    Date.parse(last.searchParams.get('from')!);
  expect(Math.round(span / 3600_000)).toBe(6);
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Sampled: one reading every'
  );
  await page.mouse.move(0, 0);
});

test('live can be paused and says so', async ({ page }) => {
  await mount(page);
  const live = page.getByRole('switch', {
    name: 'Live updates: Live',
    exact: true
  });
  await expect(live).toHaveCount(1);
  await expect(live).toBeChecked();
  await expect(page.getByText('Live', { exact: true })).toHaveCount(1);
  // Mantine's track label sits over the input; the click must go through it.
  await live.click({ force: true });
  const paused = page.getByRole('switch', {
    name: 'Live updates: Paused',
    exact: true
  });
  await expect(paused).not.toBeChecked();
  await expect(page.getByText('Paused', { exact: true })).toHaveCount(1);
});

test('a failed history read keeps the current values and says the charts are empty', async ({
  page
}) => {
  await mount(page, { fail: true });
  await expect(page.locator('[data-kpi="power"]')).toContainText('8.42');
  await expect(
    page.getByText('The source could not serve this window')
  ).toBeVisible();
  await expect(
    page.getByText(`${SIGNALS.length} without history in this window`)
  ).toBeVisible();
});

test('dragging across a chart offers a zoom, and taking it narrows the window', async ({
  page
}) => {
  const requests = await mount(page);
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Sampled: one reading every'
  );
  await page.mouse.move(0, 0);
  const chart = page
    .locator('[data-section="Electrical"] .recharts-wrapper')
    .first();
  await expect(chart.locator('.recharts-line-curve').first()).toBeVisible();
  await expect(chart).toBeVisible();
  // Raw mouse events do not scroll: the chart must be inside the viewport.
  await chart.scrollIntoViewIfNeeded();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
  await page.screenshot({ path: test.info().outputPath('drag-before.png') });
  const box = (await chart.boundingBox())!;
  // Drag across the middle third of the plot area.
  const y = box.y + box.height * 0.45;
  await page.mouse.move(box.x + box.width * 0.35, y);
  // Recharts computes its active axis value on the next animation frame.
  // Wait for that real hover state before pressing, rather than racing it.
  await expect(chart.locator('.recharts-tooltip-wrapper')).toBeVisible();
  await expect(chart.locator('.recharts-tooltip-wrapper')).toContainText(
    'Active power'
  );
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45, y, { steps: 4 });
  await page.mouse.move(box.x + box.width * 0.6, y, { steps: 4 });
  await page.mouse.up();
  await page.screenshot({ path: test.info().outputPath('drag-after.png') });
  const zoom = page.getByRole('button', { name: /^Zoom to / });
  await expect(zoom).toBeVisible();
  const before = requests.length;
  await zoom.click();
  await expect.poll(() => requests.length).toBeGreaterThan(before);
  const last = requests[requests.length - 1];
  const span =
    Date.parse(last.searchParams.get('to')!) -
    Date.parse(last.searchParams.get('from')!);
  expect(span).toBeLessThan(3600_000 * 0.5);
  expect(span).toBeGreaterThan(60_000);
  await expect(page.getByText(/^Zoomed /)).toBeVisible();
  // Zooming pauses live: a fixed span cannot follow the clock.
  await expect(page.getByText('Paused', { exact: true })).toBeVisible();
});

test('a reading exactly on its limit is within limits, as the server classifies it', async ({
  page
}) => {
  // MachineSignalBinding.classify uses strict inequalities: warn_max is the
  // last acceptable reading, not the first unacceptable one - the winding
  // figure of 125 degC is "the highest reading the machine is designed to
  // produce". The page used `>=` on the same bounds, so a sensor sitting
  // exactly on its setpoint was counted over a limit here while the detector
  // called it normal, a disagreement an operator cannot resolve on screen.
  // Ten of the eleven windings count: the eleventh is pegged and bad quality.
  await mount(page, {
    signalOverrides: { '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1': 125 }
  });

  const winding = page.locator('[data-section="Motor winding temperature"]');
  await expect(winding.getByText('10 within limits')).toBeVisible();
  await expect(winding.getByText(/over a limit/)).toHaveCount(0);
});

test('a reading past its limit is counted over it', async ({ page }) => {
  await mount(page, {
    signalOverrides: { '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1': 125.1 }
  });

  const winding = page.locator('[data-section="Motor winding temperature"]');
  await expect(
    winding.getByText('1 of 10 above warning (Warning above 125 °C)')
  ).toBeVisible();
});
