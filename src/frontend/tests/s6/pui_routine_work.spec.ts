import { expect, test } from '@playwright/test';
import type { RoutineWork } from '../../lib/types/RoutineWork';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { nativeBaseline } from '../s0/guards';
import {
  capturePanel,
  expectNoPageOverflow,
  mockAssistantOpen,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  savedRequest,
  workOrder
} from '../s4/workExecutionFixtures';
import { closeoutWork, openCloseout } from './closeoutFixtures';

nativeBaseline();
function routine(type: string): RoutineWork {
  return {
    rule: 'routine_work.v1',
    work_order_type: type,
    procedures_state: 'read',
    readings_state: 'read',
    applications: [
      {
        id: 81,
        revision: 2,
        snapshot_hash: 'a'.repeat(64),
        applied_by: 'Synthetic planner',
        applied_at: '2026-10-19T08:00:00Z',
        tasks: [
          {
            id: 901,
            step: {
              key: 'synthetic-step-1',
              sequence: 1,
              title: 'Synthetic recorded task',
              step_type: 'inspection',
              instruction: 'Synthetic instruction from the applied revision',
              required: true,
              required_permission: 'tasks.execute_workorder',
              value_type: 'boolean',
              unit: '',
              min_value: null,
              max_value: null,
              allowed_values: []
            },
            status: 'completed',
            version: 3,
            value: { boolean: false },
            note: 'Synthetic retained observation',
            disposition_reason: '',
            recorded_by: 'Synthetic technician',
            recorded_at: '2026-10-19T09:00:00Z'
          }
        ]
      }
    ],
    readings: [
      {
        id: 701,
        label: 'Synthetic reference check',
        phase: 'before',
        raw_text: '0 bar',
        value: '0',
        unit: 'bar',
        expected_min: '0',
        expected_max: '1',
        required: true,
        verification_state: 'pending',
        warnings: [],
        disposition_reason: '',
        step_execution: 901,
        recorded_by_name: 'Synthetic technician',
        recorded_at: '2026-10-19T09:00:00Z'
      }
    ]
  };
}
function overview(type: string): WorkOrderOverview {
  return { ...workOrder(), work_order_type: type, routine_work: routine(type) };
}
for (const type of ['preventive', 'inspection', 'calibration']) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`P51 ${type} retains recorded tasks, readings and references (${scheme})`, async ({
      page
    }, info) => {
      const requests = await mockExecutionWork(page, overview(type));
      await openExecutionPanel(page, 'work', scheme);
      const section = page.getByTestId('routine-work');
      await expect(section).toBeVisible();
      await section
        .getByRole('button', { name: /1. Synthetic recorded task/ })
        .click();
      await expect(section).toContainText('Recorded value: false');
      await expect(section).toContainText(
        'Synthetic instruction from the applied revision'
      );
      await expect(section).toContainText('synthetic-step-1');
      await expect(section).toContainText('a'.repeat(64));
      await expect(section).toContainText('0 bar · pending');
      await expect(section).toContainText('Recorded limits: 0 – 1');
      await expect(section).toContainText('Synthetic technician');
      await expect(section).toContainText('Before');
      if (type === 'calibration') {
        await expect(section).toContainText(
          'Calibration traceability incomplete'
        );
        await expect(section).toContainText(
          'As-found/as-left designation and test conditions are not supplied.'
        );
        await expect(section).toContainText(
          'Returned before/after readings and any recorded limits do not establish calibration acceptance.'
        );
      }
      await expect(
        section.getByRole('link', { name: 'Open native completion' })
      ).toHaveAttribute('href', /work_panel=closeout/);
      await expectNoPageOverflow(page);
      await capturePanel(page, info.outputPath(`${type}-${scheme}.png`));
      // Capture the actual page region without scrolling it under the sticky header.
      const clip = await section.boundingBox();
      expect(clip).not.toBeNull();
      await page.screenshot({
        path: info.outputPath(`${type}-${scheme}-detail.png`),
        fullPage: true,
        clip: clip!
      });
      await section
        .getByRole('link', { name: 'View applied references and authority' })
        .click();
      await expect(page.getByTestId('destination')).toContainText(
        'work_panel=plan'
      );
      await expect(
        page.getByRole('tabpanel', { name: 'Plan & Safety', exact: true })
      ).toContainText('not permission to begin physical work');
      expect(onlyReads(requests)).toBe(true);
    });
  }
}
test('P51 missing, disabled and empty sources never become completed work', async ({
  page
}, info) => {
  const value = overview('inspection');
  value.routine_work = null;
  await mockExecutionWork(page, value);
  await openExecutionPanel(page);
  await expect(page.getByTestId('routine-work')).toContainText(
    'Routine records unavailable'
  );
  await capturePanel(page, info.outputPath('routine-missing.png'));
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({
      json: {
        ...value,
        routine_work: {
          ...routine('inspection'),
          applications: [],
          readings: [],
          procedures_state: 'not_enabled',
          readings_state: 'not_enabled'
        }
      }
    })
  );
  await page.reload();
  await expect(page.getByTestId('routine-work')).toContainText(
    'Procedure records are not enabled'
  );
  await expect(page.getByTestId('routine-work')).toContainText(
    'Closeout reading records are not enabled'
  );
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({
      json: {
        ...value,
        routine_work: {
          ...routine('inspection'),
          applications: [],
          readings: []
        }
      }
    })
  );
  await page.reload();
  await expect(page.getByTestId('routine-work')).toContainText(
    'Required tasks and their completion are not established'
  );
  await expect(page.getByTestId('routine-work')).toContainText(
    'does not establish that required measurements were taken'
  );
  await expectNoPageOverflow(page);
});
test('P51 selected saved request opens the existing assistant and keeps original Work', async ({
  page
}, info) => {
  const value = overview('preventive');
  value.investigation = {
    rule: 'investigation.v1',
    packet: null,
    explanation: null,
    findings: [],
    finding_history: 'not_applicable',
    intervention: null,
    requests: { available: true, reason: '', items: [savedRequest()] }
  };
  const requests = await mockExecutionWork(page, value);
  await mockAssistantOpen(page);
  const entry = encodeURIComponent(
    '/maintenance/work-orders/73/?work_panel=work&investigation_request=401'
  );
  await page.goto(
    `/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&work_access=reader&entry=${entry}`
  );
  const section = page.getByTestId('routine-work');
  await section
    .getByRole('button', { name: 'Fault found during this work', exact: true })
    .click();
  await expect(section).toContainText(
    'Linked corrective Work is created only by an explicit command'
  );
  await section
    .getByRole('button', { name: 'Discuss request 401', exact: true })
    .click();
  await expect(page.getByTestId('ai-chat-work-focus')).toContainText('WO-73');
  await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
    'Request 401'
  );
  await expect(page.getByTestId('destination')).toContainText(
    '/maintenance/work-orders/73/?work_panel=work&investigation_request=401'
  );
  await capturePanel(page, info.outputPath('routine-request-chat.png'));
  expect(onlyReads(requests)).toBe(true);
});
test('P51 normal calibration uses the existing native Closeout without diagnosis', async ({
  page
}) => {
  const { setOverview, commands } = await closeoutWork(page);
  setOverview({
    work_order_type: 'calibration',
    routine_work: routine('calibration')
  });
  await openCloseout(page);
  const panel = page.getByRole('tabpanel', { name: 'Closeout', exact: true });
  await expect(
    panel.getByRole('button', {
      name: 'Update maintenance record',
      exact: true
    })
  ).toBeVisible();
  await expect(page.getByTestId('destination')).toContainText(
    'work_panel=closeout'
  );
  expect(commands).toEqual([]);
});
test('P51 corrective and unknown types retain the native workspace', async ({
  page
}) => {
  await mockExecutionWork(page, workOrder());
  await openExecutionPanel(page);
  await expect(page.getByTestId('work-brief')).toBeVisible();
  await expect(page.getByTestId('routine-work')).toHaveCount(0);
  for (const type of ['other', 'unknown']) {
    await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
      route.fulfill({
        json: {
          ...workOrder(),
          title: `Synthetic ${type} work`,
          work_order_type: type
        }
      })
    );
    await page.reload();
    // Prove the new overview rendered before asserting the routine section absent.
    await expect(
      page.getByRole('heading', {
        name: `WO-73: Synthetic ${type} work`,
        exact: true
      })
    ).toBeVisible();
    await expect(page.getByTestId('work-brief')).toBeVisible();
    await expect(page.getByTestId('routine-work')).toHaveCount(0);
  }
});

