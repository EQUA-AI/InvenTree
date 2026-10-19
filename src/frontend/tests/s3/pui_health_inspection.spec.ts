import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  HEALTH_SIGNALS,
  openHealth,
  signalRow,
  signalTable,
  trendCard
} from './healthFixtures';

nativeBaseline();
test.use({ timezoneId: 'UTC' });

for (const scheme of ['light', 'dark'] as const) {
  test(`selection, native expansion and focus return (${scheme})`, async ({
    page
  }, info) => {
    const requests = await openHealth(page, {
      scheme,
      signals: HEALTH_SIGNALS.map((signal) => ({
        ...signal,
        component:
          signal.id === 203
            ? {
                code: 'BRG:1',
                name: 'Motor drive-end bearing',
                status: 'draft'
              }
            : null
      }))
    });
    const inspect = signalTable(page).getByRole('button', {
      name: 'Inspect signal: Motor DE vibration (raw)',
      exact: true
    });
    await inspect.focus();
    await page.keyboard.press('Enter');
    const evidence = page.getByRole('region', {
      name: 'Signal evidence: Motor DE vibration (raw)'
    });
    await expect(evidence.getByRole('heading')).toBeFocused();
    await expect(
      trendCard(page).getByRole('combobox', { name: 'Parameter' })
    ).toHaveValue('Motor DE vibration (raw)');
    await expect(evidence).toContainText('Draft inference');
    await expect(evidence).toContainText('No unit recorded');
    await expect(evidence).toContainText('Not configured');
    await expect(evidence).toContainText(
      'No snapshot of this signal in the latest 50'
    );
    await evidence.screenshot({
      path: info.outputPath(`signal-evidence-${scheme}.png`)
    });
    await evidence
      .getByRole('button', { name: 'Close signal evidence' })
      .click();
    await expect(inspect).toBeFocused();

    const range = trendCard(page).getByRole('combobox', { name: 'Range' });
    await range.click();
    await page.getByRole('option', { name: '15 minutes', exact: true }).click();
    await expect
      .poll(() => requests.chart.at(-1)?.searchParams.get('binding'))
      .toBe('203');
    await expect
      .poll(() => {
        const url = requests.chart.at(-1)!;
        return (
          Date.parse(url.searchParams.get('to')!) -
          Date.parse(url.searchParams.get('from')!)
        );
      })
      .toBe(900000);
    const caption = await trendCard(page)
      .getByText(/^Selected window:/)
      .textContent();
    const count = requests.chart.length;
    const expand = page.getByRole('button', {
      name: 'Expand chart',
      exact: true
    });
    await expand.click();
    const modal = page.getByRole('dialog', { name: 'Expanded signal trend' });
    await expect(
      modal.getByRole('combobox', { name: 'Parameter' })
    ).toHaveValue('Motor DE vibration (raw)');
    await expect(modal.getByRole('combobox', { name: 'Range' })).toHaveValue(
      '15 minutes'
    );
    await expect(modal.getByText(/^Selected window:/)).toHaveText(caption!);
    await expect(modal.locator('.recharts-area-curve')).toHaveAttribute(
      'stroke',
      /blue-6/
    );
    expect(
      await modal
        .locator('.recharts-responsive-container')
        .evaluate((el) => el.clientHeight)
    ).toBe(320);
    await modal.screenshot({ path: info.outputPath(`expanded-${scheme}.png`) });
    await page.keyboard.press('Escape');
    await expect(expand).toBeFocused();
    await expect(
      trendCard(page).getByRole('combobox', { name: 'Range' })
    ).toHaveValue('15 minutes');
    await expect(trendCard(page).getByText(/^Selected window:/)).toHaveText(
      caption!
    );
    expect(requests.chart).toHaveLength(count);
    // The rest of the row selects its own binding too.
    await signalTable(page)
      .getByRole('row')
      .filter({ hasText: 'Bypass flow' })
      .getByRole('cell', { name: '0 m3/h', exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Signal evidence: Bypass flow' })
    ).toBeFocused();
    await expect(
      trendCard(page).getByRole('combobox', { name: 'Parameter' })
    ).toHaveValue('Bypass flow (m3/h)');
    await expect(
      trendCard(page).getByRole('combobox', { name: 'Range' })
    ).toHaveValue('15 minutes');
  });
}

test('failed evidence and unavailable anomaly association are never empty success', async ({
  page
}) => {
  await openHealth(page, { snapshotsStatus: 503, anomaliesStatus: 503 });
  await signalTable(page)
    .getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    })
    .click();
  const evidence = page.getByRole('region', {
    name: 'Signal evidence: Discharge flow'
  });
  await expect(evidence).toContainText('Retained evidence could not be read');
  await expect(evidence).toContainText('association unavailable');
  await expect(evidence).not.toContainText('No active anomaly cites');
  await expect(evidence).not.toContainText('No snapshot of this signal');
});

test('custom range form survives expand, inspect and close', async ({
  page
}) => {
  await openHealth(page);
  await trendCard(page).getByRole('combobox', { name: 'Range' }).click();
  await page.getByRole('option', { name: 'Custom range…' }).click();
  await expect(trendCard(page)).toContainText('Pick both a start and an end');
  await page.getByRole('button', { name: 'Expand chart', exact: true }).click();
  const modal = page.getByRole('dialog');
  await expect(modal.getByRole('combobox', { name: 'Range' })).toHaveValue(
    'Custom range…'
  );
  await expect(
    modal.getByText('Pick both a start and an end for the window.')
  ).toBeVisible();
  await modal.getByRole('button', { name: 'Inspect selected signal' }).click();
  await expect(
    modal.getByRole('heading', { name: 'Signal evidence: Discharge flow' })
  ).toBeFocused();
  await modal.getByRole('button', { name: 'Close signal evidence' }).click();
  await expect(
    modal.getByRole('button', { name: 'Inspect selected signal' })
  ).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('button', { name: 'Expand chart', exact: true })
  ).toBeFocused();
  await expect(
    trendCard(page).getByRole('combobox', { name: 'Range' })
  ).toHaveValue('Custom range…');
});

test('a removed selected binding never falls back to another signal on refresh', async ({
  page
}) => {
  await openHealth(page);
  await signalTable(page)
    .getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    })
    .click();
  await page.route(
    '**/api/machine-health/machines/18/health/signals/**',
    async (route) => {
      await route.fulfill({
        json: { count: 1, results: [signalRow(HEALTH_SIGNALS[1])] }
      });
    }
  );
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(trendCard(page)).toContainText(
    'Selected signal is no longer mapped here'
  );
  await expect(trendCard(page).locator('.recharts-area-curve')).toHaveCount(0);
  await expect(
    page.getByRole('region', { name: /^Signal evidence:/ })
  ).toContainText('This signal is not mapped here');
});
