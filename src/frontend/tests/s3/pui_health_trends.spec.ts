import { type Locator, type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import {
  HEALTH_SIGNALS,
  NOW,
  openHealth,
  signalTable,
  sparkline,
  trendCard
} from './healthFixtures';
import { informationTooltip } from './presentationAssertions';

nativeBaseline();
test.use({ timezoneId: 'UTC' });

/**
 * P09: the Health row graphs and full chart keep their native form while
 * every reading sits at its own time, unreadable readings and silences stay
 * open, and missing, failed, unavailable and recorded-zero records stay
 * distinct (R6 A05.3, A05.5, A05.9, A23.2; screens 10, 12, 13, 14).
 */

/** x coordinates of each subpath of an SVG path, in order. */
function subpaths(d: string): number[][] {
  return [...d.matchAll(/M([^M]+)/g)].map((match) =>
    [...match[1].matchAll(/(-?[\d.]+),(-?[\d.]+)/g)].map((point) =>
      Number(point[1])
    )
  );
}

async function rowGraphPath(page: Page, name: string) {
  if (!(await sparkline(page, name).isVisible())) {
    const details = signalTable(page).getByRole('button', {
      name: `Details for ${name}`,
      exact: true
    });
    if ((await details.getAttribute('aria-expanded')) === 'false')
      await details.click();
  }
  const d = await sparkline(page, name).locator('path').getAttribute('d');
  expect(d).toBeTruthy();
  return d!;
}

async function chooseParameter(page: Page, label: string) {
  await trendCard(page).getByRole('combobox', { name: 'Parameter' }).click();
  await page.getByRole('option', { name: label, exact: true }).click();
}

async function rowHistoryInformation(page: Page, row: Locator, label: string) {
  const details = row.getByRole('button', { name: /^Details for/ });
  const trend = row.locator('td[data-label="Trend"]');
  if (!(await trend.isVisible())) await details.click();
  await expect(trend).toBeVisible();
  await expect(trend).toContainText(
    label === 'Read failed' ? 'Read failed' : '—'
  );
  await expect(trend.getByRole('button')).toHaveCount(0);
  await page.mouse.move(0, 0);
  await row.getByRole('button', { name: /^Inspect signal:/ }).focus();
  await page.keyboard.press('Shift+Tab');
  return informationTooltip(page, details, `Trend: ${label}`);
}

const curve = (page: Page) => trendCard(page).locator('.recharts-area-curve');

/** Each x-axis tick's position and the instant its label names (UTC). */
async function axisTicks(card: Locator) {
  const labels = card.locator(
    '.recharts-xAxis-tick-labels text.recharts-cartesian-axis-tick-value'
  );
  // Expanding remounts the responsive chart inside the modal. evaluateAll
  // does not wait for its first layout or for the axis labels to render.
  await expect(labels.nth(1)).toBeVisible();
  const ticks = await labels.evaluateAll((nodes) =>
    nodes.map((node) => ({
      x: Number(node.getAttribute('x')),
      label: node.textContent ?? ''
    }))
  );
  return ticks.map(({ x, label }) => {
    // Ticks are whole minutes: "08:52 AM".
    const match = label.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?/);
    expect(match, `tick label ${label}`).toBeTruthy();
    let hours = Number(match![1]);
    if (match![4] === 'PM' && hours < 12) hours += 12;
    if (match![4] === 'AM' && hours === 12) hours = 0;
    const day = new Date(NOW);
    day.setUTCHours(hours, Number(match![2]), Number(match![3] ?? 0), 0);
    return { x, at: day.getTime() };
  });
}

test('row graphs place readings by time and break at silences and unreadable readings', async ({
  page
}) => {
  await openHealth(page);
  const flow = subpaths(await rowGraphPath(page, 'Discharge flow'));
  // Ten-minute window: readings for three minutes with one unreadable at
  // 100 s, silence from 180 s to 300 s, then readings to the end.
  expect(flow).toHaveLength(3);
  expect(flow[0][0]).toBe(0);
  expect(flow[0].at(-1)).toBe(19);
  expect(flow[1][0]).toBe(21);
  expect(flow[1].at(-1)).toBe(36);
  // Five minutes into ten is the middle of 120 px; index spacing put it at
  // 37 of 97 points (about 46 px) and joined it to the reading at 180 s.
  expect(flow[2][0]).toBe(60);
  expect(flow[2].at(-1)).toBe(120);
  // The native form is unchanged around the corrected path.
  const graph = sparkline(page, 'Discharge flow');
  await expect(graph).toHaveAttribute('width', '120');
  await expect(graph).toHaveAttribute('height', '28');
  await expect(graph.locator('path')).toHaveAttribute('stroke-width', '1.5');
});