test('P51 native site readiness still blocks an inspection update', async ({
  page
}) => {
  const { setOverview, commands } = await closeoutWork(page, { ready: false });
  setOverview({
    work_order_type: 'inspection',
    routine_work: routine('inspection')
  });
  await openCloseout(page);
  const panel = page.getByRole('tabpanel', { name: 'Closeout', exact: true });
  await expect(panel).toContainText('Finish the active capture review');
  await expect(
    panel.getByRole('button', {
      name: 'Update maintenance record',
      exact: true
    })
  ).toBeDisabled();
  expect(commands).toEqual([]);
});

test('P51 reading append and retest refresh the mounted Work overview', async ({
  page
}) => {
  const { setOverview, commands } = await closeoutWork(page);
  const records = routine('inspection');
  records.readings = [];
  setOverview({ work_order_type: 'inspection', routine_work: records });
  let overviewReads = 0;
  page.on('request', (request) => {
    if (
      new URL(request.url()).pathname === '/api/kanban/work-orders/73/overview/'
    )
      overviewReads++;
  });
  const writes: unknown[] = [];
  await page.route(
    '**/api/tasks/work-orders/73/closeout/readings/',
    async (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({ json: records.readings });
      expect(route.request().method()).toBe('POST');
      const body = route.request().postDataJSON();
      writes.push(body);
      const reading = {
        ...routine('inspection').readings[0],
        ...body,
        id: 801 + writes.length,
        phase: 'after',
        value: body.raw_text,
        verification_state: 'verified',
        expected_min: null,
        expected_max: null
      };
      // A later GET supplies the provider result; this does not mutate cached props.
      records.readings = [...records.readings, reading];
      setOverview({ routine_work: { ...records } });
      return route.fulfill({ status: 201, json: reading });
    }
  );
  // Reading capture requires native closeout authority; the viewer harness
  // intentionally has none. Reuse the existing authorized closeout fixture.
  await openCloseout(page);
  await page.getByRole('tab', { name: 'Work', exact: true }).click();
  const section = page.getByTestId('routine-work');
  await expect(section).toContainText('No closeout readings returned');
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  expect(overviewReads).toBe(1);
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  for (const [label, value] of [
    ['Synthetic initial reading', '4.2'],
    ['Synthetic retest', '5.1']
  ]) {
    await tabs
      .getByRole('tab', { name: 'Verify & Return', exact: true })
      .click();
    const verify = page.getByRole('tabpanel', {
      name: 'Verify & Return',
      exact: true
    });
    await verify
      .getByRole('textbox', { name: 'Label', exact: true })
      .fill(label);
    await verify
      .getByRole('textbox', { name: 'Value', exact: true })
      .fill(value);
    await verify
      .getByRole('textbox', { name: 'Unit', exact: true })
      .fill('bar');
    await verify
      .getByRole('button', { name: 'Add reading', exact: true })
      .click();
    await expect(
      verify.getByRole('textbox', { name: 'Label', exact: true })
    ).toHaveValue('');
    await expect.poll(() => overviewReads).toBe(writes.length + 1);
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await tabs.getByRole('tab', { name: 'Work', exact: true }).click();
    const saved = section.getByRole('region', {
      name: `Saved reading ${801 + writes.length}`,
      exact: true
    });
    await expect(saved).toContainText(label);
    await expect(saved).toContainText(
      `${value} bar · Value parsed · no limits recorded · not evaluated`
    );
    await expect(section).not.toContainText('No closeout readings returned');
  }
  await expect(
    section.getByRole('region', { name: /^Saved reading/ })
  ).toHaveCount(2);
  await expect(section).toContainText('Synthetic initial reading');
  expect(writes).toEqual([
    {
      label: 'Synthetic initial reading',
      raw_text: '4.2',
      unit: 'bar',
      required: false
    },
    { label: 'Synthetic retest', raw_text: '5.1', unit: 'bar', required: false }
  ]);
  expect(commands).toEqual([]);
  await expectNoPageOverflow(page);
});

