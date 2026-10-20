import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  continuityWork,
  recordedPoint,
  saveTransition
} from './continuityFixtures';
import { revisionWork } from './scopeRevisionFixtures';
import {
  capturePanel,
  expectNoPageOverflow,
  mockAssistantOpen,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  panelRegion
} from './workExecutionFixtures';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`held continuity retains owners conditions unknown acceptance and separate related work (${scheme})`, async ({
    page
  }, info) => {
    const requests = await mockExecutionWork(page, continuityWork());
    await openExecutionPanel(page, 'work', scheme);
    const brief = page.getByTestId('work-continuity');
    await expect(
      brief.getByText('Synthetic equipment access unavailable', { exact: true })
    ).toBeVisible();
    await expect(
      brief.getByText(/Synthetic outgoing technician.*20 Oct 2026/)
    ).toBeVisible();
    await expect(
      brief.getByText(
        /Assignment, a read receipt.*do not accept responsibility/
      )
    ).toBeVisible();
    const request = brief.getByTestId('continuity-request-401');
    await expect(
      brief
        .getByTestId('continuity-request-402')
        .getByText('Synthetic second technician', { exact: true })
    ).toBeVisible();
    await expect(request.getByText('Request 401 · v3')).toBeVisible();
    await expect(
      request.getByText('Synthetic request owner', { exact: true })
    ).toBeVisible();
    await expect(
      request.getByText(
        'Recorded constraint: Synthetic first instrument unavailable',
        { exact: true }
      )
    ).toBeVisible();
    await expect(request.getByText(/20 Oct 2026, 15:00/)).toBeVisible();
    await expect(
      brief.getByRole('button', {
        name: /Accept assignment|Resume work record|Hold work record/
      })
    ).toHaveCount(0);
    for (const name of [
      'Saved work and material context',
      'Saved energy-control records',
      'Related work dependencies (1 visible)'
    ])
      await brief.getByRole('button', { name, exact: true }).click();
    await expect(
      brief.getByText(/Installed material state: Not recorded/)
    ).toBeVisible();
    await expect(
      brief.getByText(/No lockout points returned for this Work/)
    ).toBeVisible();
    await expect(
      brief.getByText(/In progress.*Work version: 4.*Synthetic sibling owner/)
    ).toBeVisible();
    await expect(
      brief.getByRole('link', {
        name: 'WO-74 · Synthetic sibling electrical check'
      })
    ).toHaveAttribute('href', '/maintenance/work-orders/74/');
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`continuity-brief-${scheme}.png`));
    await request.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`continuity-request-${scheme}-viewport.png`),
      animations: 'disabled'
    });
    await page.getByRole('tab', { name: 'Plan & Safety', exact: true }).click();
    await expect(
      panelRegion(page, 'Plan & Safety').getByTestId('continuity-constraints')
    ).toContainText('Synthetic first instrument unavailable');
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await expect(
      page
        .getByTestId('continuity-history')
        .getByText('Synthetic equipment access unavailable', { exact: true })
    ).toBeVisible();
    await capturePanel(
      page,
      info.outputPath(`continuity-history-${scheme}.png`)
    );
    expect(onlyReads(requests)).toBe(true);
  });
}

test('existing hold and resume commands use current versions and keep checks and sibling state', async ({
  page
}, info) => {
  const overview = continuityWork(false, true);
  await mockExecutionWork(page, overview);
  const writes: {
    action: string;
    body: { expected_version: number; idempotency_key: string; reason: string };
  }[] = [];
  await page.route(
    /\/api\/tasks\/work-orders\/73\/(hold|resume)\/$/,
    async (route) => {
      const action = new URL(route.request().url()).pathname.includes('/hold/')
        ? 'hold'
        : 'resume';
      const body = route.request().postDataJSON();
      writes.push({ action, body });
      await route.fulfill({ json: saveTransition(overview, action, body) });
    }
  );
  await openExecutionPanel(page, 'work');
  const record = page.getByTestId('hold-resume-record');
  await record
    .getByLabel('Reason for hold', { exact: true })
    .fill('Synthetic changed access condition');
  await record
    .getByRole('button', { name: 'Hold work record', exact: true })
    .dblclick();
  await expect(
    record.getByText('Hold recorded', { exact: true })
  ).toBeVisible();
  await expect(record.getByText(/Synthetic command recorder/)).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0].body.expected_version).toBe(7);
  await page.reload();
  await expect(
    record.getByText('Hold recorded', { exact: true })
  ).toBeVisible();
  expect(writes).toHaveLength(1);
  await expect(
    record.getByRole('button', { name: 'Resume work record', exact: true })
  ).toBeEnabled();
  await expect(
    record.getByLabel('Record of resume checks (optional)')
  ).toHaveValue('');
  await record
    .getByLabel('Record of resume checks (optional)')
    .fill('Synthetic record requirements reviewed');
  await record
    .getByRole('button', { name: 'Resume work record', exact: true })
    .click();
  await expect(
    record.getByText('Resume recorded', { exact: true })
  ).toBeVisible();
  expect(writes).toHaveLength(2);
  expect(writes[1].body.expected_version).toBe(8);
  expect(writes[1].body.idempotency_key).not.toBe(
    writes[0].body.idempotency_key
  );
  const brief = page.getByTestId('work-continuity');
  await expect(
    brief
      .getByTestId('continuity-request-401')
      .getByText('Cannot perform — unresolved', { exact: true })
  ).toBeVisible();
  expect(overview.investigation!.requests.items![0].version).toBe(3);
  expect(overview.dependencies[0].card.lifecycle_version).toBe(4);
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('continuity-resumed-light.png'));
});

