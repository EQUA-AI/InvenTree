import { type Page, expect, test } from '@playwright/test';
import type { RecurrencePolicy } from '../../lib/types/WorkExecution';
import type { FaultOccurrence } from '../../src/pages/assets/faults/occurrenceModel';
import { nativeBaseline } from '../s0/guards';
import { openHealth, signalTable } from '../s3/healthFixtures';
import {
  mockFaultHistory,
  occurrences,
  openFaultHistory
} from '../s3/machineWorkFixtures';
import {
  captureProcessSection,
  openExecutionPanel
} from '../s4/workExecutionFixtures';
import {
  accepted,
  closeoutWork,
  containment,
  openCloseout,
  values
} from '../s6/closeoutFixtures';

nativeBaseline();
// The supplied plant clock must win over an independently configured browser.
test.use({ timezoneId: 'UTC' });

const summary = {
  recorded_telemetry_as_live: false,
  configured: true,
  state: 'warning',
  signal_count: 2,
  stale_signal_count: 0,
  degraded_data: false,
  last_observed_at: '2026-10-20T08:10:00Z',
  active_anomaly_count: 1,
  anomaly_counts: { info: 0, warning: 1, critical: 0 },
  sources: [
    {
      source_id: 5,
      name: 'Synthetic SCADA',
      source_type: 'scada',
      active: true,
      healthy: true,
      last_success_at: '2026-10-20T08:10:00Z',
      last_error_at: null,
      last_error_code: '',
      freshness_threshold_seconds: 300,
      mapped_tag_count: 2
    }
  ]
};
const deviation = {
  ...occurrences[0],
  id: 903,
  external_id: 'NEW-903',
  first_observed_at: '2026-10-20T08:05:00Z',
  last_observed_at: '2026-10-20T08:10:00Z',
  work: [
    {
      ...occurrences[0].work[0],
      id: 99,
      reference: 'WO-99',
      title: 'Synthetic job 99'
    }
  ],
  snapshot_count: 1,
  snapshots: [
    {
      ...occurrences[1].snapshots[0],
      id: 'a3f1c2d4-0000-4000-8000-000000000003',
      signal_label: 'Discharge pressure',
      unit: 'bar',
      binding: 101,
      content_hash: 'd'.repeat(64),
      captured_at: '2026-10-20T08:06:00Z',
      window_start: '2026-10-20T08:05:00Z',
      window_end: '2026-10-20T08:06:00Z',
      quality: 'uncertain',
      stale: true
    }
  ]
};
async function monitoring(
  page: Page,
  options: {
    healthStatus?: number;
    occurrenceStatus?: number;
    unconfigured?: boolean;
    disabled?: boolean;
    empty?: boolean;
    longIdentity?: boolean;
    occurrence?: FaultOccurrence;
    plantTimezone?: string | null;
  } = {}
) {
  const reads: URL[] = [];
  await page.route('**/api/machine-health/machines/18/health/', (route) => {
    reads.push(new URL(route.request().url()));
    return route.fulfill({
      status: options.healthStatus ?? 200,
      json: options.unconfigured
        ? {
            ...summary,
            configured: false,
            state: 'unknown',
            signal_count: 0,
            stale_signal_count: 0,
            active_anomaly_count: 0,
            anomaly_counts: { info: 0, warning: 0, critical: 0 },
            sources: [],
            last_observed_at: null
          }
        : options.disabled
          ? {
              ...summary,
              state: 'unknown',
              degraded_data: true,
              sources: [
                {
                  ...summary.sources[0],
                  active: false,
                  healthy: false,
                  last_success_at: null
                }
              ]
            }
          : summary
    });
  });
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/?*',
    (route) => {
      const url = new URL(route.request().url());
      reads.push(url);
      expect(Object.fromEntries(url.searchParams)).toEqual({
        status: 'active',
        limit: '25',
        offset: '0'
      });
      return route.fulfill({
        status: options.occurrenceStatus ?? 200,
        json: {
          rule: 'fault_occurrences.v1',
          plant_timezone:
            options.plantTimezone === undefined
              ? 'Asia/Riyadh'
              : options.plantTimezone,
          count: options.empty ? 0 : 1,
          limit: 25,
          offset: 0,
          results: options.empty
            ? []
            : [
                options.longIdentity
                  ? {
                      ...deviation,
                      title: 'LongSourceTitle'.repeat(30),
                      external_id: 'LongSourceAlarm'.repeat(30)
                    }
                  : (options.occurrence ?? deviation)
              ]
        }
      });
    }
  );
  return reads;
}
async function capture(page: Page, path: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: true });
  const current = page
    .getByTestId('current-monitoring')
    .filter({ visible: true });
  if (await current.count()) {
    // Keep the real native header above the facts in viewport-size evidence.
    await current.evaluate((element) =>
      element.scrollIntoView({ block: 'start' })
    );
    await page.evaluate(() => window.scrollBy(0, -220));
    await page.screenshot({
      path: path.replace('.png', '-configuration-detail.png')
    });
    await current
      .getByText(/^(Recorded active|Active) deviations$/)
      .evaluate((element) => element.scrollIntoView({ block: 'start' }));
    await page.evaluate(() => window.scrollBy(0, -220));
    await page.screenshot({
      path: path.replace('.png', '-deviation-detail.png')
    });
    await page.evaluate(() => window.scrollTo(0, 0));
  }
}