test('the full chart keeps the silence open on a proportional time axis', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await openHealth(page, { scheme, nativeTheme: true, compactChrome: true });
    const card = trendCard(page);
    await expect(curve(page)).toHaveCount(1);
    await expect(card.getByText('98 samples', { exact: true })).toHaveCount(0);
    const line = subpaths((await curve(page).getAttribute('d'))!);
    // Broken after the unreadable reading and across the silence.
    expect(line).toHaveLength(3);

    // Recharts nudges the final label inward to fit, so the mapping comes
    // from the first two ticks and only interior ticks are compared.
    const ticks = (await axisTicks(card)).slice(0, -1);
    expect(ticks.length).toBeGreaterThanOrEqual(2);
    const [first, second] = ticks;
    const pxPerMs = (second.x - first.x) / (second.at - first.at);
    const xAt = (ms: number) => first.x + (ms - first.at) * pxPerMs;
    // Ticks are evenly spaced in time, so the axis is proportional.
    for (const tick of ticks) {
      expect(Math.abs(tick.x - xAt(tick.at))).toBeLessThan(1.5);
    }
    // The line spans the whole selected window.
    expect(Math.abs(line[0][0] - xAt(NOW - 600_000))).toBeLessThan(1.5);
    expect(Math.abs(line[2].at(-1)! - xAt(NOW))).toBeLessThan(1.5);
    // The second run ends at 08:53:00 and the third starts at 08:55:00.
    expect(Math.abs(line[1].at(-1)! - xAt(NOW - 420_000))).toBeLessThan(1.5);
    expect(Math.abs(line[2][0] - xAt(NOW - 300_000))).toBeLessThan(1.5);

    const gaps = card
      .getByRole('alert')
      .filter({ hasText: 'Gaps in this record' });
    await expect(gaps).toContainText(
      'Gaps longer than 15 seconds: 1. Based on the measured 5-second interval.'
    );
    await expect(gaps).toContainText('Readings that are not numbers: 1 of 98.');
    await expect(gaps).toContainText('Nothing is filled in');
    await expect(card.getByText(/^Selected window:/)).toHaveCount(0);
    await card
      .getByRole('button', { name: 'Expand chart', exact: true })
      .click();
    const expanded = page.getByRole('dialog', {
      name: 'Expanded signal trend',
      exact: true
    });
    await expect(expanded).toBeVisible();
    await expect(expanded.getByText(/Times: your clock/)).toHaveCount(0);
    const expandedTicks = await axisTicks(expanded);
    expect(expandedTicks.length).toBeGreaterThanOrEqual(2);
    for (const tick of expandedTicks) {
      expect(tick.at).toBeGreaterThanOrEqual(NOW - 600_000);
      expect(tick.at).toBeLessThanOrEqual(NOW);
    }
    const expandedCurve = expanded.locator('.recharts-area-curve');
    await expect(expandedCurve).toHaveCount(1);
    await expect(expandedCurve).toHaveAttribute('d', /^M/);
    expect(subpaths((await expandedCurve.getAttribute('d'))!)).toHaveLength(3);
    await expanded.screenshot({
      path: testInfo.outputPath(`expanded-clock-${scheme}.png`)
    });
    await expanded
      .getByRole('button', { name: 'Close expanded signal trend', exact: true })
      .click();
    // The controls stay inside the card at every width.
    const cardBox = (await card.boundingBox())!;
    const parameterBox = (await card
      .getByRole('combobox', { name: 'Parameter' })
      .boundingBox())!;
    expect(parameterBox.x + parameterBox.width).toBeLessThanOrEqual(
      cardBox.x + cardBox.width
    );
    // The native blue area and controls are kept.
    await expect(curve(page)).toHaveAttribute('stroke', /blue-6/);
    await expect(card.getByRole('combobox', { name: 'Range' })).toHaveValue(
      '10 minutes'
    );
    await card.screenshot({
      path: testInfo.outputPath(`chart-silence-${scheme}.png`),
      animations: 'disabled'
    });
  }
});

