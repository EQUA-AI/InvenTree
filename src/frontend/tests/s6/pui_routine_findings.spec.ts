import { expect, test } from '@playwright/test';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { nativeBaseline } from '../s0/guards';
import {
  capturePanel,
  expectNoPageOverflow,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  workOrder
} from '../s4/workExecutionFixtures';

nativeBaseline();
function routine(canCreate = true): WorkOrderOverview {
  return {
    ...workOrder(),
    work_order_type: 'inspection',
    routine_work: {
      rule: 'routine_work.v1',
      work_order_type: 'inspection',
      procedures_state: 'read',
      readings_state: 'read',
      can_create_linked: canCreate,
      create_unavailable_reason: canCreate ? null : 'permission',
      linked_corrective: [],
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
                key: 'o21-step',
                sequence: 1,
                title: 'Inspect seal',
                step_type: 'inspection',
                instruction: 'Synthetic instruction',
                required: true,
                required_permission: 'tasks.execute_workorder',
                value_type: 'boolean',
                unit: '',
                min_value: null,
                max_value: null,
                allowed_values: []
              },
              status: 'completed',
              version: 2,
              value: { boolean: false },
              note: 'Observed drip',
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
          label: 'Seal check',
          phase: 'before',
          raw_text: '0 bar',
          value: '0',
          unit: 'bar',
          expected_min: null,
          expected_max: null,
          required: false,
          verification_state: 'pending',
          warnings: [],
          disposition_reason: '',
          step_execution: 901,
          recorded_by_name: 'Synthetic technician',
          recorded_at: '2026-10-19T09:00:00Z'
        }
      ]
    }
  };
}
const link = {
  id: 80,
  reference: 'WO-80',
  title: 'Inspect observed drip',
  finding: 'Drip observed at seal during inspection',
  reading_id: 701,
  step_id: 901
};
function receipt(body: any) {
  return {
    id: 80,
    reference: 'WO-80',
    process_receipt: {
      command: 'create',
      idempotency_key: body.idempotency_key,
      work_order_id: 80,
      event_id: 911,
      lifecycle_version: 1,
      metadata: {
        origin_kind: 'routine_finding',
        outcome: 'linked_created',
        routine_work_id: 73,
        prior_version: body.prior_version
      }
    }
  };
}
async function expandFault(page: Parameters<typeof openExecutionPanel>[0]) {
  await page
    .getByTestId('routine-work')
    .getByRole('button', { name: 'Fault found during this work', exact: true })
    .click();
  // Filling while Collapse is still clipping can scroll its panel internally.
  // At transition end it resets that scroll, pushing the chooser off-screen.
  await expect(
    page.getByRole('region', {
      name: 'Fault found during this work',
      exact: true
    })
  ).toHaveCSS('overflow', 'visible');
}
async function chooseFindingOption(
  page: Parameters<typeof openExecutionPanel>[0],
  field: string,
  label: string,
  capture?: string
) {
  const chooser = page.getByRole('combobox', { name: field, exact: true });
  await chooser.evaluate((element) =>
    element.scrollIntoView({ block: 'center' })
  );
  await expect(chooser).toBeInViewport({ ratio: 1 });
  await chooser.click();
  const option = page.getByRole('option', { name: label, exact: true });
  // Observe the positioned option before clicking, as a user must. This also
  // prevents automation scrolling a transient, not-yet-positioned portal.
  await expect(option).toBeVisible();
  await expect(option).toBeInViewport({ ratio: 1 });
  if (capture) await page.screenshot({ path: capture, animations: 'disabled' });
  await option.click();
}
async function openFault(
  page: Parameters<typeof openExecutionPanel>[0],
  scheme: 'light' | 'dark' = 'light'
) {
  await openExecutionPanel(page, 'work', scheme);
  await expandFault(page);
}
async function captureFinding(
  page: Parameters<typeof openExecutionPanel>[0],
  path: string
) {
  const form = page.getByTestId('routine-finding-form');
  const observation = form.getByRole('textbox', {
    name: 'Finding / observation',
    exact: true
  });
  if ((page.viewportSize()?.width ?? 0) < 500 && (await observation.count())) {
    // A long phone form scrolls above the native dock. Show both real viewports
    // instead of clipping the whole form underneath the fixed navigation.
    await observation.evaluate((element) =>
      element.scrollIntoView({ block: 'center' })
    );
    await page.screenshot({
      path: path.replace(/\.png$/, '-top.png'),
      animations: 'disabled'
    });
    const action = form.getByRole('button', {
      name: 'Create linked corrective Work',
      exact: true
    });
    await action.evaluate((element) =>
      element.scrollIntoView({ block: 'center' })
    );
    await expect
      .poll(() =>
        action.evaluate((element) => {
          const box = element.getBoundingClientRect();
          const hit = document.elementFromPoint(
            box.x + box.width / 2,
            box.y + box.height / 2
          );
          return !!hit && element.contains(hit);
        })
      )
      .toBe(true);
    await page.screenshot({ path, animations: 'disabled' });
    return;
  }
  // Center the shorter state/desktop form clear of the fixed header and dock.
  await form.evaluate((element) => element.scrollIntoView({ block: 'center' }));
  await form.screenshot({ path, animations: 'disabled' });
}
async function captureHistory(
  page: Parameters<typeof openExecutionPanel>[0],
  path: string
) {
  const relation = page
    .getByRole('tabpanel', { name: 'History', exact: true })
    .getByTestId('routine-links');
  // Keep the actual phone navigation dock below the relation in this viewport.
  await relation.evaluate((element) =>
    element.scrollIntoView({ block: 'center' })
  );
  await expect
    .poll(() =>
      relation.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const hit = document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2
        );
        return !!hit && element.contains(hit);
      })
    )
    .toBe(true);
  const box = await relation.boundingBox();
  expect(box).not.toBeNull();
  await page.screenshot({ path, clip: box!, animations: 'disabled' });
}
async function fill(page: Parameters<typeof openExecutionPanel>[0]) {
  await page.getByLabel('Corrective Work title').fill(link.title);
  await page.getByLabel('Finding / observation').fill(link.finding);
}
for (const scheme of ['light', 'dark'] as const) {
  test(`O21 explicit creation retains routine work and history (${scheme})`, async ({
    page
  }, info) => {
    const value = routine();
    const requests = await mockExecutionWork(page, value);
    const commands: any[] = [];
    let finish!: () => void;
    const gate = new Promise<void>((resolve) => {
      finish = resolve;
    });
    await page.route(
      '**/api/kanban/work-orders/commands/create/',
      async (route) => {
        const body = route.request().postDataJSON();
        commands.push(body);
        await gate;
        value.routine_work!.linked_corrective = [link];
        await route.fulfill({ status: 201, json: receipt(body) });
      }
    );
    await openFault(page, scheme);
    expect(onlyReads(requests)).toBe(true);
    await fill(page);
    await page
      .getByRole('combobox', {
        name: 'Related reading (optional)',
        exact: true
      })
      .click();
    await page
      .getByRole('option', { name: 'Seal check · before · 0 bar' })
      .click();
    await page
      .getByRole('combobox', {
        name: 'Related task step (optional)',
        exact: true
      })
      .click();
    await page.getByRole('option', { name: '1. Inspect seal' }).click();
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`form-${scheme}.png`));
    await captureFinding(page, info.outputPath(`form-${scheme}-detail.png`));
    await page
      .getByRole('button', {
        name: 'Create linked corrective Work',
        exact: true
      })
      .click();
    await expect(page.getByTestId('routine-finding-form')).toContainText(
      'Creating linked corrective Work…'
    );
    expect(commands).toHaveLength(1);
    expect(commands[0]).toMatchObject({
      machine: 18,
      recurrence_of: 73,
      prior_version: value.lifecycle_version,
      corrective_origin_kind: 'routine_finding',
      work_order_type: 'corrective',
      corrective_finding: link.finding,
      corrective_reading: 701,
      corrective_step: 901,
      title: link.title
    });
    finish();
    await expect(page.getByTestId('routine-finding-form')).toContainText(
      'Linked corrective Work created'
    );
    await expect(
      page
        .getByTestId('routine-finding-form')
        .getByRole('link', { name: 'WO-80', exact: true })
    ).toHaveAttribute('href', /work-orders\/80/);
    const retainedReading = page.getByTestId('routine-reading-701');
    await expect(retainedReading).toContainText('0 bar');
    await expect(retainedReading).toContainText('Not evaluated');
    await capturePanel(page, info.outputPath(`success-${scheme}.png`));
    await captureFinding(page, info.outputPath(`success-${scheme}-detail.png`));
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await expect(
      page.getByRole('tabpanel', { name: 'History', exact: true })
    ).toContainText(link.finding);
    await captureHistory(page, info.outputPath(`history-linked-${scheme}.png`));
    expect(commands).toHaveLength(1);
  });
  test(`O21 viewers read saved links in Work and History (${scheme})`, async ({
    page
  }, info) => {
    const value = routine(false);
    value.routine_work!.linked_corrective = [link];
    const requests = await mockExecutionWork(page, value);
    await openFault(page, scheme);
    await expect(
      page.getByRole('link', { name: /WO-80 · Inspect/ })
    ).toHaveAttribute('href', /work-orders\/80/);
    await expect(page.getByLabel('Corrective Work title')).toHaveCount(0);
    await expect(page.getByTestId('routine-work')).toContainText(
      'requires Work creation permission'
    );
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`viewer-${scheme}.png`));
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await expect(
      page.getByRole('tabpanel', { name: 'History', exact: true })
    ).toContainText(link.finding);
    await captureHistory(page, info.outputPath(`history-viewer-${scheme}.png`));
    expect(onlyReads(requests)).toBe(true);
  });
  for (const available of [true, false]) {
    test(`O21 corrective origin ${available ? 'available' : 'unavailable'} (${scheme})`, async ({
      page
    }, info) => {
      const value = workOrder();
      value.routine_origin = available
        ? { ...link, id: 72, reference: 'WO-72', state: 'available' }
        : { state: 'unavailable' };
      const requests = await mockExecutionWork(page, value);
      await openExecutionPanel(page, 'work', scheme);
      const panel = page.getByRole('tabpanel', { name: 'Work', exact: true });
      if (available)
        await expect(
          panel.getByRole('link', { name: 'Found during WO-72' })
        ).toHaveAttribute('href', /work-orders\/72/);
      else {
        await expect(panel).toContainText(
          'Found during routine Work · source unavailable'
        );
        await expect(
          panel.getByRole('link', { name: /Found during WO/ })
        ).toHaveCount(0);
      }
      await capturePanel(
        page,
        info.outputPath(`origin-${available}-${scheme}.png`)
      );
      await page.getByRole('tab', { name: 'History', exact: true }).click();
      await expect(
        page.getByRole('tabpanel', { name: 'History', exact: true })
      ).toContainText(available ? 'Found during WO-72' : 'source unavailable');
      await captureHistory(
        page,
        info.outputPath(`history-origin-${available}-${scheme}.png`)
      );
      expect(onlyReads(requests)).toBe(true);
    });
  }
}
test('O21 uncertain command survives reload and permission refusal; only exact replay confirms', async ({
  page
}, info) => {
  await mockExecutionWork(page, routine());
  const commands: any[] = [];
  await page.route(
    '**/api/kanban/work-orders/commands/create/',
    async (route) => {
      const body = route.request().postDataJSON();
      commands.push(body);
      if (commands.length === 1) await route.abort('failed');
      else if (commands.length === 2)
        await route.fulfill({ status: 403, json: { detail: 'Unavailable' } });
      else if (commands.length === 3)
        await route.fulfill({
          status: 201,
          json: { ...receipt(body), id: 99 }
        });
      else await route.fulfill({ status: 201, json: receipt(body) });
    }
  );
  await openFault(page, 'dark');
  await fill(page);
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Creation outcome unknown'
  );
  await page.reload();
  await expandFault(page);
  const check = page.getByRole('button', {
    name: 'Check original creation command',
    exact: true
  });
  for (let count = 2; count <= 4; count++) {
    await check.click();
    await expect.poll(() => commands.length).toBe(count);
    await expect(page.getByTestId('routine-finding-form')).toContainText(
      count === 4
        ? 'Linked corrective Work created'
        : 'Creation outcome unknown'
    );
    expect(commands[count - 1]).toEqual(commands[0]);
    if (count === 2) {
      await capturePanel(page, info.outputPath('uncertain-dark.png'));
      await captureFinding(page, info.outputPath('uncertain-dark-detail.png'));
    }
  }
  await expect(page.getByLabel('Corrective Work title')).toHaveCount(0);
});
test('O21 initial refusal retains observation until a successful latest review', async ({
  page
}, info) => {
  const value = routine();
  await mockExecutionWork(page, value);
  const commands: any[] = [];
  await page.route(
    '**/api/kanban/work-orders/commands/create/',
    async (route) => {
      const body = route.request().postDataJSON();
      commands.push(body);
      if (commands.length === 1)
        await route.fulfill({
          status: 409,
          json: { detail: 'Work has changed.', code: 'STALE_VERSION' }
        });
      else await route.fulfill({ status: 201, json: receipt(body) });
    }
  );
  await openFault(page);
  await fill(page);
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Linked corrective Work refused'
  );
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    link.finding
  );
  await capturePanel(page, info.outputPath('refused-light.png'));
  await page.reload();
  await expandFault(page);
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    link.finding
  );
  value.lifecycle_version += 1;
  await page
    .getByRole('button', { name: 'Review latest linked Work', exact: true })
    .click();
  await expect(page.getByLabel('Finding / observation')).toHaveValue(
    link.finding
  );
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Linked corrective Work created'
  );
  expect(commands).toHaveLength(2);
  expect(commands[1].idempotency_key).not.toBe(commands[0].idempotency_key);
  expect(commands[1].prior_version).toBe(value.lifecycle_version);
});

