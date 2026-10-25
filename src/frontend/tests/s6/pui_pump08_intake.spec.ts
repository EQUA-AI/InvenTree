import { writeFileSync } from 'node:fs';
import { type Page, expect, test } from '@playwright/test';
import { auditAccessibility, waitForRenderedScope } from '../s0/accessibility';
import { assertReadableContrast } from '../s0/contrast';
import { nativeBaseline } from '../s0/guards';
import { openHealth } from '../s3/healthFixtures';
import { captureAuthorityRegion } from '../s4/authorityFixtures';
import {
  expectNoPageOverflow,
  mockExecutionWork,
  openExecutionPanel,
  workOrder
} from '../s4/workExecutionFixtures';
import { mockPump08Staff } from './staffFixtures';

nativeBaseline();

const fields = {
  title: 'Pump 08 | High Motor Winding Temperature',
  work_scope:
    'Investigate the high Motor Winding Temperature 6 indication on Pump 08. Validate source and timestamp; review Winding 5, peer channels, running/load state, cooling conditions, maintenance history and the attached WEG 11866576 motor manual. Consider cooling impairment, electrical/load conditions, localized winding concern and measurement-path issues. Cleaning is conditional on inspection evidence supporting fouling of the applicable cooler. No physical fault, installed configuration or operating release is established by this prepared specification.',
  symptom:
    'Motor Winding Temperature 6 high indication. Review Winding 5, sensor validity, cooling and operating conditions.',
  production_impact:
    'Potential interruption of Pump 08 availability; actual process impact not established.'
};

