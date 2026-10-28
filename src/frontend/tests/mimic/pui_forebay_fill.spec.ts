import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { fixture, observed } from './layoutFixtures';
import { mimicEntry } from './navigation';

nativeBaseline();

for (const scenario of [
  'current',
  'recorded',
  'recorded-flow',
  'recorded-stale',
  'recorded-bad-quality',
  'station-disabled',
  'stale',
  'bad-quality',
  'missing',
  'non-numeric',
  'not-finite',
  'read-failed'
] as const) {
  test(`forebay schematic fill: ${scenario}`, async ({ page }, info) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.clock.install({ time: new Date(observed) });
    await page.route('**/api/machine-health/station/17/mimic/**', (route) => {
      const data = fixture(null);
      const element = data.layout.elements.find(
        (candidate) => candidate.id === 'forebay'
      )!;
      const point = data.station_points[element.pointer];
      if (scenario.startsWith('recorded')) data.polling_enabled = false;
      if (scenario === 'recorded-stale') point.reason = 'stale';
      if (scenario === 'recorded-bad-quality') point.quality = 'bad';
      if (scenario === 'recorded-flow') {
        const bay = data.bays[0];
        const flow = data.layout.elements.find(
          (candidate) => candidate.id === 'pump-flow'
        )!;
        const pointer = flow.pointer
          .replaceAll('{pump_number}', bay.key.slice(1))
          .replaceAll('{pump}', bay.key);
        bay.state = 'running';
        bay.points[pointer].value = 10;
      }
      if (scenario === 'station-disabled') data.enabled = false;
      if (scenario === 'stale') point.reason = 'stale';
      if (scenario === 'bad-quality') point.quality = 'bad';
      if (scenario === 'missing') point.value = null;
      if (scenario === 'non-numeric') point.value = 'Unknown';
      if (scenario === 'not-finite') {
        point.value = null;
        point.reason = 'not_finite';
      }
      if (scenario === 'read-failed') data.read_failed = true;
      return route.fulfill({ json: data });
    });
    await page.goto(mimicEntry());
    const tank = page.locator('[data-forebay]').first();
    const metric = page.locator(
      '[data-station-metric="forebay"] [data-reading-value]'
    );
    await expect(tank).toBeVisible();
    if (scenario === 'current') {
      await expect(tank.locator('[data-forebay-water]')).toBeVisible();
      await expect(tank).toHaveAccessibleName(
        'Forebay level: 120.97 m. Not to scale'
      );
      await expect(tank.locator('text').last()).toHaveText('Not to scale');
      await expect(metric).toHaveText('120.97 m');
    } else if (
      ['recorded', 'recorded-stale', 'recorded-flow'].includes(scenario)
    ) {
      await expect(tank.locator('[data-forebay-water]')).toBeVisible();
      await expect(tank).toHaveAccessibleName(
        /Forebay level: 120\.97 m.*Last recorded · polling off.*Synthetic regression fixture.*not to scale/i
      );
      await expect(tank.locator('text').last()).toHaveText(
        'Recorded · unscaled'
      );
      await expect(metric).toHaveText('120.97 m');
      const panel = page.locator('[data-station-metric="forebay"]');
      await expect(panel).toContainText('Last recorded · polling off');
      await expect(panel).toContainText('Synthetic regression fixture');
      if (scenario === 'recorded-stale')
        await expect(panel).toContainText('Stale');
      await expect(metric).not.toHaveAttribute('data-severity', /.+/);
      await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
      await expect(
        page.getByText('Last recorded state · polling off')
      ).toBeVisible();
      if (scenario === 'recorded-flow') {
        await expect(page.locator('[data-discharge-flow]')).toHaveCount(0);
      }
    } else {
      await expect(tank.locator('[data-forebay-water]')).toHaveCount(0);
      await expect(tank).toHaveAccessibleName('Forebay level: Unavailable');
      await expect(tank.locator('text').last()).toHaveText('Unavailable');
      await expect(metric).toHaveText('—');
    }
    await page.screenshot({ path: info.outputPath(`${scenario}.png`) });
  });
}

for (const presentation of [
  { name: 'desktop-dark', width: 1440, height: 900, scheme: 'dark' },
  { name: 'phone-light', width: 390, height: 844, scheme: 'light' },
  { name: 'phone-recorded', width: 390, height: 844, scheme: 'light' }
] as const) {
  test(`forebay schematic presentation: ${presentation.name}`, async ({
    page
  }, info) => {
    await page.setViewportSize(presentation);
    await page.clock.install({ time: new Date(observed) });
    await page.route('**/api/machine-health/station/17/mimic/**', (route) => {
      const data = fixture(null);
      if (presentation.name === 'phone-recorded') data.polling_enabled = false;
      return route.fulfill({ json: data });
    });
    await page.goto(mimicEntry(`/?scheme=${presentation.scheme}`));
    await expect(page.locator('[data-forebay-water]').first()).toBeVisible();
    await expect(
      page.locator('[data-forebay]').first().locator('text').last()
    ).toHaveText(
      presentation.name === 'phone-recorded'
        ? 'Recorded · unscaled'
        : 'Not to scale'
    );
    if (presentation.name === 'phone-recorded') {
      await expect(
        page.locator('[data-station-metric="forebay"]')
      ).toContainText('Last recorded · polling off');
      await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
      const caption = await page
        .locator('[data-forebay]')
        .first()
        .locator('text')
        .last()
        .boundingBox();
      const viewport = await page
        .getByRole('region', { name: 'Station pump bays' })
        .boundingBox();
      expect(caption!.x).toBeGreaterThanOrEqual(viewport!.x);
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(presentation.width + 1);
    await page.screenshot({
      path: info.outputPath(`${presentation.name}.png`)
    });
  });
}
