import { type Locator, type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mimicEntry } from './navigation';

nativeBaseline();

async function keyboardFocus(page: Page, target: Locator) {
  await page.keyboard.press('Tab');
  await target.focus();
  await expect(target).toBeFocused();
}

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
    recorded_telemetry_as_live: false,
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
          pointer: '/dex/PUMP{pump_number}_POWER',
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
    points: unit
      ? {
          [`/dex/PUMP${unit.slice(1)}_POWER`]: point(
            `/dex/PUMP${unit.slice(1)}_POWER`,
            7.5
          )
        }
      : {},
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

test('O27 station readings use display freshness while unavailable states stay explicit', async ({
  page
}, info) => {
  await page.clock.install({ time: new Date('2026-09-13T12:00:02Z') });
  let asLive = true;
  let enabled = true;
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: {
        ...fixture(
          new URL(route.request().url()).searchParams.get('unit'),
          false,
          enabled
        ),
        recorded_telemetry_as_live: asLive,
        display_shifted: true,
        display_shift_seconds: 400 * 86400,
        reporting: true,
        polling_enabled: false,
        last_observed_at: '2026-09-13T12:00:00Z',
        last_poll_at: '2025-08-01T00:00:00Z'
      }
    })
  );
  await page.goto(mimicEntry('/'));
  const information = page.getByRole('button', {
    name: 'Schematic awaiting plant review',
    exact: true
  });
  const provenanceTooltip = page.getByRole('tooltip').filter({
    hasText: 'Reference schematic · physical arrangement unverified.'
  });
  await expect(page.locator('time')).toHaveAttribute(
    'datetime',
    '2026-09-13T12:00:00.000Z'
  );
  await keyboardFocus(page, information);
  await expect(provenanceTooltip).toContainText('Last reading:');
  await expect(provenanceTooltip).not.toContainText('Last poll:');
  await expect(page.getByText('Live', { exact: true })).toBeVisible();
  await expect(
    page.getByText(/recorded history|Times moved .*onto the display clock/i)
  ).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath('o27-station-on.png'),
    fullPage: true
  });

  asLive = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.locator('time')).toHaveAttribute(
    'datetime',
    '2025-08-01T00:00:00.000Z'
  );
  await keyboardFocus(page, information);
  await expect(provenanceTooltip).toContainText('Last poll:');
  await expect(provenanceTooltip).not.toContainText('Last reading:');
  await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath('o27-station-off.png'),
    fullPage: true
  });

  asLive = true;
  enabled = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(
      'Readings are unavailable for this station. Check station activation and source configuration.',
      { exact: true }
    )
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath('o27-station-disabled-on.png'),
    fullPage: true
  });

  asLive = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(
      'Polling is disabled for this station. Live values are unavailable.',
      { exact: true }
    )
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath('o27-station-disabled-off.png'),
    fullPage: true
  });
});

