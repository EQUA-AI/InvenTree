import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { capturePanel, timeAssignment } from '../s4/workExecutionFixtures';
import { closeoutWork, containment, openCloseout } from './closeoutFixtures';

nativeBaseline();
const material = {
  work_order: 73,
  source: 'job_kit',
  kits_enabled: true,
  stock_visible: true,
  component: null,
  requirements: [],
  unmatched_usage: [
    {
      id: 90,
      part: null,
      stock_item: {
        pk: 91,
        hidden: false,
        serial: 'SER-91',
        batch: 'LOT-7',
        location: 'Quarantine store'
      },
      planned_quantity: null,
      issued_quantity: 1,
      used_quantity: null,
      disposition: null,
      variance_reason: 'Retained for analysis; no disposition recorded',
      source: 'walkup',
      candidate_text: 'Removed seal',
      state: 'variance_open',
      resolved_by: null,
      updated_at: '2026-10-19T09:00:00Z'
    }
  ]
};
for (const scheme of ['light', 'dark'] as const) {
  test(`P46 custody, unknown time and zero entered downtime stay distinct (${scheme})`, async ({
    page
  }, info) => {
    const { commands } = await closeoutWork(page);
    let materialReads = 0;
    await page.route(
      '**/api/tasks/work-orders/73/closeout/part-usage/',
      (route) =>
        route.fulfill({
          json: [
            {
              id: 90,
              source: 'walkup',
              state: 'variance_open',
              candidate_text: 'Removed seal',
              planned_quantity: null,
              issued_quantity: '1',
              used_quantity: null,
              disposition: '',
              version: 1
            }
          ]
        })
    );
    await page.route('**/api/tasks/work-orders/73/readiness/**', (route) =>
      route.fulfill({
        json: {
          ready: false,
          blockers: [
            {
              code: 'PART_VARIANCE_UNRESOLVED',
              message:
                'Material disposition remains unresolved; owner unavailable',
              blocking: true
            }
          ],
          warnings: []
        }
      })
    );
    await page.route('**/api/tasks/work-orders/73/materials/', (route) => {
      materialReads++;
      return route.fulfill({ json: material });
    });
    await openCloseout(page, scheme);
    const reconciliation = page.getByTestId('closeout-reconciliation');
    await expect(reconciliation).toContainText('Service downtime: unavailable');
    await expect(reconciliation).toContainText(
      'Attributed time: unavailable · coverage unknown'
    );
    expect(materialReads).toBe(0);
    await reconciliation
      .getByText('Read material custody and serial / lot records', {
        exact: true
      })
      .click();
    await expect(reconciliation).toContainText('SER-91');
    await expect(reconciliation).toContainText('LOT-7');
    expect(materialReads).toBe(1);
    await expect(
      reconciliation.getByRole('link', { name: /Stock item 91/ })
    ).toHaveAttribute('href', '/stock/item/91/');
    await expect(reconciliation).toContainText('Retained for analysis');
    await expect(reconciliation).toContainText('variance_open');
    await expect(reconciliation).toContainText('Discrepancy owners');
    await page
      .getByRole('textbox', { name: 'Downtime (minutes)', exact: true })
      .fill('0');
    await page.getByText('Read report', { exact: true }).click();
    await expect(
      page.getByTestId('prepared-closeout').locator('pre')
    ).toContainText('Downtime (minutes): 0');
    await expect(reconciliation).toContainText('Service downtime: unavailable');
    expect(commands).toEqual([]);
    await page.getByText('Read report', { exact: true }).click();
    await containment(page);
    await reconciliation.evaluate((el) =>
      window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 200)
    );
    await page.screenshot({
      path: info.outputPath(`reconciliation-detail-${scheme}.png`)
    });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`reconciliation-${scheme}.png`),
      fullPage: true
    });
  });
}

