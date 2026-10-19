import { expect, test } from '@playwright/test';
import type {
  VerificationReturn,
  VerificationTest
} from '../../lib/types/WorkExecution';
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
function verification(
  extra: Partial<VerificationReturn> = {}
): VerificationReturn {
  const result: VerificationTest = {
    id: 1,
    application_id: 7,
    procedure_revision: 3,
    version: 4,
    status: 'completed',
    value: { number: 0 },
    passed: true,
    note: 'Synthetic saved observation',
    disposition_reason: '',
    recorded_by: 'Synthetic technician',
    recorded_at: '2026-10-18T12:00:00Z',
    step: {
      key: 'one',
      sequence: 1,
      step_type: 'verification',
      title: 'Synthetic post-repair reading',
      instruction: 'Synthetic acceptance basis from applied revision',
      required: true,
      required_permission: '',
      value_type: 'number',
      unit: 'mm/s',
      min_value: '0',
      max_value: '4',
      allowed_values: []
    },
    events: [
      {
        id: 91,
        event_type: 'STEP_FAILED',
        version: 2,
        recorded_by: 'Synthetic technician',
        recorded_at: '2026-10-18T11:00:00Z',
        reason: 'Synthetic failed first attempt',
        evidence_ids: [100],
        historical_value_available: false
      },
      {
        id: 92,
        event_type: 'STEP_REOPENED',
        version: 3,
        recorded_by: 'Synthetic technician',
        recorded_at: '2026-10-18T11:30:00Z',
        reason: 'Synthetic correction then retest',
        evidence_ids: [],
        historical_value_available: false
      }
    ]
  };
  return {
    rule: 'verification_return.v1',
    procedures_state: 'read',
    tests: [result],
    attempt_history: 'outcomes_only_previous_values_not_retained',
    lockouts: [
      {
        id: 61,
        device: 'Synthetic feeder isolation',
        energy_source: 'electrical',
        status: 'restored',
        applied_by: 'Synthetic operator',
        verified_by: 'Synthetic verifier',
        verified_at: '2026-10-18T10:00:00Z',
        restored_at: '2026-10-18T12:00:00Z',
        note: 'Synthetic point restoration, independent restriction unresolved'
      }
    ],
    technical_acceptance: { available: false, reason: 'no_provider' },
    controlled_test_authority: { available: false, reason: 'no_provider' },
    operating_permission: { available: false, reason: 'no_provider' },
    observed_return: { available: false, reason: 'no_provider' },
    service_state: { available: false, reason: 'no_provider' },
    ...extra
  };
}
for (const scheme of ['light', 'dark'] as const) {
  test(`verification retains failure after a later result and separates authority (${scheme})`, async ({
    page
  }, info) => {
    if (info.project.name === 's4-desktop')
      await page.setViewportSize({ width: 1100, height: 800 });
    const requests = await mockExecutionWork(page, {
      ...workOrder(),
      lifecycle_status: 'completed',
      verification_return: verification()
    });
    await openExecutionPanel(page, 'verify', scheme);
    const verify = panelRegion(page, 'Verify & Return');
    await expect(verify.getByText('0 mm/s', { exact: true })).toBeVisible();
    await expect(
      verify.getByText('Recorded pass — acceptance separate')
    ).toBeVisible();
    await verify
      .getByRole('button', { name: 'Retained attempt events (2)' })
      .click();
    await expect(verify.getByText('Failed attempt retained')).toBeVisible();
    await expect(
      verify.getByText('Synthetic correction then retest')
    ).toBeVisible();
    await expect(
      verify.getByText('Historical value: not available')
    ).toHaveCount(2);
    await expect(verify.getByText(/Awaiting test authority/)).toBeVisible();
    await expect(
      verify.getByText(/awaiting operating authority/)
    ).toBeVisible();
    await expect(
      verify.getByText('Awaiting operations record of return.')
    ).toBeVisible();
    await expect(
      verify.getByText(/Awaiting service verification/)
    ).toBeVisible();
    await expect(
      verify.getByRole('button', {
        name: /Update|Complete|Approve|Authorize|Release/
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`verification-${scheme}.png`));
    await verify.getByRole('link', { name: 'Open Closeout' }).click();
    await expect(
      page.getByRole('tab', { name: 'Closeout', exact: true })
    ).toHaveAttribute('aria-selected', 'true');
    expect(onlyReads(requests)).toBe(true);
  });
}
test('unbounded criteria stay not evaluated and a current failure stays failed', async ({
  page
}, info) => {
  const value = verification();
  const original = value.tests[0];
  value.tests = [
    {
      ...original,
      step: { ...original.step, min_value: null, max_value: null }
    },
    {
      ...original,
      id: 2,
      status: 'failed',
      passed: false,
      value: { number: 7 }
    }
  ];
  await mockExecutionWork(page, { ...workOrder(), verification_return: value });
  await openExecutionPanel(page, 'verify');
  const verify = panelRegion(page, 'Verify & Return');
  await expect(
    verify
      .getByTestId('verification-test-1')
      .getByText('Not evaluated', { exact: true })
  ).toBeVisible();
  await expect(
    verify
      .getByTestId('verification-test-2')
      .getByText('Failed — recorded result')
  ).toBeVisible();
  await expect(
    verify
      .getByTestId('verification-test-2')
      .getByText('7 mm/s', { exact: true })
  ).toBeVisible();
  await capturePanel(
    page,
    info.outputPath('verification-no-criteria-failed.png')
  );
});
test('missing projection and disabled records stay unknown', async ({
  page
}, info) => {
  await mockExecutionWork(page);
  await openExecutionPanel(page, 'verify');
  await expect(
    page.getByText(/Physical test criteria and attempts are not supplied/)
  ).toBeVisible();
  await mockExecutionWork(page, {
    ...workOrder(),
    verification_return: verification({
      procedures_state: 'not_enabled',
      tests: []
    })
  });
  await page.reload();
  await expect(
    page.getByText('Governed test records are not enabled. Not evaluated.')
  ).toBeVisible();
  await capturePanel(page, info.outputPath('verification-disabled.png'));
});