test('sparse bays bind the selected pointer and never invent totals or thresholds', async ({
  page
}) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: fixture(new URL(route.request().url()).searchParams.get('unit'))
    })
  );
  await page.goto(mimicEntry('/'));
  await page.getByRole('button', { name: 'P17: Running', exact: true }).click();
  await expect(
    page.locator('[data-reading][data-point="/dex/PUMP17_POWER"]')
  ).toContainText('7.5 MW');
  const missingPower = page.getByLabel('Plant power: Missing contributors', {
    exact: true
  });
  await expect(missingPower).toHaveText('—');
  await keyboardFocus(page, missingPower);
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'Missing contributors' })
  ).toContainText('Missing contributors');
  await page.getByRole('button', { name: 'Motor', exact: false }).click();
  const condition = page
    .locator('tr[data-point="/dex/PUMP17_POWER"]')
    .getByLabel('No threshold configured', { exact: true });
  await expect(condition).toHaveText('—');
  await keyboardFocus(page, condition);
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'No threshold configured' })
  ).toHaveText('No threshold configured');
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
  await page.goto(mimicEntry('/'));
  await page.getByRole('button', { name: 'P17: Stale', exact: true }).click();
  await expect(
    page.locator('[data-reading][data-point="/dex/PUMP17_POWER"]')
  ).toContainText('—');
  await expect(
    page.locator(
      '[data-reading][data-point="/dex/PUMP17_POWER"] [data-value] [aria-label]'
    )
  ).toHaveAttribute('aria-label', /Stale/);
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
  await page.goto(mimicEntry('/'));
  await page.getByRole('button', { name: 'P17: Running', exact: true }).click();
  await expect(
    page.locator('[data-reading][data-point="/dex/PUMP17_POWER"]')
  ).toContainText('7.5 MW');
  failed = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByText(
      'Live readings are unavailable. Refresh to retry; cached values are hidden.',
      { exact: true }
    )
  ).toBeVisible();
  await expect(
    page.locator('[data-reading][data-point="/dex/PUMP17_POWER"]')
  ).toHaveCount(0);
  await page.locator('#root').evaluate((node) => {
    node.style.display = 'none';
  });
  await page.waitForTimeout(250);
  const before = requests;
  await page.waitForTimeout(5500);
  expect(requests).toBe(before);
});

/** A station whose bays come back in the server's text order. */
function numbered(unit: string | null, stationStatus = 'R') {
  const reading = (
    pointer: string,
    label: string,
    group: string,
    value: number | string,
    unit_: string
  ) => ({
    pointer,
    label,
    group,
    value,
    unit: unit_,
    quality: 'good',
    observed_at: '2026-09-13T12:00:00Z',
    age_seconds: 2,
    unchanged_for_seconds: null,
    reason: null,
    condition: 'unknown',
    thresholds_configured: false
  });
  const status = (key: string, code: string) =>
    reading(
      `/pd/${key}/st`,
      'Equipment Status',
      'Equipment Status Group',
      code,
      ''
    );
  const bays = [
    { key: 'P1', state: 'running', code: 'R' },
    { key: 'P10', state: 'idle', code: 'I' },
    { key: 'P2', state: 'unknown', code: 'Z' }
  ];
  return {
    ...fixture(unit),
    layout: {
      version: 1,
      review_status: 'provisional',
      status_values: { running: ['R'], idle: ['I'], fault: [] },
      bays: { x: 240, y: 70, width: 590, height: 134 },
      elements: [
        {
          id: 'station-status',
          pointer: '/st',
          view: 'station',
          role: 'status',
          label: 'Station status',
          x: 842,
          y: 96
        },
        {
          id: 'pump-status',
          pointer: '/pd/{pump}/st',
          view: 'unit',
          role: 'status',
          label: 'Pump status',
          x: 492,
          y: 56
        }
      ]
    },
    station_points: {
      '/st': reading(
        '/st',
        'Equipment Status',
        'Equipment Status Group',
        stationStatus,
        ''
      )
    },
    bays: bays.map(({ key, state, code }) => ({
      key,
      machine: Number(key.slice(1)),
      name: `Fixture station / Pump ${key.slice(1)}`,
      active: true,
      state,
      points: { [`/pd/${key}/st`]: status(key, code) }
    })),
    points: unit
      ? {
          [`/pd/${unit}/st`]: status(
            unit,
            bays.find((bay) => bay.key === unit)?.code ?? 'Z'
          ),
          '/w1': reading('/w1', 'Winding 1', 'Electric Motor', 30, 'degC'),
          '/w2': reading('/w2', 'Winding 2', 'Electric Motor', 50.5, 'degC'),
          '/w3': reading('/w3', 'Winding 3', 'Electric Motor', 41, 'degC'),
          '/speed': reading(
            '/speed',
            'Shaft speed',
            'Electric Motor',
            745,
            'rpm'
          )
        }
      : {}
  };
}

