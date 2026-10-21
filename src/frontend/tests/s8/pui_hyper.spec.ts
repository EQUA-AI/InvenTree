import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockFaultHistory } from '../s3/machineWorkFixtures';
import { nonText, readable } from './contrast';
import {
  mockEquipment,
  openEquipment,
  openExplorer,
  openFaultHistory
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';
nativeBaseline();
nativeThemeFixtures();

for (const scheme of ['light', 'dark'] as const) {
  test(`shared badge vocabulary, source clock and freshness in ${scheme}`, async ({
    page
  }) => {
    const data = await mockEquipment(page);
    data.display_shifted = true;
    data.display_shift_seconds = 400 * 86400;
    data.alarms[0].label = 'DE bearing temperature';
    await openEquipment(page, true, scheme, { role: 'acknowledger' });
    await expect(
      page.getByText('Recorded history shown as current', { exact: true })
    ).toBeVisible();
    const open = page.locator('[data-alarm-status="open"]').first();
    const ack = page.locator('[data-alarm-status="acknowledged"]').first();
    await expect(open).toHaveAttribute('data-variant', 'filled');
    await expect(ack).toHaveAttribute('data-variant', 'outline');
    await expect(open.locator('svg')).toHaveClass(/icon-bell-ringing/);
    await expect(ack.locator('svg')).toHaveClass(/icon-bell-check/);
    for (const badge of await page
      .locator('[data-equipment-view] .mantine-Badge-root')
      .all())
      if (await badge.isVisible()) await readable(badge);
    await expect(
      page.getByRole('button', {
        name: 'Acknowledge DE bearing temperature',
        exact: true
      })
    ).toHaveCount(1);
    await expect(page.getByText(/your clock|plant clock/)).toHaveCount(1);
    await expect(
      page.locator('[data-operational-panel]').first().getByRole('status')
    ).toHaveCount(1);
    await expect(
      page.getByText(/\b(AM|PM)\b|\d{1,2}\/\d{1,2}\/\d{4}/)
    ).toHaveCount(0);
    await expect(
      page.locator('svg[aria-label="Pump unit schematic"] > g[data-point]')
    ).toHaveCount(5);
    const status = page
      .getByRole('heading', { name: 'Pump unit: P1' })
      .locator('..');
    await expect(status).toContainText('2 s old');
    await expect(page.getByLabel('Process data').locator('dt')).toHaveCount(4);
    await expect(page.getByLabel('Process data').locator('dd')).toHaveCount(4);
  });
  test(`poll errors are neutral acquisition facts in ${scheme}`, async ({
    page
  }) => {
    const data = await mockEquipment(page, 'unknown');
    data.last_error_code = 'NETWORK';
    data.last_error_at = '2026-10-20T07:59:00Z';
    data.last_success_at = null;
    await openEquipment(page, true, scheme);
    const alert = page.getByRole('alert').first();
    await expect(alert).toContainText('Polling error');
    await expect(alert).toContainText(
      'Source network request failed (code NETWORK)'
    );
    await expect(alert).toContainText('time not supplied');
    await expect(alert).not.toContainText('Communication lost');
    await readable(alert);
    // The alarm's recorded severity remains explicit when its current
    // threshold reading is unavailable; color is not the only severity cue.
    await expect(
      page
        .locator('[data-alarm-status="open"]')
        .first()
        .locator('..')
        .getByText('Warning', { exact: true })
    ).toBeVisible();
    await expect(
      page
        .getByRole('heading', { name: 'Pump unit: P1' })
        .locator('..')
        .getByText('Running', { exact: true })
    ).toHaveCount(0);
    data.last_error_code = '';
    data.last_poll_at = null;
    data.last_success_at = null;
    data.last_error_at = null;
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(page.getByLabel('Process data')).toContainText(
      'Enabled · no error reported'
    );
    await expect(page.getByLabel('Process data')).not.toContainText(/\bOK\b/);
  });
  test(`3D phone touch and desktop selection in ${scheme}`, async ({
    page
  }, info) => {
    await mockEquipment(page);
    await openEquipment(page, false, scheme);
    const canvas = page.locator('.aimms-twin-canvas');
    const phone = info.project.name.endsWith('phone');
    if (phone) {
      await expect(canvas).toHaveCSS('touch-action', 'pan-y');
      await page
        .getByRole('button', { name: 'Rotate model', exact: true })
        .click();
      await expect(canvas).toHaveCSS('touch-action', 'none');
    }
    await expect(page.locator('[data-hidden-markers]')).toContainText(
      'Motor drive-end bearing (2)'
    );
    await expect(page.locator('[data-hidden-markers]')).toContainText(
      'Impeller (5)'
    );
    await page.getByText('Cutaway', { exact: true }).click();
    await expect(page.locator('[data-hidden-markers]')).not.toContainText(
      'Impeller (5)'
    );
    await page.locator('[data-position="bearing"]').click();
    await expect(
      page.getByRole('status').filter({ hasText: 'Selected:' })
    ).toContainText('Motor drive-end bearing');
    await expect(
      page
        .locator('[data-scene-phase]')
        .getByText('Reference model · geometry unverified')
    ).toBeVisible();
    await readable(page.locator('[data-scene-phase] .mantine-Badge-root'));
    await expect(page.getByText(/your clock|plant clock/)).toHaveCount(1);
    await expect(
      page.locator('[data-operational-panel]').first().getByRole('status')
    ).toHaveCount(1);
  });
  test(`machine page categories, controls and known zero in ${scheme}`, async ({
    page
  }, info) => {
    const fixture = await mockExplorer(page);
    await openExplorer(page, '/machines/index/sites/?location=12', scheme);
    const filters = page.getByRole('button', { name: 'Filters', exact: true });
    if (await filters.isVisible()) await filters.click();
    const scope = page.getByRole('region', { name: 'Loaded page summary' });
    await expect(scope).toContainText('Condition unknown (assessed)');
    await expect(scope).toContainText('Condition unavailable (not judged)');
    await expect(
      page.getByRole('textbox', { name: 'Search machines' })
    ).toBeVisible();
    await expect(
      page.getByRole('combobox', { name: 'Criticality', exact: true })
    ).toHaveValue('Any criticality');
    const sw = page.getByRole('switch', { name: 'Include sublocations' });
    await expect(sw).toHaveAttribute('aria-describedby', /.+/);
    await nonText(
      sw.locator('..').locator('.mantine-Switch-track'),
      page.locator('#location-name')
    );
    await page.getByRole('button', { name: /In alarm:/ }).click();
    await expect
      .poll(() => fixture.requests.at(-1)?.params.get('has_alarms'))
      .toBe('true');
    await expect(page.getByText(/your clock|plant clock/)).toHaveCount(1);
    await expect(
      page.locator('[data-operational-panel]').first().getByRole('status')
    ).toHaveCount(1);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    if (info.project.name.endsWith('phone'))
      await expect(page.locator('[data-machine-card]').first()).toBeVisible();
  });
  test(`opened occurrences show state, detector metrics and one clock in ${scheme}`, async ({
    page
  }) => {
    await mockFaultHistory(page);
    await openFaultHistory(page, { scheme, query: '?occurrence=901' });
    const evidence = page.getByTestId('occurrence-evidence');
    await expect(evidence.getByRole('heading').first()).toHaveText(
      'Drive-end bearing high temperature'
    );
    await expect(evidence).toContainText('Resolved');
    await expect(evidence).toContainText('High warning limit 110 °C');
    await expect(
      evidence.getByText(
        'Bearing temperature 118.4 degC above its 110 degC limit.',
        { exact: true }
      )
    ).toBeVisible();
    await expect(
      evidence.getByText('Outside occurrence span', { exact: true })
    ).toHaveCount(0);
    await expect(page.getByText(/your clock|plant clock/)).toHaveCount(1);
    await expect(
      page.locator('[data-operational-panel]').first().getByRole('status')
    ).toHaveCount(1);
    await expect(page.getByText(/dashboard time shift/)).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Acknowledge', exact: true })
    ).toHaveCount(0);
    const listHeader = page.getByRole('columnheader', {
      name: 'Severity',
      exact: true
    });
    if (await listHeader.count())
      expect((await listHeader.boundingBox())!.y).toBeLessThan(
        (await evidence.boundingBox())!.y
      );
  });
}