test('O21 exact stale replay proves noncommit and permits latest-version review', async ({
  page
}) => {
  const value = routine();
  await mockExecutionWork(page, value);
  const commands: any[] = [];
  await page.route(
    '**/api/kanban/work-orders/commands/create/',
    async (route) => {
      const body = route.request().postDataJSON();
      commands.push(body);
      if (commands.length === 1) await route.abort('failed');
      else if (commands.length === 2)
        await route.fulfill({
          status: 409,
          json: { code: 'STALE_VERSION', detail: 'Work has changed.' }
        });
      else await route.fulfill({ status: 201, json: receipt(body) });
    }
  );
  await openFault(page);
  await fill(page);
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Creation outcome unknown'
  );
  await page
    .getByRole('button', {
      name: 'Check original creation command',
      exact: true
    })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Linked corrective Work refused'
  );
  expect(commands[1]).toEqual(commands[0]);
  value.lifecycle_version += 1;
  await page
    .getByRole('button', { name: 'Review latest linked Work', exact: true })
    .click();
  await expect(page.getByLabel('Finding / observation')).toHaveValue(
    link.finding
  );
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Linked corrective Work created'
  );
  expect(commands[2].prior_version).toBe(value.lifecycle_version);
  expect(commands[2].idempotency_key).not.toBe(commands[0].idempotency_key);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O21 review confirmed creation starts an empty finding (${scheme})`, async ({
    page
  }, info) => {
    const value = routine();
    await mockExecutionWork(page, value);
    const commands: any[] = [];
    await page.route(
      '**/api/kanban/work-orders/commands/create/',
      async (route) => {
        const body = route.request().postDataJSON();
        commands.push(body);
        value.routine_work!.linked_corrective = [link];
        await route.fulfill({ status: 201, json: receipt(body) });
      }
    );
    await openFault(page, scheme);
    await fill(page);
    await page
      .getByRole('combobox', {
        name: 'Related reading (optional)',
        exact: true
      })
      .click();
    await page
      .getByRole('option', { name: 'Seal check · before · 0 bar' })
      .click();
    await page
      .getByRole('combobox', {
        name: 'Related task step (optional)',
        exact: true
      })
      .click();
    await page.getByRole('option', { name: '1. Inspect seal' }).click();
    await page
      .getByRole('button', {
        name: 'Create linked corrective Work',
        exact: true
      })
      .click();
    await expect(page.getByTestId('routine-finding-form')).toContainText(
      'Linked corrective Work created'
    );
    await page.reload();
    await expandFault(page);
    const another = page.getByRole('button', {
      name: 'Record another finding',
      exact: true
    });
    await expect(another).toBeVisible({ timeout: 3000 });
    await another.click();
    await expect(page.getByLabel('Corrective Work title')).toHaveValue('');
    await expect(page.getByLabel('Finding / observation')).toHaveValue('');
    await expect(
      page.getByRole('combobox', {
        name: 'Related reading (optional)',
        exact: true
      })
    ).toHaveValue('');
    await expect(
      page.getByRole('combobox', {
        name: 'Related task step (optional)',
        exact: true
      })
    ).toHaveValue('');
    await expect(
      page.getByRole('button', {
        name: 'Create linked corrective Work',
        exact: true
      })
    ).toBeDisabled();
    await expect(page.getByTestId('routine-work')).toContainText(link.finding);
    expect(commands).toHaveLength(1);
    await captureFinding(
      page,
      info.outputPath(`review-new-finding-${scheme}.png`)
    );
  });
  test(`O21 review reading and step selections stay compatible (${scheme})`, async ({
    page
  }, info) => {
    const value = routine();
    const records = value.routine_work!;
    const task = records.applications[0].tasks[0];
    records.applications[0].tasks.push({
      ...task,
      id: 902,
      step: { ...task.step, sequence: 2, title: 'Inspect coupling' }
    });
    records.readings.push(
      {
        ...records.readings[0],
        id: 702,
        label: 'Independent check',
        step_execution: null
      },
      {
        ...records.readings[0],
        id: 703,
        label: 'Coupling check',
        step_execution: 902
      }
    );
    await mockExecutionWork(page, value);
    const commands: any[] = [];
    await page.route(
      '**/api/kanban/work-orders/commands/create/',
      async (route) => {
        const body = route.request().postDataJSON();
        commands.push(body);
        await route.fulfill({ status: 201, json: receipt(body) });
      }
    );
    await openFault(page, scheme);
    await fill(page);
    const reading = page.getByRole('combobox', {
      name: 'Related reading (optional)',
      exact: true
    });
    const step = page.getByRole('combobox', {
      name: 'Related task step (optional)',
      exact: true
    });
    await chooseFindingOption(
      page,
      'Related task step (optional)',
      '2. Inspect coupling',
      info.outputPath(`dropdown-task-${scheme}.png`)
    );
    await expect(step).toHaveValue('2. Inspect coupling');
    await reading.click();
    await page
      .getByRole('option', { name: 'Seal check · before · 0 bar' })
      .click();
    await expect(step).toHaveValue('');
    await step.click();
    await expect(
      page.getByRole('option', { name: '2. Inspect coupling' })
    ).toHaveCount(0);
    await page.getByRole('option', { name: '1. Inspect seal' }).click();
    await reading.click();
    await page
      .getByRole('option', { name: 'Independent check · before · 0 bar' })
      .click();
    await expect(step).toHaveValue('');
    await expect(step).toBeDisabled();
    await expect(page.getByTestId('routine-finding-form')).toContainText(
      'This reading has no linked task step'
    );
    await reading.click();
    await page
      .getByRole('option', { name: 'Coupling check · before · 0 bar' })
      .click();
    await step.click();
    await expect(
      page.getByRole('option', { name: '1. Inspect seal' })
    ).toHaveCount(0);
    await expect(
      page.getByRole('option', { name: '2. Inspect coupling', exact: true })
    ).toBeInViewport({ ratio: 1 });
    await page.getByRole('option', { name: '2. Inspect coupling' }).click();
    await expectNoPageOverflow(page);
    await captureFinding(
      page,
      info.outputPath(`review-compatible-${scheme}.png`)
    );
    await page
      .getByRole('button', {
        name: 'Create linked corrective Work',
        exact: true
      })
      .click();
    await expect(page.getByTestId('routine-finding-form')).toContainText(
      'Linked corrective Work created'
    );
    expect(commands).toHaveLength(1);
    expect(commands[0]).toMatchObject({
      corrective_reading: 703,
      corrective_step: 902
    });
  });
}
for (const reason of [
  'canceled',
  'inactive',
  'permission',
  'machine_scope',
  'routine_scope',
  'missing_projection',
  'missing_hint',
  'unknown_reason'
] as const) {
  test(`O21 review creation unavailable reason ${reason}`, async ({
    page
  }, info) => {
    const value = routine(false);
    let expected = '';
    if (reason === 'canceled' || reason === 'inactive') {
      value.routine_work!.create_unavailable_reason =
        'routine_inactive_or_canceled';
      value.lifecycle_status =
        reason === 'canceled' ? 'canceled' : 'in_progress';
      value.is_active = reason !== 'inactive';
      expected = 'This routine Work is inactive or canceled';
    } else if (reason === 'missing_projection') {
      value.routine_work = null;
      expected =
        'The server does not supply linked corrective creation availability';
    } else if (reason === 'missing_hint') {
      delete value.routine_work!.can_create_linked;
      expected =
        'The server does not supply linked corrective creation availability';
    } else if (reason === 'unknown_reason') {
      delete value.routine_work!.create_unavailable_reason;
      expected = 'Linked corrective creation is unavailable. Refresh this Work';
    } else {
      value.routine_work!.create_unavailable_reason = reason;
      expected =
        reason === 'permission'
          ? 'requires Work creation permission'
          : reason === 'machine_scope'
            ? 'This machine is unavailable in your current scope'
            : 'This routine Work is unavailable in your current scope';
    }
    const requests = await mockExecutionWork(page, value);
    await openFault(page, reason === 'missing_projection' ? 'dark' : 'light');
    await expect(page.getByTestId('routine-work')).toContainText(expected);
    await expect(
      page.getByRole('button', {
        name: 'Create linked corrective Work',
        exact: true
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    expect(onlyReads(requests)).toBe(true);
    if (['canceled', 'inactive', 'missing_projection'].includes(reason)) {
      await capturePanel(
        page,
        info.outputPath(`review-unavailable-${reason}.png`)
      );
      const disclosure = page.getByRole('region', {
        name: 'Fault found during this work',
        exact: true
      });
      await disclosure.evaluate((element) =>
        element.scrollIntoView({ block: 'center' })
      );
      await disclosure.screenshot({
        path: info.outputPath(`review-unavailable-${reason}-detail.png`),
        animations: 'disabled'
      });
    }
  });
}
test('O21 review lost response reconciles original create after routine machine move', async ({
  page
}, info) => {
  const value = routine();
  await mockExecutionWork(page, value);
  const commands: any[] = [];
  await page.route(
    '**/api/kanban/work-orders/commands/create/',
    async (route) => {
      const body = route.request().postDataJSON();
      commands.push(body);
      if (commands.length === 1) {
        value.machine = 19;
        value.lifecycle_version += 1;
        value.routine_work!.linked_corrective = [link];
        await route.abort('failed');
      } else {
        expect(body).toEqual(commands[0]);
        await route.fulfill({ status: 201, json: receipt(body) });
      }
    }
  );
  await openFault(page, 'dark');
  await fill(page);
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Creation outcome unknown'
  );
  await page.reload();
  await expandFault(page);
  await page
    .getByRole('button', {
      name: 'Check original creation command',
      exact: true
    })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Linked corrective Work created'
  );
  expect(commands).toHaveLength(2);
  expect(commands[1]).toMatchObject({
    machine: 18,
    recurrence_of: 73,
    prior_version: 7
  });
  await captureFinding(
    page,
    info.outputPath('review-moved-source-confirmed-dark.png')
  );
});

test('O21 review retained mismatched refusal explains the pair and blocks resubmission', async ({
  page
}, info) => {
  const value = routine();
  const task = value.routine_work!.applications[0].tasks[0];
  value.routine_work!.applications[0].tasks.push({
    ...task,
    id: 902,
    step: { ...task.step, sequence: 2, title: 'Inspect coupling' }
  });
  await mockExecutionWork(page, value);
  const commands: any[] = [];
  await page.route(
    '**/api/kanban/work-orders/commands/create/',
    async (route) => {
      const body = route.request().postDataJSON();
      commands.push(body);
      await route.fulfill(
        commands.length === 1
          ? { status: 400, json: { detail: 'Prior refused draft' } }
          : { status: 201, json: receipt(body) }
      );
    }
  );
  await openFault(page);
  await fill(page);
  await page
    .getByRole('combobox', { name: 'Related reading (optional)', exact: true })
    .click();
  await page
    .getByRole('option', { name: 'Seal check · before · 0 bar' })
    .click();
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Linked corrective Work refused'
  );
  // Rehydrate the refused incompatible draft produced by the earlier UI.
  await page.evaluate(() => {
    const key = Object.keys(localStorage).find(
      (key) =>
        key.startsWith('aimms-process:') &&
        key.includes('"routine_finding",73,73')
    );
    if (!key) throw new Error('Missing retained routine intent');
    const state = JSON.parse(localStorage.getItem(key)!);
    state.intent.payload.corrective_step = 902;
    localStorage.setItem(key, JSON.stringify(state));
  });
  await page.reload();
  await expandFault(page);
  await page
    .getByRole('button', { name: 'Review latest linked Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Reading and task step do not match'
  );
  await expect(
    page.getByRole('button', {
      name: 'Create linked corrective Work',
      exact: true
    })
  ).toBeDisabled();
  await expect(page.getByLabel('Finding / observation')).toHaveValue(
    link.finding
  );
  await expect(
    page.getByRole('combobox', {
      name: 'Related task step (optional)',
      exact: true
    })
  ).toHaveAttribute('placeholder', 'Saved task step needs review');
  await captureFinding(
    page,
    info.outputPath('review-retained-incompatible-light.png')
  );
  await page
    .getByRole('button', { name: 'Clear incompatible task step', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Create linked corrective Work', exact: true })
    .click();
  await expect(page.getByTestId('routine-finding-form')).toContainText(
    'Linked corrective Work created'
  );
  expect(commands).toHaveLength(2);
  expect(commands[1]).toMatchObject({
    corrective_reading: 701,
    corrective_step: null
  });
});