test('bays are drawn in the order they are numbered, each in its state', async ({
  page
}) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: numbered(new URL(route.request().url()).searchParams.get('unit'))
    })
  );
  await page.goto(mimicEntry('/'));
  const bays = page.locator('g[data-bay]');
  await expect(bays).toHaveCount(3);
  // The server sorts the keys as text - P1, P10, P2. A row of pumps is not.
  expect(
    await bays.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('data-bay'))
    )
  ).toEqual(['P1', 'P2', 'P10']);

  await expect(page.locator('[data-legend="running"]')).toHaveText('Running');
  await expect(page.locator('[data-legend="idle"]')).toHaveText('Idle');
  // A state nobody can read is counted as unknown, never as idle.
  await expect(page.locator('[data-legend="unknown"]')).toHaveText('Unknown');

  // Choosing a bay is done on the drawing, and undone the same way.
  const second = page.getByRole('button', { name: 'P2: Unknown', exact: true });
  await second.click();
  await expect(second).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('Pump unit: P2')).toBeVisible();
  await second.click();
  await expect(second).toHaveAttribute('aria-pressed', 'false');
  await expect(
    page.getByRole('heading', { name: 'Pump unit: P2', exact: true })
  ).toHaveCount(0);
  await expect(page.locator('[data-bay][aria-pressed="true"]')).toHaveCount(0);
});

test('a status code is given in words, and an unknown code as it came', async ({
  page
}) => {
  let code = 'R';
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: numbered(
        new URL(route.request().url()).searchParams.get('unit'),
        code
      )
    })
  );
  await page.goto(mimicEntry('/'));
  const value = page.locator('[data-point="/st"] [data-reading-value]');
  await expect(value).toHaveText('Running');

  // Nothing guesses at a code the layout has not been told the meaning of.
  code = 'Q';
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(value).toHaveText('Q');

  // The pump's own tag, its summary line and its table agree with the words.
  await page.getByRole('button', { name: 'P10: Idle', exact: true }).click();
  await expect(page.locator('[data-unit-header]')).toContainText('Idle');
  const group = page.getByRole('button', { name: 'Equipment Status Group' });
  await keyboardFocus(page, group);
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'Equipment Status: Idle' })
  ).toContainText('Equipment Status: Idle');
  await group.click();
  const row = page.locator('tr[data-point="/pd/P10/st"]');
  await expect(row).toContainText('Idle');
  const source = row.getByRole('button', {
    name: 'Idle · Source code: I',
    exact: true
  });
  await keyboardFocus(page, source);
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'Source code: I' })
  ).toHaveText('Source code: I');
});

test('a system is one line until it is opened', async ({ page }) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: numbered(new URL(route.request().url()).searchParams.get('unit'))
    })
  );
  await page.goto(mimicEntry('/'));
  await page.getByRole('button', { name: 'P1: Running', exact: true }).click();

  const motor = page.getByRole('button', { name: 'Electric Motor' });
  // A family is a span; a lone reading is named. Neither is ranked against
  // the other, and nothing is called normal without a limit to be normal by.
  await keyboardFocus(page, motor);
  await expect(
    page.getByRole('tooltip').filter({ hasText: '3 readings: 30 – 50.5 °C' })
  ).toContainText('3 readings: 30 – 50.5 °C');
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'Shaft speed: 745 rpm' })
  ).toContainText('Shaft speed: 745 rpm');
  await expect(motor).not.toContainText('Normal');
  await expect(page.locator('tr[data-point="/w1"]')).toHaveCount(0);

  await motor.click();
  await expect(page.locator('tr[data-point="/w1"]')).toContainText('30 °C');
  await expect(
    page.locator('tr[data-point="/w1"] td').nth(1).getByRole('button')
  ).toHaveCount(0);
  const condition = page
    .locator('tr[data-point="/w1"]')
    .getByLabel('No threshold configured', { exact: true });
  await expect(condition).toHaveText('—');
  await keyboardFocus(page, condition);
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'No threshold configured' })
  ).toHaveText('No threshold configured');
  await expect(page.locator('tr[data-point="/w2"]')).toContainText('50.5 °C');
});

