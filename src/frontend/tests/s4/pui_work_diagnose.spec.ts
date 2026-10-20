/**
 * P20 / UI12: chat-primary Diagnose projection (screens 28, 29, 75, 76).
 *
 * Diagnose renders the saved investigation: qualified explanation, evidence
 * by relation, disputed findings, proposed checks (not requests), corrections
 * and the intervention basis. Its only action is Continue investigation in the
 * existing assistant: no form, wizard, confidence score or round counter.
 */
import { expect, test } from '@playwright/test';

import type { Investigation } from '../../lib/types/WorkExecution';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';

import { nativeBaseline } from '../s0/guards';
import {
  WORK_LABEL,
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

const finding = {
  id: 101,
  finding_key: 'SYN-01',
  category: 'measurement',
  observation: 'Retained zero envelope reading',
  value: 0,
  unit: 'g',
  evidence_source: 'Synthetic immutable snapshot',
  snapshot_id: '12345678-synthetic',
  observed_at: '2026-10-18T11:00:00Z',
  verification: 'disputed'
};

function investigation(extra: Partial<Investigation> = {}): Investigation {
  return {
    rule: 'investigation.v1',
    packet: { id: 901, reference: 'RP-901', generation_status: 'succeeded' },
    explanation: {
      likely_cause: 'Synthetic drive-end bearing wear',
      failure_mode: 'bearing_wear',
      preliminary: true,
      verified_by: null,
      verified_at: null,
      analysis_status: 'available',
      authority: 'derived',
      provider: 'machine_health.preliminary',
      model_or_rule_version: 'wf7-2026.10',
      generated_at: '2026-10-18T12:05:00Z',
      data_window: {
        start: '2026-10-18T06:00:00Z',
        end: '2026-10-18T12:00:00Z',
        snapshot_count: 12
      },
      stale_signal_count: 1,
      quality_summary: 'good',
      evidence: [
        {
          observation: 'Synthetic DE vibration rose',
          relation: 'supports',
          snapshot_id: '6f1c2d4e-0000-4000-8000-000000000001',
          signal_label: 'DE vibration',
          value: 7.1,
          unit: 'mm/s',
          observed_at: '2026-10-18T11:30:00Z'
        },
        {
          observation: 'Synthetic bearing temperature unchanged',
          relation: 'contradicts',
          snapshot_id: null,
          value: 0,
          unit: 'degC'
        },
        {
          observation: 'Synthetic operator heard noise',
          relation: 'unknown',
          snapshot_id: null
        }
      ],
      alternatives: ['Synthetic misalignment'],
      proposed_checks: [
        'Measure DE envelope acceleration',
        'Inspect the coupling guard area'
      ],
      amendments: [
        {
          text: 'Synthetic noise traced to the coupling guard',
          author: 'synthetic-tech',
          recorded_at: '2026-10-18T13:00:00Z',
          reason: null
        }
      ]
    },
    findings: [
      {
        ...finding,
        recorded_by: 'Synthetic Reed',
        created_at: '2026-10-18T11:00:00Z',
        updated_at: '2026-10-18T13:10:00Z'
      }
    ],
    finding_history: 'overwritten_in_place',
    intervention: {
      scope_id: 201,
      version: 3,
      verified_cause: 'Synthetic approved bearing basis',
      failure_codes: ['SYN-CODE'],
      scope_line_count: 2,
      approved_by: 'Synthetic planner',
      approved_at: '2026-10-18T11:30:00Z',
      superseded_versions: 2
    },
    requests: { available: false, reason: 'no_provider' },
    ...extra
  };
}

function packetWork(extra: Partial<WorkOrderOverview> = {}): WorkOrderOverview {
  return {
    ...workOrder(),
    repair_packet: {
      id: 901,
      reference: 'RP-901',
      status: 'executing',
      criticality: 'high',
      fault_summary: 'Synthetic fault',
      symptom: 'Synthetic noise',
      production_impact: '',
      generation_status: 'succeeded',
      diagnosis: {
        likely_cause: 'Synthetic drive-end bearing wear',
        confidence: 0.87
      },
      diagnosis_is_preliminary: true,
      diagnosis_status: 'available',
      findings: [finding],
      approved_scope: null,
      gates: []
    },
    investigation: investigation(),
    ...extra
  };
}

test('Diagnose projects the saved investigation with its qualification and gaps', async ({
  page
}, testInfo) => {
  const requests = await mockExecutionWork(page, packetWork());
  await openExecutionPanel(page, 'diagnose');
  const diagnose = panelRegion(page, 'Diagnose');
  const explanation = diagnose.getByTestId('diagnose-explanation');
  await expect(
    explanation.getByText('Preliminary — not technician verified')
  ).toBeVisible();
  await expect(
    explanation.getByText('Synthetic drive-end bearing wear', { exact: true })
  ).toBeVisible();
  await expect(
    explanation.getByText(/The initiating cause is not established/)
  ).toBeVisible();
  await expect(
    explanation.getByText('12 snapshot(s)', { exact: false })
  ).toBeVisible();
  await expect(
    explanation.getByText('A threshold configured in AIMMS', { exact: true })
  ).toBeVisible();
  // Proposed checks are not dressed up as saved requests.
  const next = diagnose.getByTestId('diagnose-next-check');
  await expect(next.getByText('No saved investigation request')).toBeVisible();
  await expect(
    next.getByText('Measure DE envelope acceleration')
  ).toBeVisible();
  await expect(
    next.getByText(/not assigned, scheduled or confirmed/)
  ).toBeVisible();
  // Evidence by recorded relation; contradiction and dispute stay visible.
  const evidence = diagnose.getByTestId('diagnose-evidence');
  await expect(
    evidence
      .getByTestId('evidence-supports')
      .getByText('Synthetic DE vibration rose')
  ).toBeVisible();
  await expect(
    evidence.getByTestId('evidence-supports').getByText(/7\.1 mm\/s/)
  ).toBeVisible();
  await expect(
    evidence
      .getByTestId('evidence-contradicts')
      .getByText('Synthetic bearing temperature unchanged')
  ).toBeVisible();
  await expect(
    evidence.getByTestId('evidence-contradicts').getByText(/0 degC/)
  ).toBeVisible();
  await expect(
    evidence.getByText('1 disputed finding(s), unresolved')
  ).toBeVisible();
  await expect(
    diagnose
      .getByTestId('diagnose-corrections')
      .getByText(/earlier value and verification are not kept/)
  ).toBeVisible();
  const basis = diagnose.getByTestId('diagnose-intervention');
  await expect(basis.getByText('Approved scope version 3')).toBeVisible();
  await expect(basis.getByText('2 earlier version(s) retained')).toBeVisible();
  // The native findings table is retained underneath.
  await expect(diagnose.getByText('0 g', { exact: true })).toBeVisible();
  // No answer form, wizard, score or round cap.
  await expect(diagnose.locator('input, textarea, select, form')).toHaveCount(
    0
  );
  await expect(diagnose.getByText(/%|round \d|of \d rounds/i)).toHaveCount(0);
  await expect(
    panelRegion(page, 'Diagnose').getByRole('button', {
      name: 'Continue investigation'
    })
  ).toHaveCount(1);
  await expectNoPageOverflow(page);
  await capturePanel(page, testInfo.outputPath('diagnose-preliminary.png'));
  await basis.getByRole('link', { name: 'Open Plan & Safety' }).click();
  await expect(
    page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .getByRole('tab', { name: 'Plan & Safety', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  expect(onlyReads(requests)).toBe(true);
});

test('Continue investigation opens the existing assistant on this Work', async ({
  page
}) => {
  const requests = await mockExecutionWork(page, packetWork());
  await mockAssistantOpen(page);
  await openExecutionPanel(page, 'diagnose');
  await panelRegion(page, 'Diagnose')
    .getByRole('button', { name: 'Continue investigation' })
    .click();
  await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
    WORK_LABEL
  );
  // Opening the assistant acknowledges or sends nothing by itself.
  expect(
    requests.filter((request) => request.path.includes('/work-focus'))
  ).toHaveLength(0);
  expect(onlyReads(requests)).toBe(true);
});

test('a verified explanation names its verifier and the Work panel shows no score', async ({
  page
}, testInfo) => {
  const base = investigation();
  await mockExecutionWork(
    page,
    packetWork({
      investigation: {
        ...base,
        explanation: {
          ...base.explanation!,
          preliminary: false,
          verified_by: 'synthetic-tech',
          verified_at: '2026-10-18T14:00:00Z',
          evidence: [],
          proposed_checks: [],
          amendments: []
        },
        intervention: null
      }
    })
  );
  await openExecutionPanel(page, 'diagnose', 'dark');
  const diagnose = panelRegion(page, 'Diagnose');
  await expect(diagnose.getByText('Verified by synthetic-tech')).toBeVisible();
  await expect(
    diagnose.getByText('No contradicting evidence is cited.')
  ).toBeVisible();
  await expect(
    diagnose.getByText('The diagnosis proposes no checks.')
  ).toBeVisible();
  await expect(
    diagnose.getByText(/No approved intervention scope is recorded/)
  ).toBeVisible();
  await expectNoPageOverflow(page);
  await capturePanel(page, testInfo.outputPath('diagnose-verified-dark.png'));
  await page
    .getByRole('tablist', { name: 'Work workflow', exact: true })
    .getByRole('tab', { name: 'Work', exact: true })
    .click();
  await expect(
    panelRegion(page, 'Work').getByText('Problem and impact')
  ).toBeVisible();
  await expect(panelRegion(page, 'Work').getByText(/Confidence|%/)).toHaveCount(
    0
  );
});

test('standalone work says no investigation is saved and keeps chat primary', async ({
  page
}, testInfo) => {
  await mockExecutionWork(page, {
    ...workOrder(),
    investigation: {
      rule: 'investigation.v1',
      packet: null,
      explanation: null,
      findings: [],
      finding_history: 'not_applicable',
      intervention: null,
      requests: { available: false, reason: 'no_provider' }
    }
  });
  await openExecutionPanel(page, 'diagnose');
  const diagnose = panelRegion(page, 'Diagnose');
  await expect(
    diagnose.getByText(
      /no repair packet, so no diagnosis, cited evidence or findings/
    )
  ).toBeVisible();
  await expect(
    diagnose.getByText('No saved investigation request')
  ).toBeVisible();
  await expect(
    diagnose.getByRole('button', { name: 'Continue investigation' })
  ).toBeEnabled();
  await capturePanel(page, testInfo.outputPath('diagnose-standalone.png'));
});

test('a server without the projection keeps the native findings view', async ({
  page
}) => {
  const overview = packetWork();
  delete overview.investigation;
  await mockExecutionWork(page, overview);
  await openExecutionPanel(page, 'diagnose');
  const diagnose = panelRegion(page, 'Diagnose');
  await expect(diagnose.getByText('Investigation findings')).toBeVisible();
  await expect(diagnose.getByText('0 g', { exact: true })).toBeVisible();
  await expect(diagnose.getByTestId('diagnose-explanation')).toHaveCount(0);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`missing wf7 basis stays unknown and source citations stay complete (${scheme})`, async ({
    page
  }, info) => {
    const base = investigation();
    const missing = {
      ...base.explanation!,
      authority: null,
      provider: 'azure_foundry_luna',
      data_window: { start: null, end: null, snapshot_count: null },
      stale_signal_count: null,
      quality_summary: null,
      evidence: [
        {
          observation: 'Synthetic prior repair cited',
          relation: 'supports' as const,
          snapshot_id: 'work_order_closeout:41@7'
        }
      ]
    };
    await mockExecutionWork(
      page,
      packetWork({ investigation: investigation({ explanation: missing }) })
    );
    await openExecutionPanel(page, 'diagnose', scheme);
    const diagnose = panelRegion(page, 'Diagnose');
    const basis = diagnose.getByTestId('diagnose-explanation');
    await expect(basis.getByText('Not recorded', { exact: true })).toHaveCount(
      4
    );
    await expect(
      diagnose.getByText('Cited record work_order_closeout:41@7', {
        exact: true
      })
    ).toBeVisible();
    await expect(
      diagnose.getByText(
        /Snapshot work_ord|0 snapshot\(s\)|A threshold configured in AIMMS/
      )
    ).toHaveCount(0);
    await expect(
      diagnose.getByText(
        /Chat does not save finding corrections or resolutions/
      )
    ).toBeVisible();
    await expect(
      diagnose.getByText(
        /Resolve it in the assistant|Checks are agreed in the assistant/
      )
    ).toHaveCount(0);
    await expect(
      diagnose.getByRole('link', {
        name: 'Open RP-901 to record supported changes'
      })
    ).toHaveAttribute('href', '/repair/packets/901/');
    await expectNoPageOverflow(page);
    await capturePanel(
      page,
      info.outputPath(`diagnose-missing-basis-${scheme}.png`)
    );
    const ungenerated = packetWork();
    ungenerated.repair_packet!.diagnosis = {};
    ungenerated.repair_packet!.diagnosis_status = null;
    ungenerated.repair_packet!.generation_status = 'idle';
    ungenerated.investigation = investigation({
      packet: { id: 901, reference: 'RP-901', generation_status: 'idle' },
      explanation: null
    });
    await mockExecutionWork(page, ungenerated);
    await page.reload();
    const empty = page.getByTestId('diagnose-explanation');
    await expect(
      empty.getByText('No explanation saved', { exact: true })
    ).toBeVisible();
    await expect(
      empty.getByText(
        /No data available|0 snapshot\(s\)|A threshold configured in AIMMS/
      )
    ).toHaveCount(0);
    await expect(
      empty.getByText('Evidence window', { exact: true })
    ).toHaveCount(0);
    await capturePanel(
      page,
      info.outputPath(`diagnose-not-generated-${scheme}.png`)
    );
  });
}