async function openIntake(
  page: Page,
  scheme: 'light' | 'dark',
  ai = false,
  options: {
    owned?: boolean;
    sourceError?: boolean;
    draftGate?: Promise<void>;
    draftFailure?: { status: number; code?: string; retryAfter?: string };
    stale?: boolean;
  } = {}
) {
  const creates: Record<string, any>[] = [];
  const drafts: Record<string, unknown>[] = [];
  await mockPump08Staff(page);
  await mockExecutionWork(page, {
    ...workOrder(),
    title: fields.title,
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
  await page.route(
    '**/api/machine-health/machines/18/health/test-alarm/',
    (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({
          json: {
            results: [
              {
                binding_id: 1494,
                name: 'Motor Winding Temperature 6',
                component_id: 21,
                component_name: 'Electric Motor',
                component_code: 'PS-MOTOR:1',
                value: 44,
                unit: '°C',
                critical_max: 145,
                eligible: true,
                value_floor: 145,
                suggested_test_value: 159.5,
                limits: { normal_max: 124.9, warn_max: 125, critical_max: 145 }
              }
            ]
          }
        });
      const body = route.request().postDataJSON();
      return route.fulfill({
        json: {
          ...body,
          anomaly_id: 501,
          machine_id: 18,
          binding_id: 1494,
          status: 'open',
          test_value: 159.5,
          unit: '°C'
        }
      });
    }
  );
  await page.route('**/api/maintenance/work-packages/intake/?*', (route) =>
    route.fulfill({
      status: options.sourceError ? 403 : 200,
      json: {
        context: {
          machine: {
            id: 18,
            name: 'Cedar Creek Final Effluent Pump Station / Pump 08'
          },
          source: {
            anomaly_id: 501,
            title: 'TEST winding indication',
            parameter: 'Motor Winding Temperature 6',
            synthetic: true,
            test_value: 159.5,
            measured_value: 44,
            unit: '°C',
            measurement_stale: options.stale ?? false,
            measurement_quality: 'good',
            work_order_id: options.owned ? 3826 : null
          }
        },
        provider: {
          available: ai,
          reason: ai ? '' : 'provider_not_configured'
        },
        can_create: !options.owned,
        prepared_scenario: {
          fields,
          suggested_owner_username: 'cc.avery.patel',
          priority: 'high',
          criticality: 'critical',
          work_order_type: 'corrective',
          limitations:
            'Prepared specification only; no physical work or operating authority.'
        }
      }
    })
  );
  await page.route(
    '**/api/maintenance/work-packages/intake/draft/',
    async (route) => {
      drafts.push(route.request().postDataJSON());
      if (options.draftGate) await options.draftGate;
      if (options.draftFailure)
        return route.fulfill({
          status: options.draftFailure.status,
          headers: options.draftFailure.retryAfter
            ? { 'Retry-After': options.draftFailure.retryAfter }
            : {},
          json: {
            code: options.draftFailure.code ?? 'AI_INTAKE_UNAVAILABLE',
            detail: 'Fixture provider refusal'
          }
        });
      return route.fulfill({
        json: {
          fields,
          draft_token: 'fixture-only-not-a-real-token',
          provider: {
            model: 'fixture-provider',
            run_id: 'fixture-inference-not-live'
          },
          review_required: true
        }
      });
    }
  );
  await page.route('**/api/maintenance/work-packages/assignees/?*', (route) =>
    route.fulfill({
      json: {
        results: [{ id: 27, name: 'Avery Patel' }]
      }
    })
  );
  await page.route('**/api/maintenance/work-packages/create/', (route) => {
    const body = route.request().postDataJSON();
    creates.push(body);
    if (creates.length === 1)
      return route.fulfill({
        status: 409,
        json: {
          code: 'DUPLICATE_OPEN_REPAIR',
          detail: 'Open bearing work needs review.',
          duplicates: [
            {
              reason: 'OPEN_REPAIR_ON_MACHINE',
              status: 'in_progress',
              work_order_id: 3826,
              work_order_reference: 'WO-003826',
              repair_packet_id: null
            }
          ]
        }
      });
    return route.fulfill({
      json: {
        work_order_id: 73,
        work_order_reference: 'WO-73',
        repair_packet_id: 28,
        repair_packet_reference: 'RP-28',
        replayed: false,
        warnings: [],
        machine_id: body.machine_id,
        anomaly_id: body.source.anomaly_id,
        assigned_to_id: body.planning.assigned_to_id,
        idempotency_key: body.idempotency_key,
        lifecycle_version: 2
      }
    });
  });
  await openHealth(page, { scheme });
  await page
    .getByRole('button', { name: 'Trigger test alarm', exact: true })
    .click();
  let dialog = page.getByRole('dialog', {
    name: 'Trigger test alarm',
    exact: true
  });
  await waitForRenderedScope(page, dialog);
  await dialog
    .getByRole('switch', {
      name: 'Create work order automatically',
      exact: true
    })
    .uncheck();
  const signal = dialog.getByRole('combobox', {
    name: 'Configured test signal',
    exact: true
  });
  await signal.fill('Motor Winding Temperature 6');
  await page
    .getByRole('option', { name: /Motor Winding Temperature 6/ })
    .click();
  await expect(signal).toHaveValue('Motor Winding Temperature 6');
  await dialog.getByRole('checkbox').check();
  await dialog
    .getByRole('button', { name: 'Trigger and notify', exact: true })
    .click();
  await dialog
    .getByRole('button', { name: 'Create work for this test', exact: true })
    .click();
  dialog = page.getByRole('dialog', { name: 'New work order', exact: true });
  await expect(dialog).toBeVisible();
  if (options.sourceError)
    await expect(dialog).toContainText('Source context unavailable');
  else
    await expect(dialog.getByTestId('work-intake-panel')).toContainText(
      'Source event #501'
    );
  return { dialog, creates, drafts };
}

for (const scheme of ['light', 'dark'] as const) {
  test(`prepared intake creates the actual native work after typed ownership and duplicate review (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await openIntake(page, scheme);
    const dialog = fixture.dialog;
    await expect(dialog).toContainText('AI drafting unavailable');
    await dialog.screenshot({
      path: info.outputPath('intake-source.png'),
      animations: 'disabled'
    });
    await dialog
      .getByRole('button', {
        name: 'Use prepared Pump 08 specification',
        exact: true
      })
      .click();
    await expect(
      dialog.getByRole('textbox', { name: 'Title', exact: true })
    ).toHaveValue(fields.title);
    await expect(
      dialog.getByRole('combobox', { name: 'Assigned staff', exact: true })
    ).toHaveValue(/Avery Patel/);
    await expect(
      dialog.getByRole('button', { name: 'Create work order', exact: true })
    ).toBeDisabled();
    await dialog
      .getByRole('checkbox', {
        name: 'I reviewed the scope, source, owner and exercise limitations.',
        exact: true
      })
      .check();
    await dialog
      .getByRole('button', { name: 'Create work order', exact: true })
      .click();
    await expect(dialog).toContainText('WO-003826');
    await expect(dialog).toContainText('Open repair on this machine');
    await expect(dialog).toContainText('in progress');
    await expect(dialog).not.toContainText('OPEN_REPAIR_ON_MACHINE');
    await expect(dialog).not.toContainText('in_progress');
    const duplicateAlert = dialog
      .getByRole('alert')
      .filter({ hasText: 'Open repair on this machine' });
    expect(await duplicateAlert.textContent()).not.toMatch(/[A-Z]+_[A-Z_]+/);
    await expect(
      dialog.getByRole('button', {
        name: 'Create a second work order',
        exact: true
      })
    ).toBeDisabled();
    await dialog
      .getByLabel('Reason for a second repair', { exact: true })
      .fill(
        'Separate motor-winding thermal investigation. Bearing-vibration WO-003826 remains unchanged and does not own this event.'
      );
    await dialog
      .getByLabel('Reason for a second repair', { exact: true })
      .scrollIntoViewIfNeeded();
    await dialog.screenshot({
      path: info.outputPath('intake-reviewed-duplicate.png'),
      animations: 'disabled'
    });
    const duplicateAction = dialog.getByRole('button', {
      name: 'Create a second work order',
      exact: true
    });
    await expect(duplicateAction).toBeEnabled();
    await duplicateAction.scrollIntoViewIfNeeded();
    const normal = await assertReadableContrast(duplicateAction);
    await duplicateAction.hover();
    const hover = await assertReadableContrast(duplicateAction);
    await duplicateAction.focus();
    const focus = await assertReadableContrast(duplicateAction);
    const contrastPath = info.outputPath(
      `duplicate-action-contrast-${scheme}.json`
    );
    writeFileSync(
      contrastPath,
      JSON.stringify({ normal, hover, focus }, null, 2)
    );
    await info.attach('duplicate-action-contrast', {
      path: contrastPath,
      contentType: 'application/json'
    });
    await auditAccessibility(page, dialog, info, `duplicate-create-${scheme}`);
    await duplicateAction.screenshot({
      path: info.outputPath(`duplicate-create-action-${scheme}.png`),
      animations: 'disabled'
    });
    await duplicateAction.click();
    await expect(page.getByTestId('destination')).toHaveText(
      '/maintenance/work-orders/73/'
    );
    expect(fixture.creates).toHaveLength(2);
    expect(fixture.drafts).toHaveLength(0);
    expect(fixture.creates[1]).toMatchObject({
      title: fields.title,
      work_order_type: 'corrective',
      priority: 'high',
      parts: [],
      create_repair_packet: true,
      source: { anomaly_id: 501 },
      planning: { assigned_to_id: 27, due_date: null, estimated_minutes: null },
      duplicate_override: true,
      duplicate_override_reason:
        'Separate motor-winding thermal investigation. Bearing-vibration WO-003826 remains unchanged and does not own this event.'
    });
    await expectNoPageOverflow(page);
  });

  test(`configured-provider fixture autofills only a reviewed form (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await openIntake(page, scheme, true);
    const dialog = fixture.dialog;
    await expect(dialog).toContainText(
      'AI-assisted draft — review before creating'
    );
    await expect(dialog).toContainText('fixture-provider');
    const provenanceContrast = await assertReadableContrast(
      dialog.getByText('TEST — not a plant measurement', { exact: true })
    );
    const qualityContrast = await assertReadableContrast(
      dialog.getByText('Quality: good', { exact: true })
    );
    await info.attach('source-provenance-contrast', {
      body: JSON.stringify({ provenanceContrast, qualityContrast }),
      contentType: 'application/json'
    });
    await expect(
      dialog.getByRole('textbox', { name: 'Title', exact: true })
    ).toHaveValue(fields.title);
    expect(fixture.drafts).toHaveLength(1);
    expect(fixture.creates).toHaveLength(0);
    await expect(
      dialog.getByRole('button', { name: 'Create work order', exact: true })
    ).toBeDisabled();
    await dialog.screenshot({
      path: info.outputPath('intake-ai-contract.png'),
      animations: 'disabled'
    });
  });
}

test('native-bound pass with mismatched prepared evidence remains unapproved with a visible reason', async ({
  page
}) => {
  await openExercise(page, 'light');
  await fillCheck(page, 1, 'true');
  await fillCheck(page, 2, '100');
  await fillCheck(page, 3, 'true');
  const panel = page.getByTestId('exercise-verification-panel');
  await expect(panel).toContainText('2 of 3 checks accepted');
  await expect(panel).toContainText('RETURN VERIFICATION NOT APPROVED');
  await expect(page.getByTestId('exercise-check-2')).toContainText(
    'The recorded result does not match the linked prepared exercise evidence.'
  );
  await expect(page.getByTestId('exercise-check-2')).not.toContainText(
    'value_does_not_match_prepared_evidence'
  );
});

function exerciseState() {
  const titles = [
    'Cooler restored',
    'Winding temperatures below warning',
    'Trial stable; thermal check clear'
  ];
  return {
    applicable: true,
    work_order_id: 73,
    profile: 'P08-MOTOR-COOL-RTS-EX',
    approved: false,
    passed_count: 0,
    required_count: 3,
    can_record: true,
    can_apply: true,
    source: { anomaly_id: 501, status: 'open' },
    application_id: 42,
    published_revision_id: 43,
    evidence: [
      { id: 101, label: 'P08-TEMP-E03', prepared: true },
      { id: 102, label: 'P08-TEMP-E05', prepared: true },
      { id: 103, label: 'P08-TEMP-E06', prepared: true }
    ],
    checks: titles.map((title, index) => ({
      sequence: index + 1,
      title,
      value_type: index === 1 ? 'number' : 'boolean',
      unit: index === 1 ? '°C' : '',
      passed: false,
      recorded: false,
      value: null as string | boolean | null,
      step_key: `00000000-0000-4000-8000-00000000000${index + 1}`,
      version: 1,
      recorded_by: null as string | null,
      recorded_at: null as string | null,
      evidence_ids: [] as number[],
      suggested_evidence_ids: index === 0 ? [101, 102] : [103],
      prepared_note:
        index === 1
          ? 'Prepared W5=94°C, W6=96°C; maximum96°C. Unchanged historian, not a physical cooldown.'
          : 'Prepared exercise record only; no physical work or operating release.',
      note: '',
      reasons: [] as string[]
    })),
    reasons: [] as string[]
  };
}

async function openExercise(
  page: Page,
  scheme: 'light' | 'dark',
  options: { advanceAfterStep?: number; resultReasons?: string[] } = {}
) {
  const state = exerciseState();
  state.reasons = options.resultReasons ?? [];
  const overview = {
    ...workOrder(),
    title: fields.title,
    machine_name: 'Cedar Creek / Pump 08',
    verification_return: {
      rule: 'verification_return.v1' as const,
      procedures_state: 'read' as const,
      exercise_verification_available: true,
      tests: [],
      attempt_history: 'events_and_notes_previous_values_not_retained' as const,
      lockouts: [],
      technical_acceptance: {
        available: true,
        recorded: false,
        current_records: [],
        reason: 'not_recorded',
        records: []
      },
      controlled_test_authority: {
        available: false as const,
        reason: 'no_provider' as const
      },
      operating_permission: {
        available: true,
        recorded: false,
        current_records: [],
        reason: 'not_recorded',
        records: []
      },
      observed_return: {
        available: true,
        recorded: false,
        current_records: [],
        reason: 'not_recorded',
        records: []
      },
      service_state: {
        available: true,
        recorded: false,
        current_records: [],
        reason: 'not_recorded',
        records: []
      }
    }
  };
  await mockExecutionWork(page, overview);
  const posts: Record<string, any>[] = [];
  await page.route(
    '**/api/tasks/work-orders/73/exercise-verification/',
    (route) => route.fulfill({ json: state })
  );
  await page.route('**/api/tasks/work-orders/73/steps/*/complete/', (route) => {
    const body = route.request().postDataJSON();
    posts.push(body);
    const check = state.checks.find((row) =>
      route.request().url().includes(row.step_key)
    )!;
    expect(body.expected_version).toBe(check.version);
    check.version += 1;
    check.value = body.value.number ?? body.value.boolean;
    check.recorded = true;
    check.passed =
      check.sequence === 2 ? Number(check.value) === 96 : check.value === true;
    const nativePassed =
      check.sequence === 2
        ? Number(check.value) <= 124.9
        : check.value === true;
    check.reasons = [
      ...(!nativePassed ? ['current_pass_not_recorded'] : []),
      ...(!check.passed ? ['value_does_not_match_prepared_evidence'] : [])
    ];
    check.note = body.note;
    check.evidence_ids = body.evidence_ids;
    check.recorded_by = 'Avery Patel';
    check.recorded_at = '2026-10-25T08:00:00Z';
    state.passed_count = state.checks.filter((row) => row.passed).length;
    state.approved = state.passed_count === 3;
    const completedVersion = check.version;
    if (options.advanceAfterStep === check.sequence && completedVersion === 2) {
      check.version += 1;
      check.value = '100';
      check.passed = false;
      check.reasons = ['value_does_not_match_prepared_evidence'];
      check.recorded_by = 'Another recorder';
      check.note = 'Concurrent prepared result; review before replacing.';
      state.passed_count = state.checks.filter((row) => row.passed).length;
      state.approved = state.passed_count === 3;
    }
    return route.fulfill({
      json: {
        step_key: check.step_key,
        application: 42,
        version: completedVersion,
        value: body.value
      }
    });
  });
  await openExecutionPanel(page, 'verify', scheme, false, 'supervisor');
  await expect(page.getByTestId('exercise-verification-panel')).toBeVisible();
  return { state, posts };
}

async function fillCheck(
  page: Page,
  sequence: number,
  value: string,
  recorder = 'Avery Patel'
) {
  const row = page.getByTestId(`exercise-check-${sequence}`);
  await row
    .getByRole('button', {
      name: 'Use linked exercise evidence and note',
      exact: true
    })
    .click();
  if (sequence === 2)
    await row
      .getByLabel('Maximum winding temperature (°C)', { exact: true })
      .fill(value);
  else {
    await row.getByRole('combobox').first().click();
    await page
      .getByRole('option', {
        name: value === 'true' ? 'Yes' : 'No',
        exact: true
      })
      .click();
  }
  if (sequence === 3) await row.getByRole('checkbox').check();
  await row
    .getByRole('button', { name: /Record exercise (?:result|retest)/ })
    .click();
  await expect(row).toContainText(recorder);
}

test('a writer advancing beyond our receipt can be loaded and retested without a lockout', async ({
  page
}, info) => {
  const fixture = await openExercise(page, 'light', { advanceAfterStep: 2 });
  await fillCheck(page, 2, '96', 'Another recorder');
  const row = page.getByTestId('exercise-check-2');
  await expect(row).toContainText('This result changed.');
  await row
    .getByRole('button', { name: 'Load current recorded result', exact: true })
    .click();
  await expect(
    row.getByLabel('Maximum winding temperature (°C)', { exact: true })
  ).toHaveValue('100');
  await expect(
    row.getByRole('button', { name: 'Record exercise retest', exact: true })
  ).toBeEnabled();
  await captureAuthorityRegion(
    page,
    row,
    info.outputPath('exercise-concurrent-result-loaded.png')
  );
  await fillCheck(page, 2, '96');
  expect(fixture.posts.map((body) => body.expected_version)).toEqual([1, 3]);
  await expect(row).not.toContainText('This result changed.');
  await expect(
    row.getByRole('button', { name: 'Record exercise retest', exact: true })
  ).toBeEnabled();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`governance blockers use actionable native-contract text (${scheme})`, async ({
    page
  }, info) => {
    const reasons = [
      'published_procedure_not_applied',
      'procedure_revision_or_snapshot_invalid',
      'source_or_ownership_invalid',
      'invented_code'
    ];
    await openExercise(page, scheme, { resultReasons: reasons });
    const panel = page.getByTestId('exercise-verification-panel');
    const blockers = panel.getByTestId('exercise-governance-blockers');
    await expect(blockers).toContainText(
      'Apply the current independently published exercise procedure'
    );
    await expect(blockers).toContainText(
      'The applied procedure is outdated or its snapshot cannot be verified.'
    );
    await expect(blockers).toContainText(
      'The linked source event or work ownership cannot be verified.'
    );
    await expect(blockers).toContainText('Verification remains blocked.');
    for (const reason of reasons)
      await expect(blockers).not.toContainText(reason);
    const contrast = await assertReadableContrast(
      blockers.getByText('Verification remains blocked.', { exact: false })
    );
    const helperContrast = await assertReadableContrast(
      page
        .getByTestId('exercise-check-2')
        .getByText('Maximum of Winding 5 and 6.', { exact: false })
    );
    await info.attach('governance-copy-contrast', {
      body: JSON.stringify({ contrast, helperContrast }),
      contentType: 'application/json'
    });
    await expect(panel).toContainText('RETURN VERIFICATION NOT APPROVED');
    await captureAuthorityRegion(
      page,
      blockers,
      info.outputPath(`exercise-governance-${scheme}.png`)
    );
  });

  test(`three exercise checks persist and never confer operating authority (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await openExercise(page, scheme);
    await fillCheck(page, 1, 'true');
    await fillCheck(page, 2, '129');
    await fillCheck(page, 3, 'true');
    const panel = page.getByTestId('exercise-verification-panel');
    await expect(panel).toContainText('RETURN VERIFICATION NOT APPROVED');
    await expect(panel).toContainText('2 of 3 checks accepted');
    await expect(page.getByTestId('exercise-check-2')).toContainText(
      'Not accepted'
    );
    await expect(panel).not.toContainText('This result changed.');
    const failedContrast = await assertReadableContrast(
      panel.getByText('RETURN VERIFICATION NOT APPROVED', { exact: true })
    );
    await info.attach('failed-banner-contrast', {
      body: JSON.stringify(failedContrast),
      contentType: 'application/json'
    });
    await captureAuthorityRegion(
      page,
      panel,
      info.outputPath('exercise-failed.png')
    );
    await fillCheck(page, 2, '96');
    await expect(panel).toContainText('RETURN VERIFICATION PASSED');
    await expect(panel).toContainText('3 of 3 checks accepted');
    await auditAccessibility(
      page,
      panel,
      info,
      `exercise-verification-${scheme}`
    );
    const passedContrast = await assertReadableContrast(
      panel.getByText('RETURN VERIFICATION PASSED', { exact: true })
    );
    await info.attach('passed-banner-contrast', {
      body: JSON.stringify(passedContrast),
      contentType: 'application/json'
    });
    await expect(panel).toContainText(
      'Physical operating release is not issued'
    );
    await captureAuthorityRegion(
      page,
      panel,
      info.outputPath('exercise-passed.png')
    );
    await page.reload();
    await expect(panel).toContainText('RETURN VERIFICATION PASSED');
    await expect(panel).toContainText('Avery Patel');
    expect(fixture.posts).toHaveLength(4);
    await expectNoPageOverflow(page);
  });
}

test('an uncertain creation retries the retained exact payload', async ({
  page
}) => {
  const fixture = await openIntake(page, 'light');
  await fixture.dialog
    .getByRole('button', {
      name: 'Use prepared Pump 08 specification',
      exact: true
    })
    .click();
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I reviewed the scope, source, owner and exercise limitations.',
      exact: true
    })
    .check();
  const bodies: Record<string, unknown>[] = [];
  await page.route('**/api/maintenance/work-packages/create/', (route) => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({
      status: 503,
      json: { detail: 'Fixture lost acknowledgement' }
    });
  });
  await fixture.dialog
    .getByRole('button', { name: 'Create work order', exact: true })
    .click();
  await fixture.dialog
    .getByRole('button', { name: 'Retry same creation', exact: true })
    .click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toEqual(bodies[0]);
  expect(fixture.creates).toHaveLength(0);
});

test('an already-owned source cannot create another owner', async ({
  page
}) => {
  const fixture = await openIntake(page, 'light', false, { owned: true });
  await expect(
    fixture.dialog.getByRole('link', {
      name: 'Open owning work order',
      exact: true
    })
  ).toHaveAttribute('href', /3826\/$/);
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Create work order',
      exact: true
    })
  ).toBeDisabled();
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Use prepared Pump 08 specification',
      exact: true
    })
  ).toHaveCount(0);
  expect(fixture.creates).toHaveLength(0);
});

test('source permission failure disables linked creation without hiding retry', async ({
  page
}) => {
  const fixture = await openIntake(page, 'light', false, { sourceError: true });
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Retry source check',
      exact: true
    })
  ).toBeEnabled();
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Create work order',
      exact: true
    })
  ).toBeDisabled();
  expect(fixture.creates).toHaveLength(0);
});

test('a late AI response preserves human edits until explicitly applied', async ({
  page
}) => {
  let releaseDraft!: () => void;
  const draftGate = new Promise<void>((resolve) => {
    releaseDraft = resolve;
  });
  const fixture = await openIntake(page, 'light', true, { draftGate });
  const title = fixture.dialog.getByRole('textbox', {
    name: 'Title',
    exact: true
  });
  await title.fill('Human reviewed investigation title');
  releaseDraft();
  await expect(fixture.dialog).toContainText(
    'Draft ready — your edits are preserved'
  );
  await expect(title).toHaveValue('Human reviewed investigation title');
  expect(fixture.creates).toHaveLength(0);
  await fixture.dialog
    .getByRole('button', { name: 'Apply draft to form', exact: true })
    .click();
  await expect(title).toHaveValue(fields.title);
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Create work order',
      exact: true
    })
  ).toBeDisabled();
});

test('cancel and reopen rechecks source readiness without another automatic AI request', async ({
  page
}) => {
  const fixture = await openIntake(page, 'light', true);
  await expect(fixture.dialog).toContainText('AI-assisted draft');
  expect(fixture.drafts).toHaveLength(1);
  await fixture.dialog
    .getByRole('button', { name: 'Cancel', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Trigger test alarm', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Create work for this test', exact: true })
    .click();
  await expect(fixture.dialog.getByTestId('work-intake-panel')).toBeVisible();
  await fixture.dialog
    .getByRole('button', {
      name: 'Use prepared Pump 08 specification',
      exact: true
    })
    .click();
  await fixture.dialog
    .getByRole('checkbox', {
      name: 'I reviewed the scope, source, owner and exercise limitations.',
      exact: true
    })
    .check();
  await expect(
    fixture.dialog.getByRole('button', {
      name: 'Create work order',
      exact: true
    })
  ).toBeEnabled();
  expect(fixture.drafts).toHaveLength(1);
  expect(fixture.creates).toHaveLength(0);
});

for (const failure of [
  { status: 429, retryAfter: '60', message: 'rate limit reached' },
  {
    status: 503,
    code: 'AI_INTAKE_UNAVAILABLE',
    message: 'AI provider unavailable'
  },
  { status: 503, code: 'AI_INTAKE_TIMEOUT', message: 'AI drafting timed out' }
]) {
  test(`AI draft refusal preserves fields and explains ${failure.status}/${failure.code ?? 'rate limit'}`, async ({
    page
  }) => {
    const fixture = await openIntake(page, 'light', true, {
      draftFailure: failure
    });
    await expect(
      fixture.dialog.getByRole('alert').filter({ hasText: failure.message })
    ).toBeVisible();
    await expect(
      fixture.dialog.getByRole('textbox', { name: 'Title', exact: true })
    ).toHaveValue('Investigate test alarm');
    expect(fixture.creates).toHaveLength(0);
    if (failure.status === 429)
      await expect(
        fixture.dialog.getByRole('button', {
          name: 'Draft with AIMMS AI',
          exact: true
        })
      ).toBeDisabled();
  });
}

test('stale measured references are withheld while the synthetic value remains labelled TEST', async ({
  page
}) => {
  const fixture = await openIntake(page, 'light', false, { stale: true });
  const source = fixture.dialog.getByTestId('work-intake-panel');
  await expect(source).toContainText('TEST — not a plant measurement');
  await expect(source).toContainText('Test: 159.5 °C');
  await expect(source).toContainText('Measured: — °C');
  await expect(source).toContainText('stale reference');
});