test('a reading that cannot be shown says why, on the drawing', async ({
  page
}) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: fixture(
        new URL(route.request().url()).searchParams.get('unit'),
        true
      )
    })
  );
  await page.goto(mimicEntry('/'));
  await page.getByRole('button', { name: 'P17: Stale', exact: true }).click();
  const tag = page.locator('[data-reading][data-point="/dex/PUMP17_POWER"]');
  const missing = tag.locator('[data-value] [aria-label]');
  await expect(missing).toHaveText('—');
  await expect(missing).toHaveAttribute('aria-label', /Stale/);
  await keyboardFocus(page, missing);
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'Stale' })
  ).toContainText('Stale');
  await expect(missing).toHaveAttribute('aria-label', /15:00:00/);
  await expect(tag).not.toContainText('7.5');
});

/** A pump with parts: a motor with a limit, a bearing, and one it lacks. */
function withParts(unit: string | null, winding = 61) {
  const base = numbered(unit);
  const reading = (
    pointer: string,
    label: string,
    group: string,
    part_code: string,
    value: number,
    unit_: string,
    extra: object = {}
  ) => ({
    pointer,
    label,
    group,
    part_code,
    value,
    unit: unit_,
    quality: 'good',
    observed_at: '2026-09-13T12:00:00Z',
    age_seconds: 2,
    unchanged_for_seconds: null,
    reason: null,
    condition: 'unknown',
    thresholds_configured: false,
    ...extra
  });
  return {
    ...base,
    layout: {
      ...base.layout,
      parts: [
        {
          id: 'part-motor',
          code: 'PS-MOTOR',
          label: 'Electric motor',
          x: 464,
          y: 206
        },
        {
          id: 'part-thrust-bearing',
          code: 'PS-THRUST-BRG',
          label: 'Thrust bearing',
          x: 512,
          y: 270
        },
        {
          id: 'part-guide-bearing',
          code: 'PS-GUIDE-BRG',
          label: 'Guide radial bearing',
          x: 582,
          y: 349
        }
      ]
    },
    points: unit
      ? {
          '/w1': reading(
            '/w1',
            'Winding 1',
            'Drive motor',
            'PS-MOTOR',
            winding,
            'degC',
            {
              thresholds_configured: true,
              condition: winding > 100 ? 'critical' : 'normal'
            }
          ),
          '/w2': reading(
            '/w2',
            'Winding 2',
            'Drive motor',
            'PS-MOTOR',
            58,
            'degC'
          ),
          '/w3': reading(
            '/w3',
            'Winding 3',
            'Drive motor',
            'PS-MOTOR',
            60,
            'degC'
          ),
          '/t1': reading(
            '/t1',
            'Thrust pad',
            'Thrust Bearing',
            'PS-THRUST-BRG',
            42,
            'degC'
          ),
          '/kw': reading(
            '/kw',
            'Active power',
            'Motor Electrical System',
            'PS-ELECTRICAL',
            8.4,
            'MW'
          )
        }
      : {}
  };
}

