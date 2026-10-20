import { expect, test } from '@playwright/test';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { nativeBaseline } from '../s0/guards';
import {
  accepted,
  closeoutWork,
  containment,
  openCloseout
} from './closeoutFixtures';

nativeBaseline();
const passedTest: WorkOrderOverview['verification_return'] = {
  rule: 'verification_return.v1',
  procedures_state: 'read',
  tests: [
    {
      id: 71,
      application_id: 4,
      procedure_revision: 2,
      step: {
        key: 'synthetic-check',
        sequence: 1,
        step_type: 'measurement',
        title: 'Synthetic bounded check',
        instruction: 'Synthetic criterion from fixture provider',
        required: true,
        required_permission: '',
        value_type: 'number',
        unit: 'mm/s',
        min_value: '0',
        max_value: '3',
        allowed_values: []
      },
      version: 2,
      status: 'completed',
      value: 0,
      passed: true,
      note: '',
      disposition_reason: '',
      recorded_by: 'Synthetic Technician',
      recorded_at: '2026-10-19T08:00:00Z',
      events: []
    }
  ],
  attempt_history: 'outcomes_only_previous_values_not_retained',
  lockouts: [],
  technical_acceptance: { available: false, reason: 'no_provider' },
  controlled_test_authority: { available: false, reason: 'no_provider' },
  operating_permission: { available: false, reason: 'no_provider' },
  observed_return: { available: false, reason: 'no_provider' },
  service_state: { available: false, reason: 'no_provider' }
};
for (const scheme of ['light', 'dark'] as const) {
  test(`P49 completed maintenance retains unknown service and open follow-up (${scheme})`, async ({
    page
  }, info) => {
    const { commands, setOverview } = await closeoutWork(page, {
      completed: true
    });
    setOverview({ verification_return: passedTest });
    await openCloseout(page, scheme);
    const closeout = page.getByRole('tabpanel', {
      name: 'Closeout',
      exact: true
    });
    const brief = closeout.getByTestId('service-restoration');
    await expect(brief).toContainText('Updated · Closeout 301');
    await expect(brief).toContainText('Recorded passes: 1');
    await expect(brief).toContainText('Test acceptance: unavailable');
    await expect(brief).toContainText('Service: awaiting verification');
    await expect(brief).toContainText('Resilience: unknown');
    await expect(brief).toContainText(accepted!.follow_up);
    await expect(brief).toContainText(
      'Follow-up owner, original trigger, checkpoint and resolution are not supplied'
    );
    await expect(brief).not.toContainText('Obligations closed');
    await containment(page);
    await page.screenshot({
      path: info.outputPath(`service-closeout-${scheme}.png`),
      fullPage: true
    });
    await page.getByRole('tab', { name: 'Work', exact: true }).click();
    await expect(
      page
        .getByRole('tabpanel', { name: 'Work', exact: true })
        .getByTestId('service-restoration')
    ).toContainText(accepted!.follow_up);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`service-work-${scheme}.png`),
      fullPage: true
    });
    await page
      .getByRole('tab', { name: 'Verify & Return', exact: true })
      .click();
    const verify = page.getByRole('tabpanel', {
      name: 'Verify & Return',
      exact: true
    });
    await expect(verify.getByTestId('service-restoration')).toContainText(
      accepted!.follow_up
    );
    await expect(
      verify.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toHaveCount(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await containment(page);
    await page.screenshot({
      path: info.outputPath(`service-verify-${scheme}.png`),
      fullPage: true
    });
    expect(commands).toEqual([]);
  });
}

test('P49 saved request retains source, owner and checkpoint independently of record completion', async ({
  page
}, info) => {
  const { setOverview } = await closeoutWork(page, { completed: true });
  setOverview({
    investigation: {
      rule: 'investigation.v1',
      packet: null,
      explanation: null,
      findings: [],
      finding_history: 'not_applicable',
      intervention: null,
      requests: {
        available: true,
        reason: '',
        items: [
          {
            id: 44,
            work_order: 73,
            schema_version: 1,
            version: 2,
            purpose: 'Determine remaining limitation',
            next_check: 'Inspect the temporary seal at the recorded checkpoint',
            priority: null,
            required_artifacts: [],
            status: 'pending_external',
            owner: 8,
            owner_name: 'Internal Coordinator',
            next_checkpoint: '2026-10-21T10:00:00Z',
            source_finding: null,
            source_proposal: null,
            blocked_reason: 'Awaiting attributed external report',
            resolution: '',
            created_by: 3,
            updated_by: 8,
            created_at: '2026-10-19T10:00:00Z',
            updated_at: '2026-10-19T11:00:00Z',
            artifacts: []
          }
        ]
      }
    }
  });
  const entry = encodeURIComponent(
    '/maintenance/work-orders/73/?work_panel=closeout&investigation_request=99'
  );
  await page.goto(
    `/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&work_access=reader&entry=${entry}`
  );
  const brief = page
    .getByRole('tabpanel', { name: 'Closeout', exact: true })
    .getByTestId('service-restoration');
  await expect(brief).toContainText('Internal Coordinator');
  await expect(brief).toContainText('21 Oct 2026, 13:00');
  await expect(brief).toContainText('Original trigger: unavailable');
  await expect(brief.getByRole('link', { name: /Request 44/ })).toHaveAttribute(
    'href',
    /work_panel=diagnose.*investigation_request=44/
  );
  const href = await brief
    .getByRole('link', { name: /Request 44/ })
    .getAttribute('href');
  expect(
    new URL(href!, 'http://fixture').searchParams.getAll(
      'investigation_request'
    )
  ).toEqual(['44']);
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('service-owned-request.png'),
    fullPage: true
  });
});
