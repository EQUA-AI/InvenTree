import { expect, test } from '@playwright/test';
import { signalRows } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { openHealth } from './healthFixtures';
import {
  mockPerformance,
  pausePerformanceLiveUpdates
} from './performanceFixtures';

nativeBaseline();

test('the Health headline identifies a synthetic critical assessment as TEST', async ({
  page
}, info) => {
  await openHealth(page, {
    summary: { state: 'critical', synthetic_active_count: 1 }
  });
  await expect(page.getByTestId('health-summary-test')).toHaveText('TEST');
  await expect(page.getByTestId('health-summary-test')).toBeVisible();
  await page
    .getByTestId('health-summary-test')
    .locator('..')
    .screenshot({
      path: info.outputPath('health-summary-test-provenance.png'),
      animations: 'disabled'
    });
});

test('all pump channels include current values, unsupported status and explicit unconfigured exercises', async ({
  page
}, info) => {
  await page.route('**/api/assets/registry/?*', (route) =>
    route.fulfill({
      json: {
        results: [
          { pk: 17, name: 'Synthetic station', asset_type: 'pumphouse' }
        ]
      }
    })
  );
  await page.route('**/api/assets/registry/17/', (route) =>
    route.fulfill({
      json: {
        children: [
          { pk: 18, name: 'Synthetic pump', parent: 17, asset_type: 'pump' }
        ]
      }
    })
  );
  let body: Record<string, unknown> | null = null;
  await page.route(
    '**/api/machine-health/machines/18/health/test-alarm/',
    (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({
          json: {
            results: [
              {
                binding_id: 100,
                component_id: null,
                component_name: null,
                component_code: null,
                name: 'Discharge pressure',
                external_key: '/test/P8_PRESSURE',
                value: 8,
                quality: 'good',
                stale: false,
                unit: 'bar',
                critical_max: null,
                eligible: true,
                value_floor: 8,
                suggested_test_value: 9,
                limits: {
                  normal_min: null,
                  normal_max: null,
                  warn_min: null,
                  warn_max: null,
                  critical_min: null,
                  critical_max: null
                }
              },
              {
                binding_id: 101,
                component_id: null,
                component_name: null,
                component_code: null,
                name: 'Motor operating status',
                value: 0,
                unit: '',
                critical_max: null,
                eligible: false,
                ineligible_reason: 'non_numeric'
              }
            ]
          }
        });
      body = route.request().postDataJSON();
      return route.fulfill({
        json: {
          anomaly_id: 501,
          machine_id: 18,
          binding_id: 100,
          status: 'open',
          test_value: body!.test_value,
          unit: 'bar',
          idempotency_key: body!.idempotency_key
        }
      });
    }
  );
  await openHealth(page);
  await page
    .getByRole('button', { name: 'Trigger test alarm', exact: true })
    .click();
  const dialog = page.getByRole('dialog', {
    name: 'Trigger test alarm',
    exact: true
  });
  await dialog
    .getByRole('switch', {
      name: 'Create work order automatically',
      exact: true
    })
    .uncheck();
  await dialog
    .getByRole('combobox', { name: 'Configured test signal', exact: true })
    .press('ArrowDown');
  await expect(
    page.getByRole('option').filter({ hasText: 'Motor operating status' })
  ).toHaveAttribute('data-combobox-disabled', 'true');
  const parameter = page
    .getByRole('option')
    .filter({ hasText: 'Discharge pressure' });
  await expect(parameter).toContainText('Measured: 8 bar');
  await dialog
    .getByRole('combobox', { name: 'Configured test signal', exact: true })
    .press('ArrowDown');
  await dialog
    .getByRole('combobox', { name: 'Configured test signal', exact: true })
    .press('Enter');
  await expect(dialog).toContainText('No configured critical limit.');
  const value = dialog.getByRole('textbox', {
    name: 'Synthetic test value',
    exact: true
  });
  await value.fill('8');
  await dialog.getByRole('checkbox').check();
  await expect(
    dialog.getByRole('button', { name: 'Trigger and notify', exact: true })
  ).toBeDisabled();
  await value.fill('12');
  await dialog.getByRole('checkbox').check();
  await dialog.screenshot({
    path: info.outputPath('all-parameter-selection.png'),
    animations: 'disabled'
  });
  await dialog
    .getByRole('button', { name: 'Trigger and notify', exact: true })
    .click();
  await expect(dialog.getByText('Test alarm recorded')).toBeVisible();
  expect(body).toMatchObject({
    binding_id: 100,
    component_id: null,
    test_value: 12
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`test alarm selects real hierarchy and retries one intent (${scheme})`, async ({
    page
  }, info) => {
    const requests: Record<string, unknown>[] = [];
    await page.route('**/api/assets/registry/?*', (route) =>
      route.fulfill({
        json: {
          results: [
            { pk: 17, name: 'Synthetic Cedar station', asset_type: 'pumphouse' }
          ]
        }
      })
    );
    await page.route('**/api/assets/registry/17/', (route) =>
      route.fulfill({
        json: {
          children: [
            { pk: 18, name: 'Synthetic pump 8', parent: 17, asset_type: 'pump' }
          ]
        }
      })
    );
    await page.route(
      '**/api/machine-health/machines/18/health/test-alarm/',
      async (route) => {
        if (route.request().method() === 'GET')
          return route.fulfill({
            json: {
              results: [
                {
                  binding_id: 100,
                  name: 'Synthetic motor winding',
                  component_id: 21,
                  component_name: 'Electric motor',
                  component_code: 'MOTOR',
                  unit: '°C',
                  critical_max: 90
                },
                {
                  binding_id: 101,
                  name: 'Synthetic motor core',
                  component_id: 21,
                  component_name: 'Electric motor',
                  component_code: 'MOTOR',
                  unit: '°C',
                  critical_max: 90
                }
              ]
            }
          });
        requests.push(route.request().postDataJSON());
        if (requests.length === 1)
          return route.fulfill({
            status: 503,
            json: { detail: 'Synthetic lost acknowledgement' }
          });
        return route.fulfill({
          json: {
            anomaly_id: 501,
            machine_id: 18,
            status: 'open',
            test_value: 99,
            unit: '°C',
            idempotency_key: requests[requests.length - 1].idempotency_key,
            binding_id: requests[requests.length - 1].binding_id
          }
        });
      }
    );
    await openHealth(page, { scheme });
    await page
      .getByRole('button', { name: 'Trigger test alarm', exact: true })
      .click();
    const dialog = page.getByRole('dialog', {
      name: 'Trigger test alarm',
      exact: true
    });
    await dialog
      .getByRole('switch', {
        name: 'Create work order automatically',
        exact: true
      })
      .uncheck();
    for (const name of [
      'Pump station',
      'Pump / equipment',
      'Equipment component',
      'Configured test signal'
    ]) {
      const input = dialog.getByRole('combobox', { name, exact: true });
      await expect(input).toBeEnabled();
      await input.focus();
      await input.press('ArrowDown');
      await input.press('Enter');
    }
    const send = dialog.getByRole('button', { name: 'Trigger and notify' });
    await expect(send).toBeDisabled();
    await dialog.getByRole('checkbox').check();
    await expect(send).toBeEnabled();
    await dialog.screenshot({
      path: info.outputPath(`test-alarm-${scheme}.png`)
    });
    await send.click();
    await expect(dialog.getByText('Alarm outcome not confirmed')).toBeVisible();
    await page.reload();
    await page
      .getByRole('button', { name: 'Trigger test alarm', exact: true })
      .click();
    await expect(
      dialog.getByText(/A previous test request is retained/)
    ).toBeVisible();
    await dialog
      .getByRole('button', { name: 'Retry same test request' })
      .click();
    await expect(dialog.getByText('Test alarm recorded')).toBeVisible();
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await expect(dialog).toContainText('Synthetic Cedar station');
    await expect(dialog).toContainText('Synthetic motor winding');
    await expect(dialog.getByRole('combobox')).toHaveCount(0);
    expect(requests).toHaveLength(2);
    expect(requests[1]).toEqual(requests[0]);
    expect(requests[0]).toMatchObject({
      binding_id: 100,
      component_id: 21,
      confirmed: true
    });
    await expect(
      dialog.getByText(/Actual measurements are unchanged/)
    ).toBeVisible();
    await dialog.screenshot({
      path: info.outputPath(`test-alarm-receipt-${scheme}.png`),
      animations: 'disabled'
    });
    await dialog.getByRole('button', { name: 'Start another test' }).click();
    const signal = dialog.getByRole('combobox', {
      name: 'Configured test signal',
      exact: true
    });
    await expect(signal).toHaveValue('');
    await expect(send).toBeDisabled();
    await signal.click();
    await page.getByRole('option', { name: /^Synthetic motor core/ }).click();
    await dialog.getByRole('checkbox').check();
    await send.click();
    await expect(dialog.getByText('Test alarm recorded')).toBeVisible();
    expect(requests).toHaveLength(3);
    expect(requests[2].binding_id).toBe(101);
    expect(requests[2].idempotency_key).not.toBe(requests[1].idempotency_key);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true);
  });
}

test('triggering a test refreshes annotations while Performance live updates are paused', async ({
  page
}, info) => {
  const specs = [
    {
      key: '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED5',
      name: 'Motor Winding Temperature 5',
      value: 40,
      unit: 'degC',
      limits: { warn_max: 80, critical_max: 90 }
    }
  ];
  await openHealth(page);
  const historyReads = await mockPerformance(page, { signals: specs });
  await page.goto(
    `/playwright/s0-native.html?${new URLSearchParams({ surface: 'machine', entry: '/machines/machine/18/performance/' })}`
  );
  await expect(page.getByTestId('pump-performance')).toBeVisible();
  await pausePerformanceLiveUpdates(page);
  await expect.poll(() => historyReads.length).toBeGreaterThan(0);
  const historyReadsBeforeTrigger = historyReads.length;
  let recorded = false;
  let reads = 0;
  await page.route(
    '**/api/machine-health/machines/18/health/signals/**',
    (route) => {
      reads += 1;
      const results = signalRows(specs).map((row) => ({
        ...row,
        test_alarms: recorded
          ? [
              {
                anomaly_id: 501,
                status: 'open',
                test_value: 99,
                unit: 'degC',
                triggered_at: '2026-10-18T09:00:00Z'
              }
            ]
          : []
      }));
      return route.fulfill({ json: { count: results.length, results } });
    }
  );
  await page.route('**/api/assets/registry/?*', (route) =>
    route.fulfill({ json: { results: [] } })
  );
  await page.route(
    '**/api/machine-health/machines/18/health/test-alarm/',
    (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({
          json: {
            results: [
              {
                binding_id: 100,
                name: 'Motor Winding Temperature 5',
                component_id: 21,
                component_name: 'Electric motor',
                component_code: 'MOTOR',
                unit: 'degC',
                critical_max: 90
              }
            ]
          }
        });
      recorded = true;
      return route.fulfill({
        json: {
          anomaly_id: 501,
          machine_id: 18,
          status: 'open',
          test_value: 99,
          unit: 'degC',
          binding_id: 100,
          idempotency_key: route.request().postDataJSON().idempotency_key
        }
      });
    }
  );
  await expect(page.getByTestId('performance-test-alarm')).toHaveCount(0);
  await page
    .getByRole('button', { name: 'Trigger test alarm', exact: true })
    .click();
  const dialog = page.getByRole('dialog', {
    name: 'Trigger test alarm',
    exact: true
  });
  await dialog
    .getByRole('switch', {
      name: 'Create work order automatically',
      exact: true
    })
    .uncheck();
  const component = dialog.getByRole('combobox', {
    name: 'Equipment component',
    exact: true
  });
  await expect(component).toBeEnabled();
  await component.focus();
  await component.press('ArrowDown');
  await page
    .getByRole('option', { name: 'Electric motor · MOTOR', exact: true })
    .click();
  await dialog
    .getByRole('combobox', { name: 'Configured test signal', exact: true })
    .click();
  await page
    .getByRole('option', { name: /^Motor Winding Temperature 5/ })
    .click();
  await dialog.getByRole('checkbox').check();
  await dialog
    .getByRole('button', { name: 'Trigger and notify', exact: true })
    .click();
  await expect(dialog.getByText('Test alarm recorded')).toBeVisible();
  await expect(dialog.getByTestId('test-alarm-recorded-target')).not.toHaveText(
    /^\s*·/
  );
  await dialog.screenshot({
    path: info.outputPath('receipt-without-station.png'),
    animations: 'disabled'
  });
  await dialog
    .getByRole('button', { name: 'Close test alarm', exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page
      .locator('[data-section="Motor winding temperature"]')
      .getByTestId('performance-test-alarm')
  ).toContainText('test 99 °C; measured 40 °C');
  await expect
    .poll(() => historyReads.length)
    .toBeGreaterThan(historyReadsBeforeTrigger);
  await expect(
    page.getByRole('switch', { name: /^Live updates:/ })
  ).not.toBeChecked();
  expect(reads).toBeGreaterThan(0);
  await page.screenshot({
    path: info.outputPath('paused-performance-test.png'),
    animations: 'disabled'
  });
});