test('a pump page shows the pump with its parts numbered and summarised', async ({
  page
}) => {
  let winding = 61;
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: withParts(
        new URL(route.request().url()).searchParams.get('unit'),
        winding
      )
    })
  );
  await page.goto(mimicEntry('/?pump=P1'));
  await expect(page.getByText('Pump unit: P1')).toBeVisible();

  // A part is known by its catalogue code, and named as this pump names it.
  const motor = page.getByRole('button', {
    name: '1. Drive motor',
    exact: true
  });
  await expect(motor.locator('[data-part-summary]')).toHaveText('58 – 61 °C');
  const bearing = page.getByRole('button', {
    name: '2. Thrust Bearing',
    exact: true
  });
  await expect(bearing.locator('[data-part-summary]')).toHaveText('42 °C');

  // A part this pump reports nothing for is still drawn, and says so.
  const absent = page.getByRole('button', {
    name: '3. Guide radial bearing',
    exact: true
  });
  await expect(absent.locator('[data-part-summary]')).toHaveText('—');
  await keyboardFocus(page, absent);
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'No readings on this pump' })
  ).toContainText('No readings on this pump');

  // The list beneath carries the same numbers; a system that is not a part
  // of the drawing carries none.
  const drive = page.locator('[data-section="Drive motor"]');
  await expect(drive.locator('[data-number]')).toHaveText('1');
  const electrical = page.locator('[data-section="Motor Electrical System"]');
  await keyboardFocus(page, electrical.getByRole('button'));
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'Active power: 8.4 MW' })
  ).toContainText('Active power: 8.4 MW');
  await expect(electrical.locator('[data-number]')).toHaveCount(0);

  // Quantitative mini-bars are removed in both limited and unlimited groups.
  await expect(page.locator('rect[data-bar]')).toHaveCount(0);
  await expect(motor).toHaveAttribute('data-verdict', 'normal');

  // Choosing a part on the drawing opens its readings.
  await expect(page.locator('tr[data-point="/w1"]')).toHaveCount(0);
  await motor.click();
  await expect(page.locator('tr[data-point="/w1"]')).toContainText('61 °C');
  await expect(
    page.locator('[data-section="Drive motor"] button').first()
  ).toBeFocused();
  await expect(
    page.locator('[data-section="Drive motor"] button').first()
  ).toHaveAccessibleName('1 Drive motor Normal');

  // Pointer and keyboard focus both outline a part. Leave both before
  // checking that a normal reading does not itself add an outline.
  await page.getByLabel('Filter unit readings', { exact: true }).focus();
  await page.mouse.move(0, 0);
  // Nothing is outlined while every reading is within its limit...
  const drawing = page.getByRole('img', { name: 'Pump unit schematic' });
  expect(
    await drawing.evaluate((node) =>
      (node as SVGElement).style.getPropertyValue('--mimic-part-motor')
    )
  ).toBe('');
  // ...and the part is, in red, once one is past it.
  winding = 140;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(motor.locator('[data-part-summary]')).toHaveText('58 – 140 °C');
  await expect(page.locator('rect[data-bar]')).toHaveCount(0);
  await expect(motor).toHaveAttribute('data-verdict', 'critical');
  await expect(motor.locator('[data-part-verdict]')).toHaveText('Critical');
  await expect(
    page.locator('[data-section="Drive motor"] button').first()
  ).toHaveAccessibleName('1 Drive motor Critical');
  expect(
    await drawing.evaluate((node) =>
      (node as SVGElement).style.getPropertyValue('--mimic-part-motor')
    )
  ).toContain('red');
});

test('a station shows the same pump view for the bay chosen on it', async ({
  page
}) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: withParts(new URL(route.request().url()).searchParams.get('unit'))
    })
  );
  await page.goto(mimicEntry('/'));
  await page.getByRole('button', { name: 'P1: Running', exact: true }).click();
  await expect(
    page.getByRole('button', { name: '1. Drive motor', exact: true })
  ).toContainText('58 – 61 °C');
});

/** A station with one alarm raised on pump 1 and one breach not yet raised. */
function alarmed(unit: string | null, status: 'open' | 'acknowledged') {
  const alarm = (
    pointer: string,
    machine: number,
    anomaly: number | null,
    value: number
  ) => ({
    pointer,
    label: pointer,
    group: 'Electric Motor',
    value,
    unit: 'degC',
    quality: 'good',
    observed_at: '2026-09-13T12:00:00Z',
    age_seconds: 2,
    unchanged_for_seconds: null,
    reason: null,
    condition: 'critical',
    thresholds_configured: true,
    machine,
    anomaly,
    anomaly_status: anomaly === null ? null : status,
    severity: anomaly === null ? null : 'critical'
  });
  return {
    ...numbered(unit),
    alarms: [alarm('/w1', 1, 44, 159.5), alarm('/w9', 10, null, 101)]
  };
}