const visibleContinuity = (page: Page) =>
  page.getByTestId('monitoring-continuity').filter({ visible: true });

for (const scheme of ['light', 'dark'] as const) {
  test(`current configured monitoring and sourced deviations survive maintenance completion, ${scheme}`, async ({
    page
  }, info) => {
    const { commands } = await closeoutWork(page, { completed: true });
    const reads = await monitoring(page);
    await openCloseout(page, scheme);
    expect(reads).toHaveLength(0);
    const disclosure = visibleContinuity(page);
    await disclosure.locator('summary').click();
    await expect(disclosure).toContainText('Current configured monitoring');
    await expect(
      disclosure.getByText('Recorded active deviations', { exact: true })
    ).toBeVisible();
    await expect(disclosure).not.toContainText('before, during and after');
    const current = disclosure.getByTestId('current-monitoring');
    await expect(
      current.getByText('Current Health state', { exact: true })
    ).toBeVisible();
    await expect(current.getByText('Warning', { exact: true })).toBeVisible();
    await expect(disclosure.getByTestId('current-monitoring')).toContainText(
      'Synthetic SCADA'
    );
    await expect(disclosure).toContainText('NEW-903');
    await expect(disclosure).toContainText(
      'Latest observation: 20 Oct 2026 11:10:00'
    );
    await expect(disclosure).toContainText(
      'Last source success: 20 Oct 2026 11:10:00'
    );
    await expect(disclosure).toContainText(
      'First observed: 20 Oct 2026 11:05:00'
    );
    await expect(disclosure).toContainText(
      'Last observed: 20 Oct 2026 11:10:00'
    );
    await expect(disclosure).toContainText('Captured: 20 Oct 2026 11:06:00');
    await expect(disclosure).toContainText('Asia/Riyadh');
    await expect(disclosure.getByText(/^Times:/)).toHaveText(
      'Times: plant clock (Asia/Riyadh, UTC+03:00)'
    );
    await expect(disclosure).toContainText('Quality at capture: Uncertain');
    await expect(disclosure).toContainText('Stale at capture');
    await expect(disclosure).toContainText(
      'Relationship to prior Work unconfirmed'
    );
    await expect(
      disclosure.getByRole('link', {
        name: 'Open exact occurrence and retained evidence'
      })
    ).toHaveAttribute(
      'href',
      '/machines/machine/18/fault-history/?occurrence=903'
    );
    await expect(
      disclosure.getByRole('link', { name: /WO-99/ })
    ).toHaveAttribute(
      'href',
      '/maintenance/work-orders/99/?work_panel=diagnose'
    );
    await expect(page.getByTestId('closeout-account')).toContainText(
      'Corrected recorded result'
    );
    await page.getByText('Original completed account', { exact: true }).click();
    await expect(page.getByTestId('prepared-closeout')).not.toContainText(
      'Accepted by'
    );
    await expect(page.getByTestId('closeout-account')).toContainText(
      values.result
    );
    await expect(page.getByTestId('closeout-account')).toContainText(
      'Synthetic Supervisor'
    );
    expect(commands).toHaveLength(0);
    await containment(page);
    await capture(
      page,
      info.outputPath(`monitoring-after-closeout-${scheme}.png`)
    );
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await visibleContinuity(page).locator('summary').click();
    await expect(visibleContinuity(page)).toContainText(
      'Factual corrections use attributed amendments'
    );
    await expect(visibleContinuity(page)).toContainText(
      'reopened or linked Work flow'
    );
    expect(commands).toHaveLength(0);
  });
}