test('recorded zero, empty read, no source history and a missing row stay distinct', async ({
  page
}, testInfo) => {
  await openHealth(page);
  const table = signalTable(page);
  // A recorded zero is a drawn line along the floor, not a gap.
  expect(subpaths(await rowGraphPath(page, 'Bypass flow'))).toHaveLength(1);
  const row = (name: string) =>
    table.getByRole('row').filter({ hasText: name });
  for (const [name, reason] of [
    ['Seal flush flow', 'No samples in window'],
    ['Cooling water flow', 'History unavailable'],
    ['Winding temperature', 'Not returned']
  ]) {
    const reading = row(name);
    await rowHistoryInformation(page, reading, reason);
  }

  await chooseParameter(page, 'Bypass flow (m³/h)');
  await expect(curve(page)).toHaveCount(1);
  await chooseParameter(page, 'Seal flush flow (l/min)');
  await expect(trendCard(page)).toContainText('No samples in this window');
  await expect(trendCard(page)).toContainText('and from a recorded zero');
  await chooseParameter(page, 'Cooling water flow (m³/h)');
  await expect(trendCard(page)).toContainText('No history for this signal');
  await expect(trendCard(page)).toContainText('no configured connector');
  await table.screenshot({
    path: testInfo.outputPath('table-states.png'),
    animations: 'disabled'
  });
});

test('source outages, timeouts, unsupported history and missing batch rows have reachable distinct reasons', async ({
  page
}, info) => {
  const reasons = [
    'SOURCE_UNAVAILABLE',
    'SOURCE_TIMEOUT',
    'HISTORY_UNSUPPORTED',
    'NO_CONNECTOR'
  ] as const;
  const signals = reasons.map((reason, index) => ({
    ...HEALTH_SIGNALS[0],
    id: 301 + index,
    name: reason,
    trend: { reason }
  }));
  await openHealth(page, {
    recordedTelemetryAsLive: false,
    signals: [
      ...signals,
      {
        ...HEALTH_SIGNALS[0],
        id: 305,
        name: 'Missing batch row',
        trend: 'missing'
      }
    ]
  });
  const table = signalTable(page);
  for (const [index, reason] of reasons.entries()) {
    const row = table.getByRole('row').filter({ hasText: reason });
    const label =
      index < 2
        ? 'Read failed'
        : index === 2
          ? 'No history'
          : 'History unavailable';
    const information = await rowHistoryInformation(page, row, label);
    await expect(information).toContainText(
      reason === 'SOURCE_TIMEOUT'
        ? 'did not answer within the read timeout'
        : reason === 'HISTORY_UNSUPPORTED'
          ? 'does not support historical windows'
          : reason === 'NO_CONNECTOR'
            ? 'no configured connector'
            : 'source could not be reached'
    );
    await chooseParameter(page, `${reason} (cusec)`);
    await expect(trendCard(page)).toContainText(
      index < 2 ? 'Could not read the source' : 'No history for this signal'
    );
    if (reason === 'SOURCE_TIMEOUT')
      await expect(trendCard(page)).toContainText(
        'did not answer within the read timeout'
      );
    if (reason === 'HISTORY_UNSUPPORTED')
      await expect(trendCard(page)).toContainText(
        'does not support historical windows'
      );
    if (index < 2)
      await expect(trendCard(page)).not.toContainText(
        'No history for this signal'
      );
  }
  await rowHistoryInformation(
    page,
    table.getByRole('row').filter({ hasText: 'Missing batch row' }),
    'Not returned'
  );
});

test('invalid readings stay distinct from a sparse record with truthful drawable counts', async ({
  page
}, info) => {
  await openHealth(page, {
    signals: [
      { ...HEALTH_SIGNALS[0], name: 'All invalid', trend: 'bad' },
      { ...HEALTH_SIGNALS[1], name: 'One drawable', trend: 'mostly-bad' }
    ]
  });
  const table = signalTable(page);
  await rowHistoryInformation(
    page,
    table.getByRole('row').filter({ hasText: 'All invalid' }),
    'No drawable readings'
  );
  await rowHistoryInformation(
    page,
    table.getByRole('row').filter({ hasText: 'One drawable' }),
    '1 of 121 drawable'
  );
  await expect(trendCard(page)).toContainText('No drawable readings');
  await expect(curve(page)).toHaveCount(0);
  await expect(trendCard(page)).not.toContainText('Each is drawn as a break');
  await chooseParameter(page, 'One drawable (m³/h)');
  await expect(trendCard(page)).toContainText('Only one drawable reading');
  await expect(trendCard(page)).toContainText('1 of 121 readings can be drawn');
  await expect(trendCard(page)).not.toContainText('Each is drawn as a break');
  await trendCard(page).screenshot({
    path: info.outputPath('invalid-readings.png')
  });
});