test('an alarm is acknowledged with a note where it is seen, and leads to Health', async ({
  page
}) => {
  let status: 'open' | 'acknowledged' = 'open';
  const posted: { url: string; body: any }[] = [];
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: alarmed(
        new URL(route.request().url()).searchParams.get('unit'),
        status
      )
    })
  );
  await page.route('**/api/machine-health/machines/**', (route) => {
    posted.push({
      url: route.request().url(),
      body: route.request().postDataJSON()
    });
    status = 'acknowledged';
    return route.fulfill({ json: { pk: 44, status } });
  });
  await page.goto(mimicEntry('/'));

  // Two readings outside their limits; only one is an alarm yet.
  const raised = page.locator('tr[data-point="/w1"]');
  const pending = page.locator('tr[data-point="/w9"]');
  await expect(raised).toContainText('Active · not acknowledged');
  await expect(
    raised.getByRole('link', { name: 'Open /w1 in Health' })
  ).toHaveAttribute('href', '/machines/machine/1/health');
  await expect(pending).toContainText('Not raised yet');
  await expect(
    pending.getByRole('button', { name: 'Acknowledge /w9', exact: true })
  ).toHaveCount(0);

  await raised
    .getByRole('button', { name: 'Acknowledge /w1', exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('/w1: 159.5 °C');
  await dialog.getByLabel('Note').fill('Seen; winding fan checked.');
  await dialog
    .getByRole('button', { name: 'Acknowledge', exact: true })
    .click();

  await expect(dialog).toHaveCount(0);
  expect(posted).toEqual([
    {
      url: expect.stringContaining(
        '/api/machine-health/machines/1/health/anomalies/44/acknowledge/'
      ),
      body: { note: 'Seen; winding fan checked.' }
    }
  ]);
  // The page re-reads, and no second acknowledgement can be submitted.
  await expect(raised).toContainText('Active · acknowledged');
  await expect(
    raised.getByRole('button', { name: 'Acknowledge /w1', exact: true })
  ).toHaveCount(0);
});

test('a refused acknowledgement keeps the note and says why', async ({
  page
}) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: alarmed(
        new URL(route.request().url()).searchParams.get('unit'),
        'open'
      )
    })
  );
  await page.route('**/api/machine-health/machines/**', (route) =>
    route.fulfill({
      status: 403,
      json: { detail: 'You do not have permission to perform this action.' }
    })
  );
  await page.goto(mimicEntry('/?pump=P1'));

  // The pump page lists only its own alarms.
  await expect(page.locator('tr[data-point="/w1"]')).toHaveCount(1);
  await expect(page.locator('tr[data-point="/w9"]')).toHaveCount(0);

  await page
    .getByRole('button', { name: 'Acknowledge /w1', exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Note').fill('Trying anyway');
  await dialog
    .getByRole('button', { name: 'Acknowledge', exact: true })
    .click();
  await expect(dialog).toContainText(
    'You do not have permission to perform this action.'
  );
  await expect(dialog.getByLabel('Note')).toHaveValue('Trying anyway');
});

test('a repair is raised from the alarm row, against the anomaly', async ({
  page
}) => {
  const posted: any[] = [];
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: alarmed(
        new URL(route.request().url()).searchParams.get('unit'),
        'open'
      )
    })
  );
  await page.route('**/api/assets/machines/**', (route) =>
    route.fulfill({ json: [{ pk: 1, name: 'Fixture station / Pump 1' }] })
  );
  await page.route('**/api/part/**', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/maintenance/work-packages/create/', (route) => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        work_order_id: 7,
        work_order_reference: 'WO-7',
        repair_packet_id: null,
        repair_packet_reference: '',
        replayed: false,
        warnings: []
      }
    });
  });
  await page.goto(mimicEntry('/?pump=P1'));

  await page
    .locator('tr[data-point="/w1"]')
    .getByRole('button', { name: 'Create repair for /w1', exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('New work order');
  await expect(dialog.getByLabel('Title')).toHaveValue(
    '/w1 outside configured limits'
  );
  await dialog
    .getByRole('button', { name: 'Create work order', exact: true })
    .click();

  await expect(dialog).toHaveCount(0);
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({
    origin: 'anomaly',
    source: { anomaly_id: 44 }
  });
});

