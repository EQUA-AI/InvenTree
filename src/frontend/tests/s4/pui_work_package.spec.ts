import { expect, test } from '@playwright/test';
import type { PreparedPackage } from '../../lib/types/WorkExecution';
import { nativeBaseline } from '../s0/guards';
import {
  capturePanel,
  expectNoPageOverflow,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  panelRegion,
  workOrder
} from './workExecutionFixtures';

nativeBaseline();

function preparedPackage(
  extra: Partial<PreparedPackage> = {}
): PreparedPackage {
  return {
    rule: 'prepared_package.v1',
    scopes: [2, 1].map((version) => ({
      id: version,
      version,
      current: version === 2,
      verified_cause: `Synthetic scope basis ${version}`,
      lines: [{ sequence: 1, action: `Synthetic task version ${version}` }],
      crew_size: 2,
      planned_elapsed_minutes: 60,
      approved_by: 'Synthetic planner',
      approved_at: '2026-10-18T12:00:00Z',
      note: 'Synthetic scope revision note'
    })),
    procedures_state: 'read',
    applications: [
      {
        id: 7,
        primary: true,
        procedure_id: 11,
        revision_id: 15,
        revision: 3,
        change_summary: 'Synthetic applied package',
        snapshot_hash: 'a'.repeat(64),
        drift_status: 'current',
        applied_by: 'Synthetic technician',
        applied_at: '2026-10-18T12:00:00Z',
        steps: [
          {
            key: 'step-1',
            sequence: 1,
            step_type: 'verification',
            title: 'Synthetic applicable test',
            instruction: 'Use the saved synthetic instruction',
            required: true,
            required_permission: '',
            value_type: 'number',
            unit: 'mm/s',
            min_value: '0',
            max_value: '4',
            allowed_values: []
          }
        ],
        resources: [
          {
            key: 'tool-1',
            sequence: 1,
            kind: 'tool',
            part_id: 44,
            part_name: 'Synthetic calibrated vibration instrument',
            quantity: '1',
            fulfillment_mode: 'checkout_return',
            required: true,
            notes: 'Synthetic calibrated instrument requirement'
          }
        ]
      }
    ],
    deviations: [
      {
        id: 8,
        category: 'changed_condition',
        application_key: '7',
        step_key: 'step-1',
        resource_key: '',
        expected: { access: true },
        actual: { access: false },
        reason: 'Synthetic access constraint',
        recorded_by: 'Synthetic recorder',
        created_at: '2026-10-18T13:00:00Z',
        resolution: '',
        resolved_at: null
      }
    ],
    can_record_deviation: false,
    physical_authority: { available: false, reason: 'no_provider' },
    affected_dependencies: { available: false, reason: 'no_provider' },
    ...extra
  };
}