test('P46 denied custody is unavailable and never turns into usable or returned stock', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Denied' } })
  );
  await openCloseout(page);
  const reconciliation = page.getByTestId('closeout-reconciliation');
  await reconciliation
    .getByText('Read material custody and serial / lot records', {
      exact: true
    })
    .click();
  await expect(reconciliation).toContainText(
    'Parts for this job could not be read'
  );
  await expect(reconciliation).not.toContainText('No parts are required');
  expect(commands).toEqual([]);
  await containment(page);
  await page.screenshot({
    path: info.outputPath('custody-denied.png'),
    fullPage: true
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 confirmed telemetry downtime remains separate from service outage (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await closeoutWork(page);
    fixture.setOverview({
      time_assignment: timeAssignment({
        downtime_records: [
          {
            id: 4,
            binding_id: 201,
            signal: 'Pump run/stop',
            started_at: '2026-10-19T06:00:00Z',
            ended_at: '2026-10-19T06:10:00Z',
            recorded_by: 'Synthetic technician',
            recorded_at: '2026-10-19T08:00:00Z',
            reason: 'Confirmed historian stop and restart',
            supersedes: null,
            superseded: false
          }
        ]
      })
    });
    await openCloseout(page, scheme);
    const value = page.getByTestId('recorded-downtime');
    await expect(value).toContainText('Confirmed machine stop window');
    await expect(value).toContainText('Synthetic technician');
    await expect(value).toContainText('Service downtime: unavailable');
    await value.screenshot({
      path: info.outputPath(`o15-downtime-${scheme}.png`),
      animations: 'disabled'
    });
  });
}

test('O15 technician confirms a sourced stop window without claiming service return', async ({
  page
}, info) => {
  await closeoutWork(page);
  const sent: unknown[] = [];
  const proposal = {
    token: 'synthetic-signed-proposal',
    binding_id: 201,
    signal: 'Pump run/stop',
    started_at: '2026-10-19T06:00:00Z',
    ended_at: '2026-10-19T06:10:00Z'
  };
  await page.route('**/api/tasks/work-orders/73/time/**', async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ json: { proposals: [proposal], reason: null } });
    sent.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        version: 8,
        time: {
          intervals: [],
          gaps: [],
          coverage: 'unknown',
          recorded_seconds: null,
          observed_at: '2026-10-20T08:00:00Z'
        },
        downtime: [
          {
            ...proposal,
            id: 4,
            recorded_by: 'Synthetic technician',
            recorded_at: '2026-10-19T08:00:00Z',
            reason: '',
            supersedes: null,
            superseded: false
          }
        ]
      }
    });
  });
  await openCloseout(page);
  const section = page.getByTestId('recorded-downtime');
  await section
    .getByText('Read telemetry and confirm downtime', { exact: true })
    .click();
  await section.getByRole('button', { name: 'Find run/stop windows' }).click();
  await section.getByRole('combobox', { name: 'Proposed stop window' }).click();
  await page.getByRole('option').filter({ hasText: 'Pump run/stop' }).click();
  await section.screenshot({
    path: info.outputPath('o15-downtime-confirm-controls.png'),
    animations: 'disabled'
  });
  await capturePanel(page, info.outputPath('o15-downtime-confirm-page.png'));
  await section
    .getByRole('button', { name: 'Confirm machine downtime', exact: true })
    .click();
  await expect(section).toContainText('Confirmed machine stop window');
  await expect(section).toContainText('Service downtime: unavailable');
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({
    operation: 'downtime',
    expected_version: 7,
    fields: { token: proposal.token }
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 reconciliation shows recorded fitted identity and its recorder (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await closeoutWork(page);
    fixture.setOverview({
      affected_component_ref: 'SEAL:1',
      actual_completed_at: '2026-10-19T08:00:00Z',
      lifecycle_status: 'completed'
    });
    await page.route(
      '**/api/tasks/work-orders/73/closeout/part-usage/',
      (route) =>
        route.fulfill({
          json: [
            {
              id: 92,
              part: 501,
              stock_item: 91,
              source: 'walkup',
              state: 'reconciled',
              candidate_text: 'Installed seal',
              planned_quantity: null,
              issued_quantity: '1',
              used_quantity: '1',
              disposition: 'spare_installed',
              version: 1
            }
          ]
        })
    );
    const fitted = {
      id: 51,
      stock_item: 91,
      serial: 'SER-91',
      lot: 'LOT-7',
      fitted_at: '2026-10-19T08:00:00Z',
      work_order: 73,
      recorded_by: 'Synthetic reconciliation technician',
      recorded_at: '2026-10-19T08:00:00Z',
      verified_by: 'Synthetic reconciliation technician',
      verified_at: '2026-10-19T08:00:00Z',
      source: 'closeout',
      reason: 'Installed usage reconciled against custody'
    };
    await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
      route.fulfill({
        json: {
          ...material,
          component: { ref: 'SEAL:1', pk: 1, name: 'Seal slot', machine: 18 },
          unmatched_usage: [
            {
              ...material.unmatched_usage[0],
              id: 92,
              part: {
                pk: 501,
                name: 'Seal',
                ipn: 'SYNTHETIC-SEAL',
                units: 'ea',
                virtual: false
              },
              stock_item: {
                pk: 91,
                hidden: false,
                serial: 'SER-91',
                batch: 'LOT-7',
                location: 'Issued stock record'
              },
              candidate_text: 'Installed seal',
              state: 'reconciled',
              used_quantity: 1,
              disposition: 'spare_installed',
              variance_reason: 'Installed and reconciled',
              resolved_by: 'Synthetic reconciliation technician',
              fitted_record: fitted
            }
          ]
        }
      })
    );
    await openCloseout(page, scheme);
    const section = page.getByTestId('closeout-reconciliation');
    await section
      .getByText('Read material custody and serial / lot records', {
        exact: true
      })
      .click();
    await expect(section).toContainText('Recorded fitted identity');
    await expect(section).toContainText('Work completion (fit ordering basis)');
    await expect(section).toContainText(
      'Actual installation time is not separately recorded.'
    );
    await expect(section).toContainText('Synthetic reconciliation technician');
    await expect(section).toContainText('LOT-7');
    await section.screenshot({
      path: info.outputPath(`o15-reconciled-fit-${scheme}.png`),
      animations: 'disabled'
    });
    await capturePanel(
      page,
      info.outputPath(`o15-reconciled-fit-page-${scheme}.png`)
    );
  });
}