for (const [name, plantTimezone, reason] of [
  ['missing', null, 'plant timezone not supplied'],
  ['invalid', 'Invalid/Plant', 'plant timezone invalid']
] as const) {
  test(`${name} plant timezone explicitly falls back to the browser clock`, async ({
    page
  }, info) => {
    await closeoutWork(page, { completed: true });
    await monitoring(page, { plantTimezone });
    await openCloseout(page);
    const disclosure = visibleContinuity(page);
    await disclosure.locator('summary').click();
    await expect(disclosure).toContainText('NEW-903');
    await expect(disclosure.getByText(/^Times:/)).toHaveText(
      `Times: your clock (UTC, UTC+00:00) · ${reason}`
    );
    await expect(disclosure).toContainText(
      'Latest observation: 20 Oct 2026 08:10:00'
    );
    await expect(disclosure).toContainText('Captured: 20 Oct 2026 08:06:00');
    await expect(disclosure).not.toContainText('plant clock');
    await containment(page);
    await capture(page, info.outputPath(`monitoring-${name}-timezone.png`));
  });
}

test('a denied occurrence refresh cannot label current monitoring with a cached plant clock', async ({
  page
}, info) => {
  await closeoutWork(page, { completed: true });
  await monitoring(page);
  await openCloseout(page);
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('Times: plant clock (Asia/Riyadh,');
  await expect(disclosure).toContainText('NEW-903');
  await monitoring(page, { occurrenceStatus: 403 });
  await disclosure
    .getByRole('button', {
      name: 'Refresh monitoring and deviations',
      exact: true
    })
    .click();
  await expect(disclosure).toContainText('Deviation records unavailable');
  await expect(disclosure.getByText(/^Times:/)).toHaveText(
    'Times: your clock (UTC, UTC+00:00) · plant timezone not supplied'
  );
  await expect(disclosure).toContainText(
    'Latest observation: 20 Oct 2026 08:10:00'
  );
  await expect(disclosure).toContainText('Connected source');
  await expect(disclosure).not.toContainText('Asia/Riyadh');
  await expect(disclosure).not.toContainText('NEW-903');
  await containment(page);
  await capture(
    page,
    info.outputPath('monitoring-denied-timezone-refresh.png')
  );
});

test('monitoring configuration is available during open work without a closure trigger', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  await monitoring(page);
  await openCloseout(page);
  await page.getByRole('tab', { name: 'Work', exact: true }).click();
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('Current configured monitoring');
  await expect(disclosure).toContainText('No watch period is inferred');
  expect(commands).toHaveLength(0);
  await capture(page, info.outputPath('monitoring-during-work.png'));
});

for (const status of [403, 500]) {
  test(`unavailable monitoring ${status} keeps deviations separate from repair and healthy verdicts`, async ({
    page
  }, info) => {
    await closeoutWork(page, { completed: true });
    await monitoring(page, { healthStatus: status, occurrenceStatus: status });
    await openCloseout(page, 'dark');
    const disclosure = visibleContinuity(page);
    await disclosure.locator('summary').click();
    await expect(disclosure).toContainText(
      status === 403
        ? 'Monitoring unavailable in the current scope'
        : 'Monitoring could not be read'
    );
    await expect(disclosure).toContainText(
      status === 403
        ? 'Deviation records unavailable'
        : 'Deviation records could not be read'
    );
    await expect(disclosure).not.toContainText('Showing 0');
    await expect(disclosure).not.toContainText('Connected source');
    await containment(page);
    await capture(
      page,
      info.outputPath(`monitoring-unavailable-${status}-dark.png`)
    );
  });
}

