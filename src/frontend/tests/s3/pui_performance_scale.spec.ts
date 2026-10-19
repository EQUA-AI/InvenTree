import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { NATIVE_SIGNALS, openPerformance } from './performanceFixtures';

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
      const requests = await openPerformance(page, {
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
  await openPerformance(page, { harness: 's3', scheme: 'dark' });
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