test('sparse and flapping records keep long silences open in row and full graphs', async ({
  page
}) => {
  for (const [shape, breaks] of [
    ['sparse', 1],
    ['flapping', 3]
  ] as const) {
    await openHealth(page, {
      signals: [{ ...HEALTH_SIGNALS[0], trend: shape }]
    });
    expect(subpaths(await rowGraphPath(page, 'Discharge flow'))).toHaveLength(
      breaks + 1
    );
    await expect(trendCard(page)).toContainText(
      `Gaps longer than 15 seconds: ${breaks}. Based on the measured 5-second interval.`
    );
    await expect(curve(page)).toHaveCount(1);
    expect(subpaths((await curve(page).getAttribute('d'))!)).toHaveLength(
      breaks + 1
    );
  }
});

test('an unmeasured interval names the server default and the actual break threshold', async ({
  page
}) => {
  await openHealth(page, {
    signals: [{ ...HEALTH_SIGNALS[0], trend: 'two-readings' }]
  });
  await expect(trendCard(page)).toContainText(
    'Gaps longer than 15 seconds: 1. Based on the expected 5-second interval (server default).'
  );
  await expect(trendCard(page)).not.toContainText("source's 5-second cadence");
});

test('one close pair among minute readings keeps row and expanded charts continuous', async ({
  page
}, info) => {
  await openHealth(page, {
    signals: [{ ...HEALTH_SIGNALS[0], trend: 'close-pair' }]
  });
  expect(subpaths(await rowGraphPath(page, 'Discharge flow'))).toHaveLength(1);
  await expect(curve(page)).toHaveCount(1);
  expect(subpaths((await curve(page).getAttribute('d'))!)).toHaveLength(1);
  await page.getByRole('button', { name: 'Expand chart', exact: true }).click();
  const modal = page.getByRole('dialog', { name: 'Expanded signal trend' });
  await expect(modal.locator('.recharts-area-curve')).toHaveCount(1);
  expect(
    subpaths((await modal.locator('.recharts-area-curve').getAttribute('d'))!)
  ).toHaveLength(1);
  await modal.screenshot({
    path: info.outputPath('close-pair-continuous.png')
  });
});

test('a failed read says it failed, in the rows and in the chart', async ({
  page
}) => {
  await openHealth(page, { batchStatus: 503, chartStatus: 503 });
  const drawable = HEALTH_SIGNALS.length;
  await expect(
    signalTable(page).getByText('Read failed', { exact: true })
  ).toHaveCount(drawable);
  await expect(signalTable(page).getByText('No samples in window')).toHaveCount(
    0
  );
  await expect(trendCard(page)).toContainText('Could not load the trend');
  // Current readings are kept beside the failure.
  await expect(
    signalTable(page).getByRole('cell', { name: '0 m³/h', exact: true })
  ).toBeVisible();
});

test('a raw unitless vibration channel keeps its own units', async ({
  page
}) => {
  await openHealth(page);
  await chooseParameter(page, 'Motor DE vibration (raw)');
  await expect(curve(page)).toHaveCount(1);
  await expect(page.getByText(/mm\/s/)).toHaveCount(0);
  const raw = signalTable(page)
    .getByRole('row')
    .filter({
      has: page.getByRole('button', {
        name: 'Inspect signal: Motor DE vibration (raw)',
        exact: true
      })
    });
  await expect(
    raw.getByRole('cell', {
      name: '1834 Unit not reported by the plant',
      exact: true
    })
  ).toBeVisible();
  const details = raw.getByRole('button', {
    name: 'Details for Motor DE vibration (raw)',
    exact: true
  });
  const inspect = raw.getByRole('button', {
    name: 'Inspect signal: Motor DE vibration (raw)',
    exact: true
  });
  await details.scrollIntoViewIfNeeded();
  await inspect.focus();
  await inspect.press('Shift+Tab');
  await expect(details).toBeFocused();
  await informationTooltip(page, details, 'Unit not reported by the plant');
  await details.press('Tab');
  await expect(inspect).toBeFocused();
  await expect(
    trendCard(page).getByText('unit not recorded', { exact: true })
  ).toHaveCount(0);
  await expect(
    trendCard(page).locator('.recharts-cartesian-axis-tick-value')
  ).not.toContainText(['unit not recorded']);
});

test('a server that states no cadence breaks only at unreadable readings, and says so', async ({
  page
}) => {
  await openHealth(page, { cadence: false });
  expect(subpaths(await rowGraphPath(page, 'Discharge flow'))).toHaveLength(2);
  await expect(trendCard(page)).toContainText(
    'This source did not state its sampling interval'
  );
});