for (const scheme of ['light', 'dark'] as const) {
  test(`saved package retains sourced criteria revisions and unknown authority (${scheme})`, async ({
    page
  }, info) => {
    const requests = await mockExecutionWork(page, {
      ...workOrder(),
      repair_packet: {
        id: 901,
        reference: 'RP-901',
        status: 'executing',
        criticality: 'high',
        fault_summary: 'Synthetic retained fault',
        symptom: 'Synthetic reported vibration',
        production_impact: '',
        generation_status: 'complete',
        diagnosis: {},
        diagnosis_is_preliminary: true,
        diagnosis_status: null,
        findings: [],
        approved_scope: {
          id: 2,
          version: 2,
          verified_cause: 'Synthetic scope basis 2',
          scope_lines: [{ sequence: 1, action: 'Synthetic task version 2' }],
          failure_codes: [],
          crew_size: 2,
          planned_elapsed_minutes: 60,
          approved_at: '2026-10-18T12:00:00Z',
          approval_note: 'Synthetic scope revision note'
        },
        gates: []
      },
      prepared_package: preparedPackage()
    });
    await openExecutionPanel(page, 'plan', scheme, true);
    const plan = panelRegion(page, 'Plan & Safety');
    await expect(
      plan.getByText('Current scope version 2', { exact: true })
    ).toBeVisible();
    await plan
      .getByRole('button', { name: 'Instructions and sourced criteria (1)' })
      .click();
    await expect(
      plan.getByText('Use the saved synthetic instruction')
    ).toBeVisible();
    await expect(plan.getByText(/Minimum: 0/)).toBeVisible();
    await plan
      .getByRole('button', { name: 'Resource requirements (1)' })
      .click();
    await expect(
      plan.getByText('Synthetic calibrated instrument requirement')
    ).toBeVisible();
    await expect(
      plan.getByRole('link', {
        name: 'Synthetic calibrated vibration instrument',
        exact: true
      })
    ).toHaveAttribute('href', '/part/44/');
    await expect(plan.getByText(/tool · Part 44/)).toHaveCount(0);
    await expect(plan.getByText('access: Yes', { exact: true })).toBeVisible();
    await expect(plan.getByText('access: No', { exact: true })).toBeVisible();
    await expect(plan.getByText(/\{\"access\":/)).toHaveCount(0);
    await plan
      .getByRole('button', { name: 'All retained scope versions (2)' })
      .click();
    await expect(plan.getByText('Synthetic task version 1')).toBeVisible();
    await expect(
      plan.getByText(/no physical-authority provider/)
    ).toBeVisible();
    await expect(
      plan.getByText(
        /No record links a scope revision to affected dependencies/
      )
    ).toBeVisible();
    await expect(plan.getByText('Synthetic access constraint')).toBeVisible();
    await expect(
      plan.getByText('Unresolved — no resolution recorded')
    ).toBeVisible();
    await expect(
      plan.getByRole('button', {
        name: /Accept scope|Approve|Start work|Save deviation/
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`package-${scheme}.png`));
    expect(onlyReads(requests)).toBe(true);
  });
}

test('missing package and disabled procedure source do not establish readiness', async ({
  page
}, info) => {
  await mockExecutionWork(page);
  await openExecutionPanel(page, 'plan');
  await expect(page.getByText('Prepared package not supplied')).toBeVisible();
  await mockExecutionWork(page, {
    ...workOrder(),
    prepared_package: preparedPackage({
      procedures_state: 'not_enabled',
      applications: [],
      deviations: [],
      scopes: []
    })
  });
  await page.reload();
  await expect(
    page.getByText('Governed procedure records are not enabled.')
  ).toBeVisible();
  await expect(
    page.getByText('No current approved repair scope recorded.')
  ).toBeVisible();
  await capturePanel(page, info.outputPath('package-disabled.png'));
});

test('native deviation save retains the draft on uncertain response without resubmitting', async ({
  page
}, info) => {
  const requests = await mockExecutionWork(page, {
    ...workOrder(),
    prepared_package: preparedPackage({
      can_record_deviation: true,
      scopes: []
    })
  });
  await page.route('**/api/tasks/work-orders/73/deviations/', (route) =>
    route.fulfill({
      status: 503,
      json: { detail: 'Synthetic unavailable response' }
    })
  );
  await openExecutionPanel(page, 'plan');
  const plan = panelRegion(page, 'Plan & Safety');
  await plan
    .getByRole('button', { name: 'Record a changed condition' })
    .click();
  await plan
    .getByRole('textbox', { name: 'Changed condition and reason' })
    .fill('Synthetic unavailable access — retain this draft');
  await plan
    .getByRole('button', { name: 'Save deviation', exact: true })
    .click();
  await expect(plan.getByText(/Save not confirmed/)).toBeVisible();
  await expect(plan.getByRole('textbox')).toHaveValue(
    'Synthetic unavailable access — retain this draft'
  );
  await expect(
    plan.getByRole('button', { name: 'Save deviation', exact: true })
  ).toBeDisabled();
  expect(requests.filter((request) => request.method === 'POST')).toEqual([
    { method: 'POST', path: '/api/tasks/work-orders/73/deviations/' }
  ]);
  await capturePanel(page, info.outputPath('deviation-retained.png'));
});

test('confirmed native deviation saves once and refreshes its owning overview', async ({
  page
}) => {
  let overviewReads = 0;
  const value = preparedPackage({ can_record_deviation: true, scopes: [] });
  await mockExecutionWork(page, { ...workOrder(), prepared_package: value });
  await page.route('**/api/kanban/work-orders/73/overview/', (route) => {
    overviewReads++;
    return route.fulfill({ json: { ...workOrder(), prepared_package: value } });
  });
  let saves = 0;
  await page.route('**/api/tasks/work-orders/73/deviations/', (route) => {
    saves++;
    expect(route.request().postDataJSON()).toEqual({
      category: 'changed_condition',
      reason: 'Synthetic changed condition'
    });
    return route.fulfill({ status: 201, json: { id: 99 } });
  });
  await openExecutionPanel(page, 'plan');
  await page
    .getByRole('button', { name: 'Record a changed condition' })
    .click();
  await page
    .getByRole('textbox', { name: 'Changed condition and reason' })
    .fill('Synthetic changed condition');
  await page
    .getByRole('button', { name: 'Save deviation', exact: true })
    .click();
  await expect(
    page.getByText('Deviation saved to this work order.')
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Changed condition and reason' })
  ).toHaveValue('');
  await expect.poll(() => overviewReads).toBe(2);
  expect(saves).toBe(1);
});

test('part names remain unlinked for Work readers without part access', async ({
  page
}, info) => {
  await mockExecutionWork(page, {
    ...workOrder(),
    prepared_package: preparedPackage({ scopes: [] })
  });
  await openExecutionPanel(page, 'plan');
  const plan = panelRegion(page, 'Plan & Safety');
  await plan.getByRole('button', { name: 'Resource requirements (1)' }).click();
  await expect(
    plan.getByText('Synthetic calibrated vibration instrument', { exact: true })
  ).toBeVisible();
  await expect(
    plan.getByRole('link', {
      name: 'Synthetic calibrated vibration instrument'
    })
  ).toHaveCount(0);
  await mockExecutionWork(page, {
    ...workOrder(),
    prepared_package: preparedPackage({
      scopes: [],
      applications: [
        {
          ...preparedPackage().applications[0],
          resources: [
            {
              ...preparedPackage().applications[0].resources[0],
              part_name: null
            }
          ]
        }
      ]
    })
  });
  await page.reload();
  await plan.getByRole('button', { name: 'Resource requirements (1)' }).click();
  await expect(
    plan.getByText('Part name unavailable', { exact: true })
  ).toBeVisible();
  await capturePanel(page, info.outputPath('package-part-unavailable.png'));
});