for (const source of ['missing', 'disabled', 'empty']) {
  test(`P51 calibration ${source} source does not claim retained readings`, async ({
    page
  }, info) => {
    const value = overview('calibration');
    if (source === 'missing') value.routine_work = null;
    else {
      value.routine_work!.readings = [];
      if (source === 'disabled')
        value.routine_work!.readings_state = 'not_enabled';
    }
    await mockExecutionWork(page, value);
    await openExecutionPanel(page);
    const section = page.getByTestId('routine-work');
    await expect(section).toContainText(
      source === 'missing'
        ? 'Routine records unavailable'
        : source === 'disabled'
          ? 'Closeout reading records are not enabled'
          : 'No closeout readings returned'
    );
    await expect(section).toContainText('Calibration traceability incomplete');
    await expect(section).toContainText('As-found/as-left designation');
    await expect(section).not.toContainText('readings are retained');
    await expect(section).not.toContainText('Returned before/after readings');
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`calibration-${source}.png`));
  });
}

for (const type of ['preventive', 'inspection', 'calibration']) {
  test(`P51 ${type} parsed reading without limits is not evaluated`, async ({
    page
  }, info) => {
    const value = overview(type);
    Object.assign(value.routine_work!.readings[0], {
      value: '4.200000',
      raw_text: '4.2 bar',
      verification_state: 'verified',
      expected_min: null,
      expected_max: null
    });
    await mockExecutionWork(page, value);
    await openExecutionPanel(page);
    const reading = page.getByRole('region', {
      name: 'Saved reading 701',
      exact: true
    });
    await expect(reading).toContainText(
      '4.200000 bar · Value parsed · no limits recorded · not evaluated'
    );
    await expect(reading).toContainText(
      'Recorded limits: Not recorded – Not recorded'
    );
    await expect(reading).not.toContainText('bar · verified');
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`${type}-unbounded.png`));
  });
}