test('successful empty deviations and unconfigured monitoring stay unknown', async ({
  page
}, info) => {
  await closeoutWork(page);
  await monitoring(page, { empty: true, unconfigured: true });
  await openCloseout(page);
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText(
    'No health source configured — condition unknown'
  );
  await expect(disclosure).toContainText(
    'Showing 0 of 0 active occurrence records'
  );
  await expect(disclosure).toContainText(
    'Latest observation: Time unavailable'
  );
  await capture(page, info.outputPath('monitoring-unconfigured.png'));
});

test('a disabled source has no invented source success or watch duration', async ({
  page
}, info) => {
  await closeoutWork(page);
  await monitoring(page, { disabled: true });
  await openCloseout(page, 'dark');
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('Disabled');
  await expect(disclosure).toContainText(
    'Last source success: Time unavailable'
  );
  await expect(disclosure).toContainText('Data degraded');
  await expect(disclosure).not.toContainText('Connected source');
  await capture(page, info.outputPath('monitoring-disabled-dark.png'));
});

test('Health keeps current observations distinct from retained occurrence evidence', async ({
  page
}, info) => {
  await openHealth(page, { scheme: 'dark' });
  await expect(
    page.getByText(
      'Configured proactive monitoring applies before, during and after maintenance and Closeout.',
      { exact: false }
    )
  ).toHaveCount(0);
  const currentFlow = signalTable(page).getByRole('row', {
    name: /Discharge flow/
  });
  await expect(currentFlow).toContainText('812.5');
  await expect(currentFlow).not.toContainText('118.4');
  await expect(signalTable(page)).not.toContainText('Retained snapshot');
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('health-continuity-dark.png')
  });
  await mockFaultHistory(page);
  await openFaultHistory(page, { query: '?occurrence=901', scheme: 'dark' });
  const retained = page.getByTestId('occurrence-evidence');
  await expect(retained).toContainText('118.4');
  await expect(retained).toContainText('Synthetic SCADA');
  await expect(retained).toContainText('Evidence retained at the time');
  await expect(retained).toContainText('Retained snapshot');
  await containment(page);
  await capture(page, info.outputPath('fault-retained-continuity-dark.png'));
});

test('a refused refresh hides previously read source and deviation facts', async ({
  page
}, info) => {
  await closeoutWork(page, { completed: true });
  await monitoring(page);
  await openCloseout(page, 'dark');
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('NEW-903');
  await expect(disclosure).toContainText('Connected source');
  await monitoring(page, { healthStatus: 403, occurrenceStatus: 403 });
  await disclosure
    .getByRole('button', {
      name: 'Refresh monitoring and deviations',
      exact: true
    })
    .click();
  await expect(disclosure).toContainText(
    'Monitoring unavailable in the current scope'
  );
  await expect(disclosure).toContainText('Deviation records unavailable');
  await expect(disclosure).not.toContainText('NEW-903');
  await expect(disclosure).not.toContainText('Connected source');
  await capture(page, info.outputPath('monitoring-refused-refresh-dark.png'));
});

test('long source identities remain readable within the phone and desktop', async ({
  page
}, info) => {
  await closeoutWork(page);
  await monitoring(page, { longIdentity: true });
  await openCloseout(page);
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  await expect(disclosure).toContainText('LongSourceAlarm'.repeat(30));
  await containment(page);
  await capture(page, info.outputPath('monitoring-long-source.png'));
});