test('O15 simultaneous time and downtime retain separate original intents', async ({
  page
}, info) => {
  await closeoutWork(page);
  const proposal = {
    token: 'synthetic-window',
    binding_id: 201,
    signal: 'Pump run/stop',
    started_at: '2026-10-19T06:00:00Z',
    ended_at: '2026-10-19T06:10:00Z'
  };
  const sent: Record<string, unknown>[] = [];
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/tasks/work-orders/73/time/**', async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ json: { proposals: [proposal], reason: null } });
    const body = route.request().postDataJSON();
    sent.push(body);
    if (sent.length <= 2) {
      await held;
      return route.abort('failed');
    }
    return route.fulfill({
      json: {
        record_command_outcome: 'not_committed',
        idempotency_key: body.idempotency_key
      }
    });
  });
  await openCloseout(page);
  const section = page.getByTestId('closeout-reconciliation');
  await section
    .getByText('Read assignment provenance and native time', { exact: true })
    .click();
  const time = section.getByTestId('recorded-work-time');
  const downtime = section.getByTestId('recorded-downtime');
  await time.getByRole('button', { name: 'Start work', exact: true }).click();
  await downtime
    .getByText('Read telemetry and confirm downtime', { exact: true })
    .click();
  await downtime.getByRole('button', { name: 'Find run/stop windows' }).click();
  await downtime
    .getByRole('combobox', { name: 'Proposed stop window' })
    .click();
  await page.getByRole('option').filter({ hasText: 'Pump run/stop' }).click();
  await downtime
    .getByRole('button', { name: 'Confirm machine downtime', exact: true })
    .click();
  await expect.poll(() => sent.length).toBe(2);
  const retained = await page.evaluate(() =>
    Object.entries(localStorage)
      .filter(([key]) => key.startsWith('aimms-record-command:'))
      .map(([key, value]) => ({ key, intent: JSON.parse(value) }))
  );
  expect(retained).toHaveLength(2);
  expect(retained.find((row) => row.key.endsWith(':time'))?.intent).toEqual(
    sent[0]
  );
  expect(retained.find((row) => row.key.endsWith(':downtime'))?.intent).toEqual(
    sent[1]
  );
  expect(sent[0].operation).toBe('start');
  expect(sent[1].operation).toBe('downtime');
  release();
  await expect(time).toContainText('Original outcome remains unconfirmed');
  await expect(downtime).toContainText('Original outcome remains unconfirmed');
  // Upgrade a prior unscoped start without losing the concurrent downtime.
  await page.evaluate(() => {
    const key = Object.keys(localStorage).find(
      (key) => key.startsWith('aimms-record-command:') && key.endsWith(':time')
    )!;
    localStorage.setItem(key.slice(0, -5), localStorage.getItem(key)!);
    localStorage.removeItem(key);
  });
  await page.reload();
  await page
    .getByTestId('closeout-reconciliation')
    .getByText('Read assignment provenance and native time', { exact: true })
    .click();
  await time.getByRole('button', { name: 'Retry retained operation' }).click();
  await expect(time).toContainText('Original operation was not applied.');
  await downtime
    .getByRole('button', { name: 'Retry retained operation' })
    .click();
  await expect(downtime).toContainText('Original operation was not applied.');
  expect(sent[2]).toEqual(sent[0]);
  expect(sent[3]).toEqual(sent[1]);
  await capturePanel(page, info.outputPath('o15-review-separate-intents.png'));
});

test('O15 an installation without completion evidence is a pending candidate', async ({
  page
}, info) => {
  const fixture = await closeoutWork(page);
  fixture.setOverview({ affected_component_ref: 'SEAL:1' });
  await page.route(
    '**/api/tasks/work-orders/73/closeout/part-usage/',
    (route) =>
      route.fulfill({
        json: [
          {
            id: 92,
            part: 501,
            stock_item: 91,
            source: 'walkup',
            state: 'reconciled',
            disposition: 'spare_installed',
            candidate_text: 'Installed seal',
            used_quantity: '1',
            issued_quantity: '1',
            planned_quantity: null,
            version: 1
          }
        ]
      })
  );
  await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
    route.fulfill({
      json: {
        ...material,
        unmatched_usage: [
          {
            ...material.unmatched_usage[0],
            id: 92,
            part: {
              pk: 501,
              name: 'Seal',
              ipn: 'SYNTHETIC-SEAL',
              units: 'ea',
              virtual: false
            },
            candidate_text: 'Installed seal',
            used_quantity: 1,
            resolved_by: 'Synthetic recorder',
            variance_reason: 'Installed, completion not recorded',
            stock_item: {
              pk: 91,
              hidden: false,
              serial: 'SER-91',
              batch: 'LOT-7',
              location: 'Issued stock record'
            },
            state: 'reconciled',
            disposition: 'spare_installed',
            fitted_record: {
              id: 51,
              stock_item: 91,
              serial: 'SER-91',
              lot: 'LOT-7',
              fitted_at: null,
              work_order: 73,
              recorded_by: 'Synthetic recorder',
              recorded_at: '2026-10-19T08:00:00Z',
              verified_by: null,
              verified_at: null,
              source: 'closeout',
              reason: 'Installed, completion not recorded'
            }
          }
        ]
      }
    })
  );
  await openCloseout(page);
  const section = page.getByTestId('closeout-reconciliation');
  await section
    .getByText('Read material custody and serial / lot records', {
      exact: true
    })
    .click();
  await expect(section).toContainText(
    'Installation requires review — not current fit'
  );
  await expect(section).toContainText(
    'Work completion (fit ordering basis): Not recorded'
  );
  await expect(section).toContainText('Verified by: Not recorded');
  await expect(
    section.getByText('Recorded fitted identity', { exact: true })
  ).toHaveCount(0);
  await capturePanel(
    page,
    info.outputPath('o15-review-pending-installation.png')
  );
});