test('uncertain hold survives reload and a reconciliation refusal retains the original operation', async ({
  page
}, info) => {
  const overview = continuityWork(false, true);
  await mockExecutionWork(page, overview);
  const bodies: {
    expected_version: number;
    idempotency_key: string;
    reason: string;
  }[] = [];
  await page.route('**/api/tasks/work-orders/73/hold/', async (route) => {
    const body = route.request().postDataJSON();
    bodies.push(body);
    if (bodies.length === 1) await route.abort('failed');
    else if (bodies.length === 2)
      await route.fulfill({
        status: 403,
        json: {
          code: 'PERMISSION_DENIED',
          detail: 'Synthetic execution permission changed'
        }
      });
    else await route.fulfill({ json: saveTransition(overview, 'hold', body) });
  });
  await openExecutionPanel(page, 'work', 'dark');
  await page
    .getByLabel('Reason for hold', { exact: true })
    .fill('Synthetic original uncertain reason');
  await page
    .getByRole('button', { name: 'Hold work record', exact: true })
    .click();
  await expect(
    page.getByText('Original operation unconfirmed', { exact: true })
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText('Original operation unconfirmed', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText(/Retained reason: Synthetic original uncertain reason/)
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Check original operation', exact: true })
    .click();
  await expect(
    page.getByText(/a refusal now cannot disprove an earlier save/)
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Hold work record', exact: true })
  ).toHaveCount(0);
  // The uncertain wording is already shown before the check; wait for it.
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toEqual(bodies[0]);
  await capturePanel(page, info.outputPath('continuity-uncertain-dark.png'));
  await page
    .getByRole('button', { name: 'Check original operation', exact: true })
    .click();
  await expect(page.getByText('Hold recorded', { exact: true })).toBeVisible();
  expect(bodies[2]).toEqual(bodies[0]);
});

test('stale command keeps the reason and requires a fresh read before another intent', async ({
  page
}, info) => {
  const overview = continuityWork(false, true);
  await mockExecutionWork(page, overview);
  const bodies: {
    expected_version: number;
    idempotency_key: string;
    reason: string;
  }[] = [];
  await page.route('**/api/tasks/work-orders/73/hold/', async (route) => {
    const body = route.request().postDataJSON();
    bodies.push(body);
    if (bodies.length === 1) {
      overview.lifecycle_version = 8;
      overview.work_continuity!.lifecycle_version = 8;
      await route.fulfill({
        status: 409,
        json: {
          code: 'STALE_VERSION',
          detail: 'Synthetic newer Work revision',
          current_version: 8
        }
      });
    } else
      await route.fulfill({ json: saveTransition(overview, 'hold', body) });
  });
  await openExecutionPanel(page, 'work');
  await page
    .getByLabel('Reason for hold', { exact: true })
    .fill('Synthetic retained stale reason');
  await page
    .getByRole('button', { name: 'Hold work record', exact: true })
    .click();
  await expect(
    page
      .getByTestId('hold-resume-record')
      .getByText('Work changed', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Hold work record', exact: true })
  ).toHaveCount(0);
  expect(bodies).toHaveLength(1);
  await capturePanel(page, info.outputPath('continuity-stale-light.png'));
  await page
    .getByRole('button', {
      name: 'Review latest Work before retrying',
      exact: true
    })
    .click();
  await expect(page.getByLabel('Reason for hold', { exact: true })).toHaveValue(
    'Synthetic retained stale reason'
  );
  await page
    .getByRole('button', { name: 'Hold work record', exact: true })
    .click();
  await expect(page.getByText('Hold recorded', { exact: true })).toBeVisible();
  expect(bodies[1].expected_version).toBe(8);
  expect(bodies[1].idempotency_key).not.toBe(bodies[0].idempotency_key);
});

test('resume refusal shows the recorded readiness blocker and keeps the hold', async ({
  page
}, info) => {
  const overview = continuityWork(true, true);
  overview.assigned_to = null;
  overview.assigned_to_name = null;
  overview.assigned_to_username = null;
  await mockExecutionWork(page, overview);
  let writes = 0;
  await page.route('**/api/tasks/work-orders/73/resume/', async (route) => {
    writes += 1;
    await route.fulfill({
      status: 409,
      json: {
        code: 'ASSIGNEE_REQUIRED',
        detail: 'Work order readiness blocked',
        blockers: [{ message: 'A typed assignee is required for this action' }]
      }
    });
  });
  await openExecutionPanel(page, 'work', 'dark');
  await page
    .getByRole('button', { name: 'Resume work record', exact: true })
    .click();
  const record = page.getByTestId('hold-resume-record');
  await expect(
    record.getByText(/A typed assignee is required for this action/)
  ).toBeVisible();
  await expect(
    record.getByText('Resume recorded', { exact: true })
  ).toHaveCount(0);
  await expect(
    page
      .getByTestId('work-continuity')
      .getByText('Synthetic equipment access unavailable', { exact: true })
  ).toBeVisible();
  expect(writes).toBe(1);
  expect(overview.lifecycle_status).toBe('on_hold');
  expect(overview.investigation!.requests.items![0].status).toBe(
    'cannot_perform'
  );
  await capturePanel(
    page,
    info.outputPath('continuity-resume-refused-dark.png')
  );
});

test('packet ownership and absent acceptance providers create no competing action', async ({
  page
}, info) => {
  const overview = revisionWork();
  overview.verification_return = continuityWork().verification_return;
  overview.verification_return!.lockouts = [{ ...recordedPoint }];
  overview.repair_packet!.gates = [
    {
      id: 81,
      name: 'Synthetic independent permit constraint',
      gate_type: 'permit',
      status: 'pending',
      is_blocking: true,
      is_mandatory: true,
      requires_photo: false,
      requires_second_person: false
    }
  ];
  overview.work_continuity = continuityWork(false).work_continuity;
  overview.work_continuity!.commands = {
    hold: false,
    resume: false,
    unavailable_reason: 'packet_owns_lifecycle'
  };
  await mockExecutionWork(page, overview);
  await openExecutionPanel(page, 'work', 'dark');
  const brief = page.getByTestId('work-continuity');
  await expect(
    brief.getByText(/repair packet owns this Work lifecycle/)
  ).toBeVisible();
  await expect(
    brief.getByRole('button', {
      name: /Accept assignment|Hold work record|Resume work record/
    })
  ).toHaveCount(0);
  await capturePanel(page, info.outputPath('continuity-packet-owner-dark.png'));
  await brief
    .getByRole('button', { name: 'Saved gate requirements (1)' })
    .click();
  await expect(
    brief.getByText(
      /Synthetic independent permit constraint.*Recorded as blocking/
    )
  ).toBeVisible();
  await brief
    .getByRole('button', { name: 'Saved energy-control records' })
    .click();
  await expect(
    brief.getByText(/Synthetic feeder point.*restored/)
  ).toBeVisible();
});

test('request discussion preserves its saved identity and older servers stay explicitly unavailable', async ({
  page
}, info) => {
  const overview = continuityWork();
  await mockExecutionWork(page, overview);
  await mockAssistantOpen(page);
  await openExecutionPanel(page, 'work');
  await page
    .getByTestId('continuity-request-401')
    .getByRole('button', { name: 'Discuss request 401' })
    .click();
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
    'Request 401'
  );
  await page
    .getByRole('button', { name: 'close-ai-chat', exact: true })
    .click();
  delete overview.work_continuity;
  overview.investigation!.requests = {
    available: false,
    reason: 'no_provider'
  };
  await page.reload();
  await expect(
    page.getByText('Work continuity not supplied', { exact: true })
  ).toBeVisible();
  await expect(page.getByText(/Pending saved requests \(0\)/)).toHaveCount(0);
  await expect(
    page.getByRole('button', {
      name: /Accept assignment|Hold work record|Resume work record/
    })
  ).toHaveCount(0);
  await capturePanel(page, info.outputPath('continuity-older-server.png'));
});