test('a bay with an alarm is marked on the drawing, and counted beside the states', async ({
  page
}) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) => {
    const data = alarmed(
      new URL(route.request().url()).searchParams.get('unit'),
      'open'
    );
    return route.fulfill({
      json: {
        ...data,
        bays: data.bays.map((bay) => ({
          ...bay,
          alarm: bay.key === 'P1' ? 'critical' : null
        }))
      }
    });
  });
  await page.goto(mimicEntry('/'));

  // Still running - that is what the pump is doing - and in alarm as well.
  const bay = page.getByRole('button', {
    name: 'P1: Running, Critical alarm',
    exact: true
  });
  await expect(bay).toHaveAttribute('data-alarm', 'critical');
  await expect(bay.locator('[data-alarm-marker="critical"]')).toBeVisible();
  await expect(page.locator('g[data-bay="P10"]')).not.toHaveAttribute(
    'data-alarm',
    /.+/
  );
  await expect(page.locator('[data-legend="alarm-critical"]')).toContainText(
    'Critical alarm'
  );
  await expect(page.locator('[data-legend="alarm-warning"]')).toHaveCount(0);
});

test('an alarm is dismissed only with a reason, retaining inspection without further mutation', async ({
  page
}) => {
  let status: 'open' | 'suppressed' = 'open';
  const posted: { url: string; body: any }[] = [];
  await page.route('**/api/machine-health/station/17/mimic/**', (route) => {
    const data = alarmed(
      new URL(route.request().url()).searchParams.get('unit'),
      'open'
    );
    data.alarms[0].anomaly_status = status as any;
    return route.fulfill({ json: data });
  });
  await page.route('**/api/machine-health/machines/**', (route) => {
    posted.push({
      url: route.request().url(),
      body: route.request().postDataJSON()
    });
    status = 'suppressed';
    return route.fulfill({ json: { pk: 44, status } });
  });
  await page.goto(mimicEntry('/?pump=P1'));

  const row = page.locator('tr[data-point="/w1"]');
  await row.getByRole('button', { name: 'Dismiss /w1', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Dismiss alarm');
  const confirm = dialog.getByRole('button', { name: 'Dismiss', exact: true });
  // Waving an alarm away has to say why.
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel('Reason').fill('Limit set too tight');
  await confirm.click();

  await expect(dialog).toHaveCount(0);
  expect(posted).toEqual([
    {
      url: expect.stringContaining(
        '/api/machine-health/machines/1/health/anomalies/44/dismiss/'
      ),
      body: { note: 'Limit set too tight' }
    }
  ]);
  await expect(row).toContainText('Dismissed');
  for (const action of [
    'Acknowledge /w1',
    'Create repair for /w1',
    'Dismiss /w1'
  ]) {
    await expect(
      row.getByRole('button', { name: action, exact: true })
    ).toHaveCount(0);
  }
  const signal = row.getByRole('button', { name: '/w1', exact: true });
  const statusInspector = row.locator('[data-alarm-info]');
  await expect(signal).toBeVisible();
  await expect(statusInspector).toContainText('Dismissed');
  await keyboardFocus(page, signal);
  await expect(
    page.getByRole('tooltip').filter({ hasText: /^\/w1$/ })
  ).toBeVisible();
  await keyboardFocus(page, statusInspector);
  await expect(
    page.getByRole('tooltip').filter({
      hasText: 'Acknowledgement records that someone saw the application alarm'
    })
  ).toBeVisible();
  // Looking up the signal and status never repeats the dismissal or submits
  // another alarm mutation.
  expect(posted).toHaveLength(1);
  await expect(
    row.getByRole('link', { name: 'Open /w1 in Health' })
  ).toBeVisible();
});