for (const scheme of ['light', 'dark'] as const) {
  for (const action of ['create_linked', 'reopen'] as const) {
    test(`O15 same-component recurrence ${action} is explicit (${scheme})`, async ({
      page
    }, info) => {
      const { commands, setOverview } = await closeoutWork(page, {
        completed: true,
        ...(action === 'reopen' ? { captures: [] } : {})
      });
      const componentRef = occurrences[1].components[0].code;
      const observedAt = deviation.first_observed_at;
      const freshOccurrence: FaultOccurrence = {
        ...deviation,
        id: 904,
        severity: 'warning',
        status: 'open',
        active: true,
        first_observed_at: observedAt,
        external_id: 'SYN-REC-904',
        title: 'Synthetic same-component deviation',
        evidence_summary:
          'Synthetic retained component deviation; interpretation unresolved',
        metrics: {},
        signals: occurrences[1].signals,
        components: occurrences[1].components,
        work: [],
        repair_packet: null,
        snapshots: [
          {
            ...deviation.snapshots[0],
            signal_label: 'DE bearing temperature',
            unit: 'degC',
            binding: 100,
            quality: 'uncertain'
          }
        ]
      };
      const policy = {
        rule: 'recurrence_policy.v1' as const,
        days: 30,
        available: true,
        prior_work_id: 73,
        prior_version: 8,
        can_create: true,
        can_reopen: action === 'reopen',
        closure_acceptance:
          action === 'reopen'
            ? ('awaiting_verification' as const)
            : ('unknown' as const),
        items: [
          {
            occurrence_id: 904,
            title: freshOccurrence.title,
            observed_at: observedAt,
            component_ref: componentRef,
            action,
            closure_acceptance:
              action === 'reopen'
                ? ('awaiting_verification' as const)
                : ('unknown' as const),
            work_order_id: null as number | null
          }
        ]
      };
      setOverview({
        affected_component_ref: componentRef,
        affected_component: freshOccurrence.components[0].name,
        structured_closeout: {
          ...accepted!,
          verified_at: null,
          amended: action === 'create_linked',
          amendment_count: action === 'create_linked' ? 1 : 0,
          result: action === 'reopen' ? values.result : accepted!.result
        },
        recurrence_policy: policy
      });
      await monitoring(page, { occurrence: freshOccurrence });
      const posted: Record<string, unknown>[] = [];
      await page.route(
        '**/api/kanban/work-orders/commands/create/',
        async (route) => {
          const body = route.request().postDataJSON();
          posted.push(body);
          const linked = action === 'create_linked';
          const id = linked ? 80 : 73;
          freshOccurrence.work = [
            {
              id,
              reference: `WO-${id}`,
              title: 'Synthetic recurrence response',
              lifecycle_status: linked ? 'draft' : 'in_progress',
              priority: 'medium',
              work_order_type: 'corrective',
              affected_component: 'Drive-end bearing',
              actual_completed_at: null,
              relation: 'owning'
            }
          ];
          if (!linked)
            setOverview({
              lifecycle_status: 'in_progress',
              lifecycle_version: 9,
              actual_completed_at: null
            });
          if (linked) {
            policy.items[0].work_order_id = 80;
            (policy.items[0] as { action: string }).action = 'linked';
          } else
            (policy.items[0] as { action: string }).action = 'already_reopened';
          await route.fulfill({
            status: 201,
            json: {
              id,
              process_receipt: {
                command: 'create',
                work_order_id: id,
                event_id: 101,
                idempotency_key: body.idempotency_key,
                lifecycle_version: linked ? 1 : 9,
                metadata: {
                  occurrence_id: 904,
                  ...(linked ? { recurrence_of: 73 } : {})
                }
              }
            }
          });
        }
      );
      await openExecutionPanel(page, 'closeout', scheme, false, 'editor');
      const disclosure = visibleContinuity(page);
      await disclosure.locator('summary').click();
      const section = disclosure.getByTestId('recurrence-policy');
      await expect(
        section.getByText('Same-component recurrence policy · 30 days')
      ).toBeVisible();
      expect(posted).toHaveLength(0);
      expect(commands).toHaveLength(0);
      await captureProcessSection(
        page,
        section,
        info.outputPath(`o15-recurrence-${action}-${scheme}.png`)
      );
      await section
        .getByRole('button', {
          name:
            action === 'reopen'
              ? 'Reopen unaccepted Work'
              : 'Create linked corrective work',
          exact: true
        })
        .click();
      await expect(
        section.getByText(
          action === 'reopen'
            ? 'Work reopened; the previous unaccepted closure is retained.'
            : 'Linked corrective Work 80 created; the previous closure is unchanged.'
        )
      ).toBeVisible();
      expect(posted).toHaveLength(1);
      expect(posted[0].recurrence_of).toBe(73);
      expect(posted[0].recurrence_occurrence).toBe(904);
      const account = page.getByTestId('closeout-account');
      if (action === 'create_linked') {
        await expect(account).toContainText(accepted!.result);
        await account
          .locator('summary')
          .filter({ hasText: /^Original completed account$/ })
          .click();
        await expect(
          account.getByText(`Result: ${values.result}`, { exact: true })
        ).toBeVisible();
      } else {
        await expect(
          account.getByText(values.result, { exact: true }).first()
        ).toBeVisible();
      }
      await containment(page);
    });
  }
}

