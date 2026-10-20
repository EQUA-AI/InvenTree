import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { NATIVE_SIGNALS, openPausedPerformance } from './performanceFixtures';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const)
  for (const count of [50, 200, 500])
    test(`${scheme} ${count} synthetic channels: bounded reads, navigation and retained controls`, async ({
      page
    }, info) => {
      const signals = [
        ...NATIVE_SIGNALS.slice(0, Math.min(40, count)),
        ...Array.from({ length: count - Math.min(40, count) }, (_, i) => ({
          key: `/dex/PUMP1_EXTRA_${i}`,
          name: `Extra instrument ${i}`,
          value: i,
          unit: 'bar'
        }))
      ];
      const requests = await openPausedPerformance(page, {
        signals,
        harness: 's3',
        scheme
      });
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      expect(requests).toHaveLength(Math.ceil((count + 2) / 200));
      const spans = requests.map((url) => [
        url.searchParams.get('from'),
        url.searchParams.get('to')
      ]);
      expect(new Set(spans.map((span) => span.join('/'))).size).toBe(1);
      expect(
        requests.every(
          (url) =>
            ['bindings', 'keys'].reduce(
              (n, k) => n + (url.searchParams.get(k)?.split(',').length ?? 0),
              0
            ) <= 200
        )
      ).toBe(true);
      const scope = requests.map((url) => url.search).join('|');
      if (count > 100)
        await expect(
          page.locator('[data-section="Electrical"] .recharts-surface')
        ).toHaveCount(0);
      await page
        .getByLabel('Find a channel', { exact: true })
        .fill(`EXTRA_${count - 41}`);
      await page
        .getByRole('button', {
          name: new RegExp(`Extra instrument ${count - 41} ·`)
        })
        .click();
      await expect(
        page.locator('[data-section="Other signals"]')
      ).toBeVisible();
      await expect(
        page.locator('[data-section="Other signals"]')
      ).toContainText(`EXTRA_${count - 41}`);
      if (count === 500) {
        await page.getByTestId('channel-navigator').screenshot({
          path: info.outputPath(`search-${count}-${scheme}.png`),
          animations: 'disabled'
        });
        const result = page
          .getByRole('button', {
            name: new RegExp(`Extra instrument ${count - 41} ·`)
          })
          .locator('.mantine-Button-label');
        expect(
          await result.evaluate((el) => el.scrollHeight <= el.clientHeight + 1)
        ).toBe(true);
      }
      await page.getByRole('button', { name: 'Show all', exact: true }).click();
      await expect(
        page.getByText('Power and power factor', { exact: true })
      ).toBeVisible();
      await page
        .getByRole('button', { name: 'Collapse Electrical', exact: true })
        .click();
      await page
        .getByRole('button', { name: 'Open Electrical', exact: true })
        .click();
      expect(requests.map((url) => url.search).join('|')).toBe(scope);
      const nav = page.getByTestId('channel-navigator');
      await nav.scrollIntoViewIfNeeded();
      expect(
        await nav.evaluate((node) => node.getBoundingClientRect().right)
      ).toBeLessThanOrEqual(page.viewportSize()!.width);
      await page.screenshot({
        path: info.outputPath(`scale-${count}-${scheme}.png`),
        animations: 'disabled'
      });
    });

test('large channel navigation in dark scheme', async ({ page }, info) => {
  await openPausedPerformance(page, { harness: 's3', scheme: 'dark' });
  await page
    .getByLabel('Find a channel', { exact: true })
    .fill('WINDING_TEMPERATURED1');
  await page.getByTestId('channel-navigator').scrollIntoViewIfNeeded();
  await page.getByTestId('channel-navigator').screenshot({
    path: info.outputPath('navigation-dark.png'),
    animations: 'disabled'
  });
  const buttons = page
    .getByTestId('channel-navigator')
    .getByRole('button', { name: /Winding \d+ ·/ });
  expect(await buttons.count()).toBeGreaterThan(0);

  expect(
    await buttons.evaluateAll((els) =>
      els.every((el) => {
        const label = el.querySelector('.mantine-Button-label');
        if (!label) return false;
        const text = label.getBoundingClientRect();
        const button = el.getBoundingClientRect();
        return (
          label.scrollHeight <= label.clientHeight + 1 &&
          label.scrollWidth <= label.clientWidth + 1 &&
          text.top >= button.top &&
          text.bottom <= button.bottom &&
          text.left >= button.left &&
          text.right <= button.right
        );
      })
    )
  ).toBe(true);
});

for (const scheme of ['light', 'dark'] as const)
  test(`complete batches with different measured cadences keep their native curves in ${scheme}`, async ({
    page
  }, info) => {
    const signals = [
      ...Array.from({ length: 200 }, (_, i) => ({
        key: `/dex/PUMP1_EXTRA_CADENCE_${i}`,
        name: `Extra cadence ${i}`,
        value: i,
        unit: 'bar'
      })),
      ...NATIVE_SIGNALS.filter((s) =>
        /PUMP1_(ACTIVE_POWER|SPEED)$|WINDING_TEMPERATURED[1-4]$/.test(s.key)
      )
    ];
    const requests = await openPausedPerformance(page, {
      harness: 's3',
      scheme,
      signals,
      harnessParams: {
        entry:
          '/machines/machine/18/performance?history_from=2026-10-01T00:00:00Z&history_to=2026-10-01T00:05:00Z'
      },
      mapSeriesResponse: (response, url) =>
        url.searchParams.get('bindings')?.split(',').includes('300')
          ? {
              ...response,
              resolution_seconds: 20,
              cadence_seconds: 20,
              series: response.series.map((entry) => {
                const samples = entry.samples.filter((_, i) => i % 4 === 0);
                return { ...entry, samples, count: samples.length };
              })
            }
          : response
    });
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    expect(requests).toHaveLength(2);
    await page
      .getByRole('button', { name: 'Open Electrical', exact: true })
      .click();
    const section = page.locator('[data-section="Electrical"]');
    const curve = section.locator('path.recharts-line-curve').first();
    await expect(curve).toHaveAttribute('d', /L/);
    const provenance = page.getByTestId('history-provenance');
    await provenance
      .locator('summary')
      .filter({ hasText: 'Per-batch resolution and clock' })
      .click();
    await expect(provenance).toContainText(
      'Resolution 5 s; measured cadence 5 s'
    );
    await expect(provenance).toContainText(
      'Resolution 20 s; measured cadence 20 s'
    );
    await page
      .getByRole('button', {
        name: 'Open Motor winding temperature',
        exact: true
      })
      .click();
    const winding = page.locator('[data-section="Motor winding temperature"]');
    await expect(
      winding.locator('path.recharts-line-curve').first()
    ).toHaveAttribute('d', /L/);
    await section.screenshot({
      path: info.outputPath(`batch-cadence-curve-${scheme}.png`),
      animations: 'disabled'
    });
    await winding.screenshot({
      path: info.outputPath(`batch-cadence-sensors-${scheme}.png`),
      animations: 'disabled'
    });
    await provenance.screenshot({
      path: info.outputPath(`batch-cadence-metadata-${scheme}.png`),
      animations: 'disabled'
    });
  });
