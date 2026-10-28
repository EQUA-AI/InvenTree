import { type Page, expect, test } from '@playwright/test';
import { auditAccessibility, waitForRenderedScope } from '../s0/accessibility';
import { assertReadableContrast } from '../s0/contrast';
import { nativeBaseline } from '../s0/guards';
import { openHealth } from '../s3/healthFixtures';
import {
  expectNoPageOverflow,
  mockExecutionWork,
  workOrder
} from '../s4/workExecutionFixtures';
import { mockPump08Staff } from './staffFixtures';

nativeBaseline();

async function openAutomaticWork(
  page: Page,
  scheme: 'light' | 'dark',
  options: {
    failFirst?: boolean;
    mismatchedReceipt?: boolean;
    previewFailure?: boolean;
    refuseFirst?: boolean;
    eventEnabled?: boolean;
    noDuplicates?: boolean;
    duplicateAppears?: boolean;
  } = {}
) {
  const posts: Record<string, any>[] = [];
  const legacyCreates: string[] = [];
  let planReads = 0;
  await mockPump08Staff(page);
  await mockExecutionWork(page, {
    ...workOrder(),
    title: 'Pump 08 | High Motor Winding Temperature',
    assigned_to: 27,
    assigned_to_name: 'Avery Patel'
  });
  await page.route('**/api/assets/registry/?*', (route) =>
    route.fulfill({
      json: {
        results: [
          {
            pk: 17,
            name: 'Cedar Creek Final Effluent Pump Station',
            asset_type: 'pumphouse'
          }
        ]
      }
    })
  );
  await page.route('**/api/assets/registry/17/', (route) =>
    route.fulfill({
      json: {
        children: [
          {
            pk: 18,
            name: 'Cedar Creek / Pump 08',
            parent: 17,
            asset_type: 'pump'
          }
        ]
      }
    })
  );
  await page.route('**/api/maintenance/work-packages/assignees/?*', (route) =>
    route.fulfill({
      json: {
        results: [{ id: 27, name: 'Avery Patel' }]
      }
    })
  );
  await page.route('**/api/maintenance/work-packages/create/', (route) => {
    legacyCreates.push(route.request().url());
    return route.fulfill({
      status: 500,
      json: { detail: 'Automatic flow must not use a second creation request.' }
    });
  });
  await page.route(
    '**/api/machine-health/machines/18/health/test-alarm/**',
    (route) => {
      const request = route.request();
      if (request.method() === 'GET') {
        if (new URL(request.url()).searchParams.has('binding_id')) {
          planReads += 1;
          return route.fulfill({
            status: options.previewFailure ? 403 : 200,
            json: {
              detail: 'Work creation permission is required for this machine.',
              plan_token:
                planReads > 1
                  ? 'renewed-preview-fixture-not-live'
                  : 'signed-preview-fixture-not-live',
              scenario: {
                label: 'Pump 08 motor-winding exercise specification',
                fields: {
                  title: 'Pump 08 | High Motor Winding Temperature',
                  work_scope:
                    'Investigate Winding Temperature 6. Validate source, timestamp and sensor path; review Winding 5, load, cooling and WEG 11866576 manual. Any cooler cleaning is conditional on inspection evidence and approved procedure.',
                  symptom: 'Synthetic winding-six high indication.',
                  production_impact:
                    'Potential Pump 08 availability interruption; actual impact not established.'
                },
                suggested_owner_username: 'cc.avery.patel',
                priority: 'high',
                criticality: 'critical',
                work_order_type: 'corrective',
                parts: [],
                limitations:
                  'Prepared specification, not AI output or physical work. No parts or operating release.'
              },
              duplicates:
                options.noDuplicates ||
                (options.duplicateAppears && planReads === 1)
                  ? []
                  : [
                      {
                        work_order_id: 3826,
                        work_order_reference: 'WO-003826',
                        title: 'Bearing vibration investigation',
                        status: 'in_progress',
                        repair_packet_id: null,
                        reason: 'OPEN_REPAIR_ON_MACHINE'
                      }
                    ]
            }
          });
        }
        return route.fulfill({
          json: {
            event_work_enabled: options.eventEnabled === true,
            results: [
              {
                binding_id: 1494,
                component_id: 21,
                component_code: 'PS-MOTOR:1',
                component_name: 'Electric Motor',
                name: 'Motor Winding Temperature 6',
                value: 44,
                unit: '°C',
                quality: 'good',
                stale: false,
                critical_max: 145,
                value_floor: 145,
                suggested_test_value: 159.5,
                eligible: true,
                limits: {
                  normal_min: null,
                  normal_max: 124.9,
                  warn_min: null,
                  warn_max: 125,
                  critical_min: null,
                  critical_max: 145
                }
              }
            ]
          }
        });
      }
      const body = request.postDataJSON();
      posts.push(body);
      if (options.duplicateAppears && posts.length === 1)
        return route.fulfill({
          status: 409,
          json: {
            code: 'DUPLICATE_OPEN_REPAIR',
            detail: 'This machine already has open repair work.'
          }
        });
      if (options.refuseFirst && posts.length === 1)
        return route.fulfill({
          status: 400,
          json: {
            code: 'WORK_PACKAGE_INVALID',
            detail: 'The work preview expired. Reload it before triggering.'
          }
        });
      if (options.failFirst && posts.length === 1)
        return route.fulfill({
          status: 503,
          json: { detail: 'Response lost after a possible commit.' }
        });
      return route.fulfill({
        json: {
          anomaly_id: 501,
          machine_id: 18,
          binding_id: 1494,
          status: 'open',
          synthetic: true,
          test_value: 159.5,
          unit: '°C',
          idempotency_key: body.idempotency_key,
          work_order: {
            work_order_id: 73,
            repair_packet_id: 28,
            machine_id: 18,
            anomaly_id: options.mismatchedReceipt ? 999 : 501,
            assigned_to_id: body.assigned_to_id,
            idempotency_key: `test-alarm-work:11:${body.idempotency_key}`,
            work_order_reference: 'WO-73',
            repair_packet_reference: 'RP-28',
            lifecycle_version: 2,
            warnings: [],
            replayed: posts.length > 1,
            provenance: 'prepared_scenario_not_ai_output',
            ...(body.event_owned_work
              ? {
                  assignment: {
                    code: 'ASSIGNMENT_REQUIREMENTS_UNKNOWN',
                    supervisor_escalation:
                      'Supervisor review required: staffing evidence is incomplete.'
                  },
                  triage: {
                    explanation:
                      'Critical synthetic test event; not a physical diagnosis.'
                  }
                }
              : {})
          }
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
  await waitForRenderedScope(page, dialog);
  await expect(
    dialog.getByRole('switch', {
      name: 'Create work order automatically',
      exact: true
    })
  ).toBeChecked();
  const signal = dialog.getByRole('combobox', {
    name: 'Configured test signal',
    exact: true
  });
  await signal.click();
  await signal.fill('Motor Winding Temperature 6');
  await page
    .getByRole('option', { name: /Motor Winding Temperature 6/ })
    .click();
  await expect(signal).toHaveValue('Motor Winding Temperature 6');
  return { dialog, posts, legacyCreates };
}

async function reviewAutomaticWork(page: Page) {
  const dialog = page.getByRole('dialog', {
    name: 'Trigger test alarm',
    exact: true
  });
  await expect(
    dialog.getByRole('combobox', { name: 'Assigned staff', exact: true })
  ).toHaveValue(/Avery Patel.*#27/);
  const submit = dialog.getByRole('button', {
    name: 'Trigger and create work order',
    exact: true
  });
  await expect(submit).toBeDisabled();
  const existing = dialog.getByRole('link', { name: /WO-003826/ });
  await expect(existing).toHaveAttribute('target', '_blank');
  await expect(dialog).toContainText('Bearing vibration investigation');
  await expect(dialog).toContainText('No repair packet');
  await dialog
    .getByRole('textbox', {
      name: 'Reason for a separate work order',
      exact: true
    })
    .fill(
      'Separate winding thermal exercise. Existing bearing-vibration WO-003826 remains unchanged.'
    );
  await dialog
    .getByRole('checkbox', {
      name: 'I reviewed existing work and need a separate work order.',
      exact: true
    })
    .check();
  await dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await expect(submit).toBeEnabled();
  return submit;
}

test('event-owned work shows its persisted staffing escalation when enabled', async ({
  page
}, info) => {
  const fixture = await openAutomaticWork(page, 'light', {
    eventEnabled: true
  });
  await expect(
    fixture.dialog.getByRole('checkbox', {
      name: 'Let the event prepare work and request automatic assignment'
    })
  ).toBeChecked();
  await expect(
    fixture.dialog.getByTestId('test-alarm-work-plan')
  ).toBeVisible();
  await expect(
    fixture.dialog.getByRole('combobox', {
      name: 'Assigned staff',
      exact: true
    })
  ).toHaveCount(0);
  await expect(
    fixture.dialog.getByRole('link', { name: /WO-003826/ })
  ).toBeVisible();
  const submit = fixture.dialog.getByRole('button', {
    name: 'Trigger and create work order',
    exact: true
  });
  const review = fixture.dialog.getByRole('checkbox', {
    name: 'I reviewed existing work and need a separate work order.',
    exact: true
  });
  await review.check();
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await expect(submit).toBeDisabled();
  await expect(
    fixture.dialog.getByTestId('test-alarm-readiness')
  ).toContainText('Explain why a separate work order is needed.');
  await fixture.dialog
    .getByRole('textbox', {
      name: 'Reason for a separate work order',
      exact: true
    })
    .fill(
      'Separate winding exercise reviewed against the bearing investigation.'
    );
  await expect(review).not.toBeChecked();
  await review.check();
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await expect(submit).toBeEnabled();
  await expectNoPageOverflow(page);
  await auditAccessibility(
    page,
    fixture.dialog,
    info,
    'event-duplicate-review'
  );
  await fixture.dialog.screenshot({
    path: info.outputPath('event-work-reviewed.png'),
    animations: 'disabled'
  });
  await submit.focus();
  await page.keyboard.press('Enter');
  await expect(fixture.dialog).toContainText('Supervisor review required');
  await expect(fixture.dialog).toContainText('not a physical diagnosis');
  expect(fixture.posts).toHaveLength(1);
  expect(fixture.posts[0]).toMatchObject({
    event_owned_work: true,
    binding_id: 1494,
    duplicate_override: true,
    duplicate_override_reason:
      'Separate winding exercise reviewed against the bearing investigation.'
  });
  expect(fixture.posts[0]).not.toHaveProperty('plan_token');
  expect(fixture.posts[0]).not.toHaveProperty('assigned_to_id');
  expect(fixture.posts[0]).not.toHaveProperty('create_work_order');
  await fixture.dialog.screenshot({
    path: info.outputPath('event-work-receipt.png'),
    animations: 'disabled'
  });
});

test('event-owned work without duplicates retries the exact retained command', async ({
  page
}) => {
  const fixture = await openAutomaticWork(page, 'light', {
    eventEnabled: true,
    noDuplicates: true,
    failFirst: true
  });
  await expect(
    fixture.dialog.getByTestId('test-alarm-work-plan')
  ).toBeVisible();
  await expect(
    fixture.dialog.getByText('Existing work needs review')
  ).toHaveCount(0);
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await fixture.dialog
    .getByRole('button', { name: 'Trigger and create work order' })
    .click();
  await expect(fixture.dialog).toContainText(
    'Alarm and work-order outcome not confirmed'
  );
  await page.reload();
  await page
    .getByRole('button', { name: 'Trigger test alarm', exact: true })
    .click();
  await fixture.dialog
    .getByRole('button', { name: 'Retry same test request' })
    .click();
  await expect(fixture.dialog).toContainText('Supervisor review required');
  expect(fixture.posts).toHaveLength(2);
  expect(fixture.posts[1]).toEqual(fixture.posts[0]);
  expect(fixture.posts[0]).toMatchObject({
    event_owned_work: true,
    duplicate_override: false,
    duplicate_override_reason: ''
  });
});

test('event-owned work reloads review when a duplicate appears after preview', async ({
  page
}) => {
  const fixture = await openAutomaticWork(page, 'light', {
    eventEnabled: true,
    duplicateAppears: true
  });
  await expect(
    fixture.dialog.getByTestId('test-alarm-work-plan')
  ).toBeVisible();
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await fixture.dialog
    .getByRole('button', { name: 'Trigger and create work order' })
    .click();
  await expect(fixture.dialog).toContainText(
    'Alarm and work-order request refused'
  );
  await expect(
    fixture.dialog.getByRole('link', { name: /WO-003826/ })
  ).toBeVisible();
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Trigger and create work order'
    })
  ).toBeDisabled();
  await fixture.dialog
    .getByRole('textbox', {
      name: 'Reason for a separate work order',
      exact: true
    })
    .fill('Reviewed the newly visible bearing investigation.');
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I reviewed existing work and need a separate work order.',
      exact: true
    })
    .check();
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await fixture.dialog
    .getByRole('button', { name: 'Trigger and create work order' })
    .click();
  await expect(fixture.dialog).toContainText('Supervisor review required');
  expect(fixture.posts).toHaveLength(2);
  expect(fixture.posts[0].duplicate_override).toBe(false);
  expect(fixture.posts[1]).toMatchObject({
    event_owned_work: true,
    duplicate_override: true,
    duplicate_override_reason:
      'Reviewed the newly visible bearing investigation.'
  });
  expect(fixture.posts[1].idempotency_key).not.toBe(
    fixture.posts[0].idempotency_key
  );
});

test('event-owned work cannot bypass an unavailable duplicate preview', async ({
  page
}) => {
  const fixture = await openAutomaticWork(page, 'light', {
    eventEnabled: true,
    previewFailure: true
  });
  await expect(fixture.dialog).toContainText('Work preview unavailable');
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Trigger and create work order'
    })
  ).toBeDisabled();
  expect(fixture.posts).toHaveLength(0);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`one reviewed trigger creates and opens the actual work order (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await openAutomaticWork(page, scheme);
    const helperContrast = await assertReadableContrast(
      fixture.dialog.getByText('Registered signal mapping', { exact: false })
    );
    await info.attach('trigger-helper-contrast', {
      body: JSON.stringify(helperContrast),
      contentType: 'application/json'
    });
    await fixture.dialog
      .getByText('Measured now:', { exact: false })
      .locator('..')
      .screenshot({
        path: info.outputPath(`automatic-work-limits-${scheme}.png`),
        animations: 'disabled'
      });
    const submit = await reviewAutomaticWork(page);
    await expect(fixture.dialog).toContainText('Measured now: 44 °C');
    await expect(fixture.dialog).toContainText('Normal maximum 124.9 °C');
    await expect(fixture.dialog).not.toContainText('Normal minimum');
    await expect(fixture.dialog).not.toContainText('Warning below');
    await expect(
      fixture.dialog.getByRole('link', { name: /WO-003826/ })
    ).toContainText('(opens in a new tab)');
    await expect(fixture.dialog).toContainText('not AI output');
    await expect(fixture.dialog).toContainText(
      'Symptom: Synthetic winding-six high indication.'
    );
    await expect(fixture.dialog).toContainText(
      'Type: Corrective · Priority: High · Criticality: Critical'
    );
    await expectNoPageOverflow(page);
    await auditAccessibility(
      page,
      fixture.dialog,
      info,
      `automatic-trigger-${scheme}`
    );
    await fixture.dialog.screenshot({
      path: info.outputPath(`automatic-work-reviewed-${scheme}.png`),
      animations: 'disabled'
    });
    await submit.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('destination')).toHaveText(
      '/maintenance/work-orders/73/'
    );
    expect(fixture.posts).toHaveLength(1);
    expect(fixture.legacyCreates).toHaveLength(0);
    expect(fixture.posts[0]).toMatchObject({
      binding_id: 1494,
      component_id: 21,
      test_value: 159.5,
      confirmed: true,
      create_work_order: true,
      assigned_to_id: 27,
      plan_token: 'signed-preview-fixture-not-live',
      duplicate_override: true,
      duplicate_override_reason:
        'Separate winding thermal exercise. Existing bearing-vibration WO-003826 remains unchanged.'
    });
  });
}

test('uncertain automatic creation retries the exact request after reload', async ({
  page
}) => {
  const fixture = await openAutomaticWork(page, 'light', { failFirst: true });
  await (await reviewAutomaticWork(page)).click();
  await expect(fixture.dialog).toContainText(
    'Alarm and work-order outcome not confirmed'
  );
  await page.reload();
  await page
    .getByRole('button', { name: 'Trigger test alarm', exact: true })
    .click();
  await fixture.dialog
    .getByRole('button', { name: 'Retry same test request', exact: true })
    .click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/73/'
  );
  expect(fixture.posts).toHaveLength(2);
  expect(fixture.posts[1]).toEqual(fixture.posts[0]);
  expect(fixture.legacyCreates).toHaveLength(0);
});

test('a mismatched combined receipt cannot navigate or enable a new command', async ({
  page
}) => {
  const fixture = await openAutomaticWork(page, 'light', {
    mismatchedReceipt: true
  });
  await (await reviewAutomaticWork(page)).click();
  await expect(fixture.dialog).toContainText(
    'Alarm and work-order outcome not confirmed'
  );
  await expect(page.getByTestId('destination')).not.toHaveText(
    '/maintenance/work-orders/73/'
  );
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Retry same test request',
      exact: true
    })
  ).toBeEnabled();
  expect(fixture.posts).toHaveLength(1);
});

test('unavailable work preview blocks triggering without silently dropping work creation', async ({
  page
}, info) => {
  const fixture = await openAutomaticWork(page, 'light', {
    previewFailure: true
  });
  await expect(fixture.dialog).toContainText('Work preview unavailable');
  await expect(fixture.dialog).toContainText(
    'Work creation permission is required for this machine.'
  );
  await expect(fixture.dialog).toContainText(
    'turn off automatic work-order creation to trigger only the alarm'
  );
  await expect(
    fixture.dialog.getByTestId('test-alarm-readiness')
  ).toContainText('Wait for a valid work preview or reload it.');
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I confirm this is a synthetic test; notify authorized recipients.',
      exact: true
    })
    .check();
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Trigger and create work order',
      exact: true
    })
  ).toBeDisabled();
  expect(fixture.posts).toHaveLength(0);
  await fixture.dialog
    .getByRole('alert')
    .filter({ hasText: 'Work preview unavailable' })
    .screenshot({
      path: info.outputPath('automatic-work-preview-refused.png'),
      animations: 'disabled'
    });
});

test('a definite preview refusal explains both outcomes and requires renewed review', async ({
  page
}, info) => {
  const fixture = await openAutomaticWork(page, 'light', { refuseFirst: true });
  await (await reviewAutomaticWork(page)).click();
  await expect(fixture.dialog).toContainText(
    'Alarm and work-order request refused'
  );
  await expect(fixture.dialog).toContainText(
    'The work preview expired. Reload it before triggering.'
  );
  const refusalContrast = await assertReadableContrast(
    fixture.dialog.getByText('Alarm and work-order request refused', {
      exact: true
    })
  );
  await info.attach('combined-refusal-contrast', {
    body: JSON.stringify(refusalContrast),
    contentType: 'application/json'
  });
  await expect(
    fixture.dialog.getByTestId('test-alarm-readiness')
  ).toContainText('Confirm that you reviewed the existing work.');
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Trigger and create work order',
      exact: true
    })
  ).toBeDisabled();
  await fixture.dialog
    .getByRole('alert')
    .filter({ hasText: 'Alarm and work-order request refused' })
    .screenshot({
      path: info.outputPath('automatic-work-definite-refusal.png'),
      animations: 'disabled'
    });
  await (await reviewAutomaticWork(page)).click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/73/'
  );
  expect(fixture.posts).toHaveLength(2);
  expect(fixture.posts[1].idempotency_key).not.toBe(
    fixture.posts[0].idempotency_key
  );
  expect(fixture.posts[1].plan_token).toBe('renewed-preview-fixture-not-live');
});

test('disabled automatic submission states the missing reason and confirmations', async ({
  page
}, info) => {
  const fixture = await openAutomaticWork(page, 'light');
  const status = fixture.dialog.getByTestId('test-alarm-readiness');
  await expect(status).toContainText(
    'Explain why a separate work order is needed.'
  );
  await expect(status).toContainText(
    'Confirm that you reviewed the existing work.'
  );
  await expect(status).toContainText(
    'Confirm the synthetic exercise and notification request.'
  );
  const submit = fixture.dialog.getByRole('button', {
    name: 'Trigger and create work order',
    exact: true
  });
  await expect(submit).toBeDisabled();
  await expect(submit).toHaveAttribute(
    'aria-describedby',
    (await status.getAttribute('id')) as string
  );
  await status.scrollIntoViewIfNeeded();
  await fixture.dialog.screenshot({
    path: info.outputPath('automatic-work-unmet-conditions.png'),
    animations: 'disabled'
  });
  expect(fixture.posts).toHaveLength(0);
});