test('review refused recurrence survives reload and can be reviewed then retried', async ({
  page
}, info) => {
  const { setOverview } = await closeoutWork(page, { completed: true });
  const policy = {
    rule: 'recurrence_policy.v1' as const,
    days: 30,
    available: true,
    closure_acceptance: 'unknown' as const,
    can_create: true,
    can_reopen: false,
    items: [
      {
        occurrence_id: 904,
        title: 'Synthetic retry deviation',
        observed_at: deviation.first_observed_at,
        component_ref: 'PS-MOTOR-DE-BRG:1',
        action: 'create_linked' as 'create_linked' | 'linked',
        work_order_id: null as number | null,
        closure_acceptance: 'unknown' as const,
        reopen_blocked_reason: 'closure_revision_required'
      }
    ]
  };
  setOverview({ recurrence_policy: policy });
  await monitoring(page);
  const bodies: Record<string, any>[] = [];
  await page.route(
    '**/api/kanban/work-orders/commands/create/',
    async (route) => {
      const body = route.request().postDataJSON();
      bodies.push(body);
      if (bodies.length === 1) {
        setOverview({ lifecycle_version: 9 });
        return route.fulfill({
          status: 409,
          json: {
            code: 'STALE_VERSION',
            detail: 'Work changed; review latest recurrence.'
          }
        });
      }
      policy.items[0].action = 'linked';
      policy.items[0].work_order_id = 80;
      await route.fulfill({
        status: 201,
        json: {
          id: 80,
          reference: 'WO-80',
          process_receipt: {
            command: 'create',
            work_order_id: 80,
            event_id: 111,
            idempotency_key: body.idempotency_key,
            lifecycle_version: 1,
            metadata: {
              recurrence_of: 73,
              occurrence_id: 904,
              closure_acceptance: 'unknown',
              outcome: 'linked_created'
            }
          }
        }
      });
    }
  );
  await openExecutionPanel(page, 'closeout', 'light', false, 'editor');
  const disclosure = visibleContinuity(page);
  await disclosure.locator('summary').click();
  const section = disclosure.getByTestId('recurrence-policy');
  await expect(
    section.getByText(/Closure acceptance is not recorded/)
  ).toBeVisible();
  await expect(
    section.getByText(/Retained closure evidence prevents reopening/)
  ).toBeVisible();
  await section
    .getByRole('button', { name: 'Create linked corrective work', exact: true })
    .click();
  await expect(
    section.getByText('Work changed; review latest recurrence.')
  ).toBeVisible();
  await page.reload();
  await disclosure.locator('summary').click();
  await expect(
    section.getByRole('button', {
      name: 'Create linked corrective work',
      exact: true
    })
  ).toHaveCount(0);
  await section
    .getByRole('button', { name: 'Review latest recurrence', exact: true })
    .click();
  await section
    .getByRole('button', { name: 'Create linked corrective work', exact: true })
    .click();
  await expect(
    section.getByText(
      'Linked corrective Work WO-80 created; the previous closure is unchanged.'
    )
  ).toBeVisible();
  expect(bodies).toHaveLength(2);
  expect(bodies[1].prior_version).toBe(9);
  expect(bodies[1].idempotency_key).not.toBe(bodies[0].idempotency_key);
  await section
    .getByRole('button', { name: 'Review latest recurrence', exact: true })
    .click();
  await expect(
    section.getByRole('link', {
      name: 'Linked corrective Work: 80',
      exact: true
    })
  ).toBeVisible();
  expect(bodies).toHaveLength(2);
  await expect(section).toContainText(
    'Linked corrective Work is already recorded for this deviation.'
  );
  await expect(
    section.getByText(
      'Closure acceptance is not recorded. This does not permit reopening; create linked corrective work.',
      { exact: true }
    )
  ).toHaveCount(0);
  await captureProcessSection(
    page,
    section,
    info.outputPath('review-recurrence-refusal-recovered-light.png')
  );
});