test('P51 recorded zero bounds preserve the native reading state', async ({
  page
}) => {
  const value = overview('inspection');
  value.routine_work!.readings = [
    { expected_min: '0', expected_max: null },
    { expected_min: null, expected_max: '0' },
    { expected_min: '0', expected_max: '1' }
  ].map((bounds, i) => ({
    ...routine('inspection').readings[0],
    id: 710 + i,
    verification_state: 'verified',
    ...bounds
  }));
  await mockExecutionWork(page, value);
  await openExecutionPanel(page);
  for (const [i, limits] of [
    '0 – Not recorded',
    'Not recorded – 0',
    '0 – 1'
  ].entries()) {
    const reading = page.getByRole('region', {
      name: `Saved reading ${710 + i}`,
      exact: true
    });
    await expect(reading).toContainText('0 bar · verified');
    await expect(reading).toContainText(`Recorded limits: ${limits}`);
    await expect(reading).not.toContainText('not evaluated');
  }
});

for (const scheme of ['light', 'dark'] as const) {
  test(`P51 disposition separates unknown authority and earlier outcome from the original recorder (${scheme})`, async ({
    page
  }, info) => {
    const value = overview('calibration');
    value.routine_work!.readings = [
      'Synthetic deviation review reason',
      ''
    ].map((reason, i) => ({
      ...routine('calibration').readings[0],
      id: 720 + i,
      verification_state: 'dispositioned',
      disposition_reason: reason,
      recorded_by_name: 'Synthetic original technician'
    }));
    await mockExecutionWork(page, value);
    await openExecutionPanel(page, 'work', scheme);
    for (const [i, reason] of [
      'Synthetic deviation review reason',
      'Not recorded'
    ].entries()) {
      const reading = page.getByRole('region', {
        name: `Saved reading ${720 + i}`,
        exact: true
      });
      await expect(reading).toContainText('bar · dispositioned');
      await expect(reading).toContainText(
        'Reading recorded by: Synthetic original technician'
      );
      await expect(reading).toContainText('Reading recorded at:');
      const disposition = reading.getByRole('region', {
        name: `Reading ${720 + i} disposition`,
        exact: true
      });
      await expect(disposition).toContainText(`Disposition reason: ${reason}`);
      await expect(disposition).toContainText(
        'Disposition recorded by: Not recorded'
      );
      await expect(disposition).toContainText(
        'Disposition recorded at: Not recorded'
      );
      await expect(disposition).toContainText(
        'Earlier verification outcome is not retained in this reading record.'
      );
      await expect(disposition).not.toContainText(
        'Synthetic original technician'
      );
      await expect(disposition).not.toContainText('19 Oct 2026');
    }
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`disposition-${scheme}.png`));
    const clip = await page.getByTestId('routine-work').boundingBox();
    expect(clip).not.toBeNull();
    await page.screenshot({
      path: info.outputPath(`disposition-${scheme}-detail.png`),
      fullPage: true,
      clip: clip!
    });
  });
}