for (const scheme of ['light', 'dark'] as const)
  test(`mapped reference-part marker contrast ${scheme}`, async ({
    page
  }, info) => {
    const data = await mockEquipment(page);
    const point = Object.values(data.points).find((p) => p.unit === 'kW')!;
    point.part_code = 'PS-MOTOR';
    point.thresholds_configured = true;
    await openEquipment(page, true, scheme);
    for (const condition of [
      'normal',
      'warning',
      'critical',
      'unknown'
    ] as const) {
      point.condition = condition;
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      const part = page
        .locator('g[data-part="PS-MOTOR"]')
        .filter({ has: page.locator('circle + text') });
      await expect(part).toHaveCount(1);
      await expect(part).toHaveAttribute('data-verdict', condition);
      await expect(part.locator('[data-part-label]')).toHaveCount(3);
      await expect(
        part.locator('[data-part-label]').first().locator(':scope > *')
      ).toHaveCSS('text-overflow', 'ellipsis');
      // The key and its corresponding drawing marker each carry the numeral.
      await expect(part.locator('circle + text')).toHaveCount(2);
      for (const numeral of await part.locator('circle + text').all())
        await readable(numeral);
      await part.screenshot({
        path: info.outputPath(`mapped-part-${condition}-${scheme}.png`)
      });
    }
  });