function followupPolicy(title: string, canCreate = true): RecurrencePolicy {
  return {
    rule: 'recurrence_policy.v1',
    days: 30,
    available: true,
    can_create: canCreate,
    can_reopen: false,
    closure_acceptance: 'unknown',
    items: [
      {
        occurrence_id: 904,
        title,
        component_ref: 'PS-MOTOR-DE-BRG:1',
        observed_at: deviation.first_observed_at,
        action: 'create_linked',
        closure_acceptance: 'unknown',
        work_order_id: null
      }
    ]
  };
}

for (const scheme of ['light', 'dark'] as const) {
  for (const canCreate of [true, false]) {
    test(`followup2 long recurrence title and ${canCreate ? 'linked response' : 'viewer role'} (${scheme})`, async ({
      page
    }, info) => {
      const { setOverview } = await closeoutWork(page, { completed: true });
      const title = 'Synthetic source deviation '.repeat(10).slice(0, 255);
      expect(title).toHaveLength(255);
      const policy = followupPolicy(title, canCreate);
      setOverview({
        structured_closeout: { ...accepted!, verified_at: null },
        recurrence_policy: policy
      });
      await monitoring(page);
      const bodies: Record<string, any>[] = [];
      await page.route(
        '**/api/kanban/work-orders/commands/create/',
        async (route) => {
          const body = route.request().postDataJSON();
          bodies.push(body);
          expect(Array.from(body.title)).toHaveLength(200);
          expect(body.title).toBe(title.slice(0, 200));
          policy.items[0].action = 'linked';
          policy.items[0].work_order_id = 80;
          await route.fulfill({
            status: 201,
            json: {
              id: 80,
              reference: 'WO-80',
              process_receipt: {
                command: 'create',
                work_order_id: 80,
                event_id: 119,
                idempotency_key: body.idempotency_key,
                lifecycle_version: 1,
                metadata: {
                  recurrence_of: 73,
                  occurrence_id: 904,
                  closure_acceptance: 'unknown'
                }
              }
            }
          });
        }
      );
      await openExecutionPanel(
        page,
        'closeout',
        scheme,
        false,
        canCreate ? 'editor' : 'viewer'
      );
      const disclosure = visibleContinuity(page);
      await disclosure.locator('summary').click();
      const section = disclosure.getByTestId('recurrence-policy');
      await expect(section).toContainText(title);
      const create = section.getByRole('button', {
        name: 'Create linked corrective work',
        exact: true
      });
      if (canCreate) {
        await create.click();
        await expect(
          section.getByRole('link', {
            name: 'Linked corrective Work: 80',
            exact: true
          })
        ).toBeVisible();
        await expect(section).toContainText(
          'Linked corrective Work is already recorded for this deviation.'
        );
        await expect(
          section.getByText(
            'Closure acceptance is not recorded. This does not permit reopening; create linked corrective work.',
            { exact: true }
          )
        ).toHaveCount(0);
        await expect(create).toHaveCount(0);
        expect(bodies).toHaveLength(1);
      } else {
        await expect(section).toContainText(
          'A Work creation role is required for linked corrective work.'
        );
        await expect(
          section.getByText(
            'Closure acceptance is not recorded. This does not permit reopening; create linked corrective work.',
            { exact: true }
          )
        ).toHaveCount(0);
        await expect(create).toHaveCount(0);
        expect(bodies).toHaveLength(0);
      }
      await containment(page);
      await captureProcessSection(
        page,
        section,
        info.outputPath(
          `followup2-title-${canCreate ? 'linked' : 'viewer'}-${scheme}.png`
        )
      );
    });
  }

  test(`followup2 stale panel review preserves the newer uncertain recurrence (${scheme})`, async ({
    page
  }, info) => {
    const { setOverview } = await closeoutWork(page, { completed: true });
    const policy = followupPolicy('Synthetic retained-operation deviation');
    setOverview({ recurrence_policy: policy });
    await monitoring(page);
    const bodies: Record<string, any>[] = [];
    await page.route(
      '**/api/kanban/work-orders/commands/create/',
      async (route) => {
        const body = route.request().postDataJSON();
        bodies.push(body);
        if (bodies.length === 1) {
          setOverview({ lifecycle_version: 9 });
          return route.fulfill({
            status: 409,
            json: { detail: 'Synthetic first operation refused.' }
          });
        }
        if (bodies.length === 2) return route.abort('failed');
        policy.items[0].action = 'linked';
        policy.items[0].work_order_id = 80;
        return route.fulfill({
          status: 201,
          json: {
            id: 80,
            reference: 'WO-80',
            process_receipt: {
              command: 'create',
              work_order_id: 80,
              event_id: 120,
              idempotency_key: body.idempotency_key,
              lifecycle_version: 1,
              metadata: { recurrence_of: 73, occurrence_id: 904 }
            }
          }
        });
      }
    );
    await openExecutionPanel(page, 'closeout', scheme, false, 'editor');
    const disclosure = visibleContinuity(page);
    await disclosure.locator('summary').click();
    const section = disclosure.getByTestId('recurrence-policy');
    const create = section.getByRole('button', {
      name: 'Create linked corrective work',
      exact: true
    });
    const review = section.getByRole('button', {
      name: 'Review latest recurrence',
      exact: true
    });
    await create.click();
    await expect(section).toContainText('Synthetic first operation refused.');
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await disclosure.locator('summary').click();
    await expect(review).toBeVisible();
    await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
    await review.click();
    await create.click();
    await expect(section).toContainText('Recurrence outcome unknown.');
    expect(bodies).toHaveLength(2);
    expect(bodies[1].idempotency_key).not.toBe(bodies[0].idempotency_key);
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    // This mounted instance still shows K1; it must adopt K2 instead of clearing it.
    await expect(review).toBeVisible();
    await review.click();
    await expect(section).toContainText('Recurrence outcome unknown.');
    await expect(create).toHaveCount(0);
    const retained = await page.evaluate(() => {
      const key = Object.keys(localStorage).find(
        (k) =>
          k.startsWith('aimms-process:') && k.includes('"recurrence",73,904')
      );
      return key ? JSON.parse(localStorage.getItem(key)!) : null;
    });
    expect(retained.status).toBe('uncertain');
    expect(retained.intent.payload).toEqual(bodies[1]);
    const uncertainAlert = section.getByRole('alert');
    const reconcile = uncertainAlert.getByRole('button', {
      name: 'Check original recurrence command',
      exact: true
    });
    await expect(reconcile).toBeVisible();
    // Mantine Alert and button flex children must fit; page containment alone
    // cannot catch content clipped by an alert's overflow:hidden.
    expect(
      await reconcile.evaluate((button) => {
        const alert = button.closest('[role="alert"]')!;
        const outer = alert.getBoundingClientRect();
        const inner = button.getBoundingClientRect();
        const label = button.querySelector('.mantine-Button-label')!;
        return (
          inner.left >= outer.left &&
          inner.right <= outer.right &&
          label.scrollWidth <= label.clientWidth + 1
        );
      })
    ).toBe(true);
    await containment(page);
    await captureProcessSection(
      page,
      section,
      info.outputPath(`followup2-stale-review-uncertain-${scheme}.png`)
    );
    await section
      .getByRole('button', {
        name: 'Check original recurrence command',
        exact: true
      })
      .click();
    await expect(section).toContainText(
      'Linked corrective Work WO-80 created; the previous closure is unchanged.'
    );
    expect(bodies).toHaveLength(3);
    expect(bodies[2]).toEqual(bodies[1]);
    await expect(
      section.getByText(
        'Closure acceptance is not recorded. This does not permit reopening; create linked corrective work.',
        { exact: true }
      )
    ).toHaveCount(0);
    await containment(page);
    await captureProcessSection(
      page,
      section,
      info.outputPath(`followup2-stale-review-reconciled-${scheme}.png`)
    );
  });
}
