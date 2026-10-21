import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { revealWorkContext, savedRequest } from './workExecutionFixtures';
import {
  capturePanel,
  expectNoPageOverflow,
  mockAssistantOpen,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  panelRegion,
  workOrder
} from './workExecutionFixtures';

nativeBaseline();

for (const scheme of ['light', 'dark'] as const) {
  test(`recorded outcome leads without routine chores or inferred service success (${scheme})`, async ({
    page
  }, info) => {
    const requests = await mockExecutionWork(page, {
      ...workOrder(),
      lifecycle_status: 'completed',
      parts: [
        {
          id: 31,
          part: 81,
          part_name: 'Synthetic guard fastener',
          part_ipn: 'SYN-81',
          part_thumbnail: null,
          quantity: 1,
          allocated_quantity: 1,
          allocation_status: 'full',
          allocation_note: '',
          created_at: '2026-10-20T08:00:00Z',
          updated_at: '2026-10-20T08:00:00Z'
        }
      ],
      prepared_package: {
        rule: 'prepared_package.v1',
        scopes: [
          {
            id: 71,
            version: 2,
            current: true,
            verified_cause: 'Recorded mechanical guard concern',
            lines: [{ sequence: 1, action: 'Recorded adjustment scope' }],
            crew_size: null,
            planned_elapsed_minutes: null,
            approved_by: 'Synthetic planner',
            approved_at: '2026-10-20T08:00:00Z',
            note: ''
          }
        ],
        procedures_state: 'read',
        applications: [],
        deviations: [],
        can_record_deviation: false,
        physical_authority: { available: false, reason: 'no_provider' },
        affected_dependencies: { available: false, reason: 'no_provider' }
      },
      structured_closeout: {
        id: 301,
        cause: '',
        action: 'Recorded guard adjustment',
        result: 'Recorded noise reduced',
        verification_summary: 'Recorded repeat measurement retained',
        downtime_minutes: null,
        follow_up_required: true,
        follow_up: 'Operations restriction remains independent',
        completed_at: '2026-10-20T09:00:00Z',
        completed_by: 9,
        verified_at: null,
        amended: true,
        amendment_count: 1
      }
    });
    await openExecutionPanel(page, 'work', scheme);
    const panel = panelRegion(page, 'Work');
    const brief = panel.getByTestId('work-brief');
    await expect(brief.getByRole('heading').first()).toHaveText(
      'Recorded repair outcome'
    );
    await expect(brief).toContainText('Recorded noise reduced');
    await expect(brief).toContainText(
      'Operations restriction remains independent'
    );
    await expect(panel.getByTestId('service-restoration')).toContainText(
      'Service restoration is not established by Work completion.'
    );
    await expect(brief).toContainText(
      'Amended account · 1 retained amendments'
    );
    await expect(brief).toContainText(
      'Synthetic guard fastener · Required: 1 · Allocated: 1'
    );
    await expect(brief).toContainText(
      'Scope 71 · revision 2 · Synthetic planner'
    );
    await expect(
      panel.getByRole('button', {
        name: /Generate|\bAssign\b|Approve|Start time|Update maintenance/
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    expect(onlyReads(requests)).toBe(true);
    await capturePanel(page, info.outputPath(`recorded-outcome-${scheme}.png`));
    await brief
      .getByRole('link', { name: 'View maintenance account and report' })
      .click();
    await expect(
      page.getByRole('tab', { name: 'Closeout', exact: true })
    ).toHaveAttribute('aria-selected', 'true');
  });
}

test('routine inspection remains neutral in dark mode and task paths remain reachable', async ({
  page
}, info) => {
  await mockExecutionWork(page, {
    ...workOrder(),
    work_order_type: 'inspection'
  });
  await openExecutionPanel(page, 'work', 'dark');
  const panel = panelRegion(page, 'Work');
  await expect(panel.getByTestId('work-brief')).toContainText(
    'does not require an invented fault cause'
  );
  await expect(panel.getByTestId('work-brief')).toContainText(
    'Saved request queue not supplied'
  );
  await expect(
    panel.getByRole('heading', { name: 'Board cards', exact: true })
  ).toBeVisible();
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('inspection-unknown-dark.png'));
});

test('cannot-perform stays first class and opens the exact shared request in chat', async ({
  page
}, info) => {
  const request = savedRequest();
  const requests = await mockExecutionWork(page, {
    ...workOrder(),
    investigation: {
      rule: 'investigation.v1',
      packet: null,
      explanation: null,
      findings: [],
      finding_history: 'not_applicable',
      intervention: null,
      requests: { available: true, reason: '', items: [request] }
    }
  });
  await mockAssistantOpen(page);
  await openExecutionPanel(page);
  await revealWorkContext(page, 'Investigation and preparation summary');
  const brief = page.getByTestId('work-brief');
  await expect(brief).toContainText('1 unresolved requests · 1 saved requests');
  await expect(brief).toContainText('cannot perform');
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('operational-exception-light.png'));
  await brief
    .getByRole('button', { name: 'Continue investigation', exact: true })
    .click();
  await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
    'Request 401'
  );
  expect(onlyReads(requests)).toBe(true);
});
