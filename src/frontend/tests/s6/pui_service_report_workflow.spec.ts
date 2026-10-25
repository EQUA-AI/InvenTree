import { expect, test } from '@playwright/test';
import { auditAccessibility } from '../s0/accessibility';
import { assertReadableContrast } from '../s0/contrast';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockPositions } from '../s2/equipmentFixtures';
import { captureAuthorityRegion } from '../s4/authorityFixtures';
import {
  closeoutWork,
  containment,
  openCloseout,
  values
} from './closeoutFixtures';

nativeBaseline();

for (const scheme of ['light', 'dark'] as const) {
  test(`packet ownership and governed closeout blockers are readable (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await closeoutWork(page, { captures: [] });
    fixture.setOverview({
      lifecycle_status: 'draft',
      repair_packet: {
        id: 28,
        reference: 'RP-28',
        status: 'draft',
        criticality: 'critical',
        fault_summary: 'Synthetic motor test',
        symptom: '',
        production_impact: '',
        generation_status: 'not_started',
        diagnosis: {},
        diagnosis_is_preliminary: true,
        diagnosis_status: null,
        findings: [],
        approved_scope: null,
        gates: []
      }
    });
    const message =
      'The controlled exercise requires three current passed checks with valid prepared evidence. Physical authority remains separate.';
    await page.route('**/api/repair/packets/28/close/', (route) =>
      route.fulfill({
        json: {
          action: 'complete',
          ready: false,
          lifecycle_version: 7,
          packet_id: 28,
          work_order_id: 73,
          blockers: [{ code: 'EXERCISE_VERIFICATION_INCOMPLETE', message }],
          warnings: []
        }
      })
    );
    await openCloseout(page, scheme);
    await expect(page.locator('html')).toHaveAttribute(
      'data-mantine-color-scheme',
      scheme
    );
    const ownership = page
      .getByTestId('closeout-preparation')
      .locator('.mantine-Alert-message');
    await expect(ownership).toContainText(
      'The linked Repair Packet owns these transitions.'
    );
    const blocker = page
      .getByTestId('work-record-update')
      .getByText(message, { exact: true });
    await expect(blocker).toBeVisible();
    const contrast = {
      scheme,
      ownership: await assertReadableContrast(ownership),
      blocker: await assertReadableContrast(blocker)
    };
    await info.attach(`governance-contrast-${scheme}`, {
      body: JSON.stringify(contrast),
      contentType: 'application/json'
    });
    await auditAccessibility(
      page,
      page.getByTestId('closeout-preparation'),
      info,
      `packet-ownership-${scheme}`
    );
    await auditAccessibility(
      page,
      page.getByTestId('work-record-update'),
      info,
      `closeout-blocker-${scheme}`
    );
    await page
      .getByTestId('closeout-preparation')
      .screenshot({ path: info.outputPath(`packet-ownership-${scheme}.png`) });
    await page
      .getByTestId('work-record-update')
      .screenshot({ path: info.outputPath(`closeout-blocker-${scheme}.png`) });
  });
}

test('manual account uses the current version after submission and independent approval', async ({
  page
}) => {
  const fixture = await closeoutWork(page, { captures: [] });
  let version = 7;
  let report: Record<string, unknown> | null = null;
  let status = 'not_submitted';
  await page.route(
    '**/api/tasks/work-orders/73/closeout/service-report/',
    (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({
          json: {
            status,
            version,
            report,
            report_id: report ? 601 : null,
            submitted_by: 3,
            effects: []
          }
        });
      const body = route.request().postDataJSON();
      expect(body.expected_version).toBe(version);
      if (body.action === 'submit') {
        report = {
          reference: 'WO-73',
          title: 'Manual account',
          account: body.account,
          capture_id: null
        };
        status = 'submitted';
      }
      if (body.action === 'approve') status = 'approved';
      version += 1;
      fixture.setOverview({ lifecycle_version: version });
      return route.fulfill({
        json: {
          work_order_id: 73,
          event_id: version,
          command: `service_report_${body.action}`,
          idempotency_key: body.idempotency_key,
          lifecycle_version: version
        }
      });
    }
  );
  let completionVersion: number | undefined;
  await page.route('**/api/tasks/work-orders/73/complete/', (route) => {
    completionVersion = route.request().postDataJSON().expected_version;
    return route.fulfill({
      status: 409,
      json: {
        code: 'READINESS_ERROR',
        detail: 'Synthetic final boundary refusal'
      }
    });
  });
  await openCloseout(page);
  await page
    .getByRole('textbox', { name: 'Action taken', exact: true })
    .fill(values.action);
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill(values.result);
  await page
    .getByRole('textbox', { name: 'Verification summary', exact: true })
    .fill(values.verification_summary);
  await page
    .getByRole('button', { name: 'Open service report', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Submit service report', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Approve service report', exact: true })
  ).toBeEnabled();
  await page
    .getByRole('button', { name: 'Approve service report', exact: true })
    .click();
  await expect(page.getByTestId('service-report-workflow')).toContainText(
    'Approved'
  );
  await expect(
    page.getByRole('button', { name: 'Resubmit service report', exact: true })
  ).toBeDisabled();
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect.poll(() => completionVersion).toBe(9);
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill(`${values.result} Rechecked.`);
  await expect(
    page.getByRole('button', { name: 'Resubmit service report', exact: true })
  ).toBeEnabled();
  await page
    .getByRole('button', { name: 'Resubmit service report', exact: true })
    .click();
  await expect(
    page.getByText('Awaiting approval', { exact: true })
  ).toBeVisible();
});

test('approved report does not ask a fresh reviewer to complete an empty submission draft', async ({
  page
}, info) => {
  await closeoutWork(page, { captures: [] });
  await page.route(
    '**/api/tasks/work-orders/73/closeout/service-report/',
    (route) =>
      route.fulfill({
        json: {
          status: 'approved',
          version: 7,
          report_id: 601,
          report: {
            reference: 'WO-73',
            title: 'Approved retained report',
            account: values,
            capture_id: null
          },
          submitted_by: 3,
          submitted_by_name: 'Synthetic Technician',
          approved_by: 11,
          approved_by_name: 'Synthetic Supervisor',
          effects: []
        }
      })
  );
  await openCloseout(page);
  await page
    .getByRole('button', { name: 'Open service report', exact: true })
    .click();
  const workflow = page.getByTestId('service-report-workflow');
  await expect(workflow).toContainText('Approved');
  await expect(
    page.getByRole('textbox', { name: 'Action taken', exact: true })
  ).toHaveValue('');
  await expect(workflow).not.toContainText(
    'Submission is unavailable until this account is complete.'
  );
  await captureAuthorityRegion(
    page,
    workflow,
    info.outputPath('approved-fresh-reviewer-no-submission-prompt.png')
  );
});

test('manual account is editable and report downloads as PDF', async ({
  page
}, info) => {
  const fixture = await closeoutWork(page, { captures: [] });
  fixture.setOverview({ lifecycle_status: 'draft' });
  await page.route('**/api/tasks/work-orders/73/readiness/**', (route) =>
    route.fulfill({
      json: {
        ready: false,
        lifecycle_version: 7,
        blockers: [
          {
            code: 'ASSIGNEE_REQUIRED',
            message: 'Assign a technician before execution'
          }
        ]
      }
    })
  );
  let payload: Record<string, unknown> | undefined;
  await page.route(
    '**/api/tasks/work-orders/73/closeout/report.pdf',
    (route) => {
      payload = route.request().postDataJSON();
      return route.fulfill({
        contentType: 'application/pdf',
        body: '%PDF-1.7\n% synthetic download fixture\n%%EOF'
      });
    }
  );
  await openCloseout(page);
  await expect(page.getByTestId('closeout-preparation')).toContainText(
    'Assign a technician before execution'
  );
  await assertReadableContrast(
    page
      .getByTestId('work-record-update')
      .getByText('Assign a technician before execution', { exact: true })
  );
  await page
    .getByRole('textbox', { name: 'Action taken', exact: true })
    .fill('Checked the synthetic motor');
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill('Test condition reviewed');
  await page
    .getByRole('textbox', { name: 'Verification summary', exact: true })
    .fill('Simulation only; no operating permission');
  const report = page.getByTestId('prepared-closeout');
  await report.getByText('Read report', { exact: true }).click();
  await expect(report.getByTestId('closeout-report-document')).toContainText(
    'Checked the synthetic motor'
  );
  const downloading = page.waitForEvent('download');
  await report
    .getByRole('button', { name: 'Download PDF', exact: true })
    .click();
  expect((await downloading).suggestedFilename()).toMatch(/\.pdf$/);
  expect(payload?.source).toBe('draft');
  expect((payload?.account as Record<string, unknown>).action).toBe(
    'Checked the synthetic motor'
  );
  expect(fixture.commands).toHaveLength(0);
  await captureAuthorityRegion(
    page,
    report.getByTestId('closeout-report-document'),
    info.outputPath('manual-report-preview.png')
  );
  await containment(page);
});

test('native lifecycle preparation performs only one authorized transition per click', async ({
  page
}) => {
  const fixture = await closeoutWork(page, { captures: [] });
  let status = 'draft';
  let version = 7;
  fixture.setOverview({ lifecycle_status: status });
  const transitions: string[] = [];
  await page.route('**/api/tasks/work-orders/73/readiness/**', (route) =>
    route.fulfill({
      json: { ready: true, lifecycle_version: version, blockers: [] }
    })
  );
  await page.route('**/api/tasks/work-orders/73/transition/', (route) => {
    const body = route.request().postDataJSON();
    expect(body.expected_version).toBe(version);
    transitions.push(body.to_status);
    status = body.to_status;
    version += 1;
    fixture.setOverview({
      lifecycle_status: status,
      lifecycle_version: version
    });
    return route.fulfill({
      json: {
        work_order_id: 73,
        command: 'transition',
        event_id: version,
        idempotency_key: body.idempotency_key,
        lifecycle_status: status,
        lifecycle_version: version
      }
    });
  });
  await openCloseout(page);
  for (const label of [
    'Plan this Work',
    'Check and mark ready',
    'Start execution',
    'Finish execution and begin closeout'
  ]) {
    const button = page.getByRole('button', { name: label, exact: true });
    await expect(button).toBeEnabled();
    await button.click();
  }
  await expect(page.getByTestId('closeout-preparation')).toHaveCount(0);
  expect(transitions).toEqual(['planned', 'ready', 'in_progress', 'verifying']);
});

test('ending a synthetic exercise requires a reason and does not complete Work', async ({
  page
}) => {
  await closeoutWork(page, { captures: [] });
  await mockMachine(page);
  await mockPositions(page);
  let ended = false;
  await page.route(
    '**/api/tasks/work-orders/73/closeout/service-report/',
    (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({
          json: {
            status: 'not_submitted',
            version: 7,
            test_alarm: true,
            report: null,
            report_id: null,
            effects: [],
            test_recovery: {
              total: 1,
              active: 0,
              pending: 1,
              other_active_alarms: 2
            }
          }
        });
      const body = route.request().postDataJSON();
      expect(body.action).toBe('end_test');
      expect(body.confirmed).toBe(true);
      expect(body.reason).toBe('Stopping the simulation without a repair');
      ended = true;
      return route.fulfill({
        json: {
          work_order_id: 73,
          command: 'service_report_end_test',
          event_id: 801,
          idempotency_key: body.idempotency_key,
          metadata: {
            work_completed: false,
            equipment_url: '/machines/machine/18/equipment/'
          }
        }
      });
    }
  );
  await openCloseout(page);
  await page
    .getByRole('button', { name: 'Open service report', exact: true })
    .click();
  await expect(page.getByTestId('service-report-workflow')).toContainText(
    'Other real equipment alarms remain active'
  );
  await expect(page.getByTestId('service-report-workflow')).toContainText(
    'Test exercise suppressed — not ended'
  );
  await page
    .getByRole('button', { name: 'End test exercise', exact: true })
    .click();
  const dialog = page.getByRole('dialog', {
    name: 'End test exercise',
    exact: true
  });
  await expect(
    dialog.getByRole('button', { name: 'End test and view equipment' })
  ).toBeDisabled();
  await dialog
    .getByLabel('Reason for ending the test')
    .fill('Stopping the simulation without a repair');
  await dialog.getByRole('checkbox').check();
  await dialog
    .getByRole('button', { name: 'End test and view equipment' })
    .click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/machines/machine/18/equipment/'
  );
  expect(ended).toBe(true);
});

test('verified return navigates only after the committed command receipt', async ({
  page
}) => {
  await closeoutWork(page, { completed: true });
  await mockMachine(page);
  await mockPositions(page);
  let submitted = false;
  await page.route(
    '**/api/tasks/work-orders/73/closeout/service-report/',
    async (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({
          json: {
            status: 'approved',
            report_id: 601,
            report: null,
            version: 8,
            test_alarm: true,
            sap_configured: false,
            verification_enabled: true,
            verified: false,
            effects: []
          }
        });
      const body = route.request().postDataJSON();
      expect(body.action).toBe('return');
      submitted = true;
      return route.fulfill({
        json: {
          work_order_id: 73,
          event_id: 900,
          command: 'service_report_return',
          idempotency_key: body.idempotency_key,
          lifecycle_version: 9,
          metadata: {
            resolved_test_alarms: [501],
            equipment_url: '/machines/machine/18/equipment/'
          }
        }
      });
    }
  );
  await openCloseout(page);
  await page.getByRole('button', { name: 'Open service report' }).click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/73/?work_panel=closeout'
  );
  await page.getByRole('button', { name: 'Verify and view equipment' }).click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/machines/machine/18/equipment/'
  );
  expect(submitted).toBe(true);
});
for (const scheme of ['light', 'dark'] as const) {
  test(`service report previews, submits and independently approves (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await closeoutWork(page);
    let version = 7;
    let state: Record<string, any> = {
      required: true,
      test_alarm: true,
      status: 'not_submitted',
      report_id: null,
      report: null,
      content_hash: null,
      submitted_by: null,
      approved_by: null,
      sap_configured: false,
      verification_enabled: true,
      verified: false,
      effects: [],
      version
    };
    const commands: Record<string, any>[] = [];
    await page.route(
      '**/api/tasks/work-orders/73/closeout/service-report/',
      async (route) => {
        if (route.request().method() === 'GET')
          return route.fulfill({ json: state });
        const body = route.request().postDataJSON();
        commands.push(body);
        if (body.action === 'submit')
          state = {
            ...state,
            status: 'submitted',
            report_id: 601,
            submitted_by: 3,
            submitted_by_name: 'Synthetic Technician',
            content_hash: 'c'.repeat(64),
            report: {
              reference: 'WO-73',
              title: 'Synthetic service report',
              account: body.account,
              capture_id: body.capture_id
            }
          };
        if (body.action === 'approve')
          state = {
            ...state,
            status: 'approved',
            approved_by: 11,
            approved_by_name: 'Synthetic Supervisor'
          };
        version += 1;
        state.version = version;
        fixture.setOverview({ lifecycle_version: version });
        return route.fulfill({
          json: {
            work_order_id: 73,
            event_id: 601 + commands.length,
            lifecycle_version: version,
            idempotency_key: body.idempotency_key,
            command: `service_report_${body.action}`,
            metadata: {}
          }
        });
      }
    );
    await openCloseout(page, scheme);
    await page.getByRole('button', { name: 'Open service report' }).click();
    await page.getByRole('button', { name: 'Preview draft report' }).click();
    const draft = page.getByRole('dialog', { name: 'Draft service report' });
    await expect(draft).toContainText(values.action);
    await expect(
      draft.getByRole('button', { name: 'Download PDF', exact: true })
    ).toBeInViewport({ ratio: 1 });
    await draft.screenshot({
      path: info.outputPath(`service-report-draft-${scheme}.png`)
    });
    await draft.getByRole('button', { name: 'Close report preview' }).click();
    await page.getByRole('button', { name: 'Submit service report' }).click();
    await expect(
      page.getByText('Awaiting approval', { exact: true })
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Preview submitted report' })
      .click();
    const preview = page.getByRole('dialog', {
      name: 'Submitted service report'
    });
    await expect(preview).toContainText('Retained server snapshot');
    await preview.screenshot({
      path: info.outputPath(`service-report-preview-${scheme}.png`)
    });
    await preview.getByRole('button', { name: 'Close report preview' }).click();
    await expect(
      page.getByRole('button', { name: 'Approve service report' })
    ).toBeEnabled();
    await page.getByRole('button', { name: 'Approve service report' }).click();
    await expect(
      page.getByText('Approved by: Synthetic Supervisor')
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Resubmit service report', exact: true })
    ).toBeDisabled();
    await expect(page.getByText(/SAP writeback: Not configured/)).toBeVisible();
    expect(commands.map((command) => command.action)).toEqual([
      'submit',
      'approve'
    ]);
    expect(commands[0].account.action).toBe(values.action);
    expect(commands[1].report_id).toBe(601);
    await containment(page);
    await captureAuthorityRegion(
      page,
      page.getByTestId('service-report-workflow'),
      info.outputPath(`service-report-approved-${scheme}.png`)
    );
  });
}

test('failed report refresh removes retained approval and all command authority', async ({
  page
}) => {
  await closeoutWork(page, { completed: true });
  let failed = false;
  await page.route(
    '**/api/tasks/work-orders/73/closeout/service-report/',
    (route) =>
      route.fulfill({
        status: failed ? 503 : 200,
        json: failed
          ? { detail: 'Synthetic denied refresh' }
          : {
              status: 'approved',
              report_id: 601,
              report: null,
              version: 8,
              test_alarm: true,
              sap_configured: false,
              verification_enabled: true,
              verified: false,
              effects: []
            }
      })
  );
  await openCloseout(page);
  await page.getByRole('button', { name: 'Open service report' }).click();
  await expect(
    page.getByRole('button', { name: 'Write back to SAP' })
  ).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Verify and view equipment' })
  ).toBeEnabled();
  failed = true;
  await page.getByRole('button', { name: 'Reload report' }).click();
  await expect(
    page.getByText(
      'Service report unavailable. No approval or recovery is confirmed.'
    )
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Verify and view equipment' })
  ).toHaveCount(0);
});
