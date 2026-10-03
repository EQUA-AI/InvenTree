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
  await expect(page.locator('g[data-point="/dex/PUMP17_POWER"]')).toContainText(
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
  await expect(page.locator('g[data-point="/dex/PUMP17_POWER"]')).toContainText(
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
  await expect(page.locator('g[data-point="/dex/PUMP17_POWER"]')).toContainText(
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
  await expect(page.locator('g[data-point="/dex/PUMP17_POWER"]')).toHaveCount(
    0
  );
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
  await page.goto('/');
  const bays = page.locator('g[data-bay]');
  await expect(bays).toHaveCount(3);
  // The server sorts the keys as text - P1, P10, P2. A row of pumps is not.
  expect(
    await bays.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('data-bay'))
    )
  ).toEqual(['P1', 'P2', 'P10']);

  await expect(page.locator('[data-legend="running"]')).toHaveText('Running 1');
  await expect(page.locator('[data-legend="idle"]')).toHaveText('Idle 1');
  // A state nobody can read is counted as unknown, never as idle.
  await expect(page.locator('[data-legend="unknown"]')).toHaveText('Unknown 1');

  // Choosing a bay is done on the drawing, and undone the same way.
  const second = page.getByRole('button', { name: 'P2: Unknown', exact: true });
  await second.click();
  await expect(second).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('Pump unit: P2')).toBeVisible();
  await second.click();
  await expect(second).toHaveAttribute('aria-pressed', 'false');
  await expect(
    page.getByText('Select a pump bay to view its instruments and readings.')
  ).toBeVisible();
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
  await page.goto('/');
  const value = page.locator('g[data-point="/st"] text').last();
  await expect(value).toHaveText('Running');

  // Nothing guesses at a code the layout has not been told the meaning of.
  code = 'Q';
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(value).toHaveText('Q');

  // The pump's own tag, its summary line and its table agree with the words.
  await page.getByRole('button', { name: 'P10: Idle', exact: true }).click();
  await expect(
    page.locator('g[data-point="/pd/P10/st"] text').last()
  ).toHaveText('Idle');
  const group = page.getByRole('button', { name: 'Equipment Status Group' });
  await expect(group).toContainText('Equipment Status: Idle');
  await group.click();
  const row = page.locator('tr[data-point="/pd/P10/st"]');
  await expect(row).toContainText('Idle');
  await expect(row).toContainText('Source code: I');
});

test('a system is one line until it is opened', async ({ page }) => {
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({
      json: numbered(new URL(route.request().url()).searchParams.get('unit'))
    })
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'P1: Running', exact: true }).click();

  const motor = page.getByRole('button', { name: 'Electric Motor' });
  // A family is a span; a lone reading is named. Neither is ranked against
  // the other, and nothing is called normal without a limit to be normal by.
  await expect(motor).toContainText('3 readings: 30 – 50.5 degC');
  await expect(motor).toContainText('Shaft speed: 745 rpm');
  await expect(motor).toContainText('No threshold configured');
  await expect(page.locator('tr[data-point="/w1"]')).toHaveCount(0);

  await motor.click();
  await expect(page.locator('tr[data-point="/w1"]')).toContainText('30 degC');
  await expect(page.locator('tr[data-point="/w2"]')).toContainText('50.5 degC');
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
  await page.goto('/');
  await page.getByRole('button', { name: 'P17: Stale', exact: true }).click();
  const tag = page.locator('g[data-point="/dex/PUMP17_POWER"]');
  await expect(tag.locator('text').last()).toHaveText('Stale');
  await expect(tag.locator('rect')).toHaveAttribute('stroke-dasharray', '4 3');
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
  await page.goto('/?pump=P1');
  await expect(page.getByText('Pump unit: P1')).toBeVisible();

  // A part is known by its catalogue code, and named as this pump names it.
  const motor = page.getByRole('button', {
    name: '1. Drive motor',
    exact: true
  });
  await expect(motor).toContainText('3 readings: 58 – 61 degC');
  const bearing = page.getByRole('button', {
    name: '2. Thrust Bearing',
    exact: true
  });
  await expect(bearing).toContainText('Thrust pad: 42 degC');

  // A part this pump reports nothing for is still drawn, and says so.
  await expect(
    page.getByRole('button', { name: '3. Guide radial bearing', exact: true })
  ).toContainText('No readings on this pump');

  // The list beneath carries the same numbers; a system that is not a part
  // of the drawing carries none.
  const drive = page.locator('[data-section="Drive motor"]');
  await expect(drive.locator('[data-number]')).toHaveText('1');
  const electrical = page.locator('[data-section="Motor Electrical System"]');
  await expect(electrical).toContainText('Active power: 8.4 MW');
  await expect(electrical.locator('[data-number]')).toHaveCount(0);

  // Each winding is a bar, as tall as its reading against the others; the
  // shaft speed is not among them, being in another unit.
  const bars = motor.locator('rect[data-bar]');
  await expect(bars).toHaveCount(3);
  const heights = async () =>
    bars.evaluateAll((nodes) =>
      nodes.map((node) => [
        node.getAttribute('data-bar'),
        Number(node.getAttribute('height'))
      ])
    );
  let [w1, w2, w3] = await heights();
  expect(w1[1]).toBeGreaterThan(w3[1]);
  expect(w3[1]).toBeGreaterThan(w2[1]);

  // Choosing a part on the drawing opens its readings.
  await expect(page.locator('tr[data-point="/w1"]')).toHaveCount(0);
  await motor.click();
  await expect(page.locator('tr[data-point="/w1"]')).toContainText('61 degC');

  // Pointing at a part outlines it; take the pointer away to see its own colour.
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
  await expect(motor).toContainText('3 readings: 58 – 140 degC');
  // The one past its limit is the tallest bar, and red.
  [w1, w2, w3] = await heights();
  expect(w1[1]).toBeGreaterThan(w2[1]);
  expect(w1[1]).toBeGreaterThan(w3[1]);
  await expect(bars.first()).toHaveAttribute('fill', /red/);
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
  await page.goto('/');
  await page.getByRole('button', { name: 'P1: Running', exact: true }).click();
  await expect(
    page.getByRole('button', { name: '1. Drive motor', exact: true })
  ).toContainText('3 readings: 58 – 61 degC');
});
