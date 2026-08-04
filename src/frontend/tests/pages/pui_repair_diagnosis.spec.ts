import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';

const GENERATED_AT = '2026-08-04T12:00:00Z';

function diagnosisBlob(overrides: Record<string, unknown> = {}) {
  return {
    likely_cause: 'Suspected fault related to: bearing noise',
    confidence: 0.3,
    confidence_label: 'low',
    alternatives: [],
    evidence: [],
    confirm_tests: [
      'Confirm the symptom is reproducible and capture readings.'
    ],
    failure_mode: null,
    status: 'available',
    authority: 'derived',
    authority_source: null,
    data_window: { start: null, end: null, snapshot_count: 0 },
    freshness: { stale: false, stale_signal_count: 0 },
    quality: { summary: 'unknown', bad_signal_count: 0 },
    provider: 'heuristic',
    model_or_rule_version: '',
    generated_at: GENERATED_AT,
    verified_by_user: false,
    verified_at: null,
    verified_by: null,
    amendments: [],
    generator: 'heuristic',
    schema_version: 2,
    ...overrides
  };
}

function packetPayload(diagnosis: Record<string, unknown>) {
  return {
    pk: 901,
    reference: 'RP-0901',
    status: 'diagnosed',
    status_label: 'Diagnosed',
    machine_name: 'Influent Pump 1',
    criticality: 'high',
    generation_status: 'succeeded',
    symptom: 'bearing noise',
    fault_summary: 'bearing noise',
    production_impact: '',
    diagnosis,
    findings: [],
    approved_scope: null,
    gates: [],
    parts: [],
    approvals: [],
    events: [],
    closeout: {},
    work_order: null
  };
}

async function mockPacket(
  page: Page,
  first: Record<string, unknown>,
  afterVerify?: Record<string, unknown>
) {
  let verified = false;
  await page.route(
    (url) => url.pathname === '/api/repair/packets/901/',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          packetPayload(verified && afterVerify ? afterVerify : first)
        )
      });
    }
  );
  await page.route(
    (url) => url.pathname === '/api/repair/packets/901/verify-diagnosis/',
    async (route) => {
      verified = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, detail: '', diagnosis: afterVerify })
      });
    }
  );
}

async function openDiagnosisTab(page: Page) {
  await page.getByRole('tab', { name: 'Diagnosis', exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Diagnosis', exact: true }).click();
}

test('heuristic packet is labelled as offline fallback, not analysis', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'repair/packets/901/' });
  await mockPacket(page, diagnosisBlob());
  await page.reload();
  await openDiagnosisTab(page);

  await expect(page.getByTestId('diagnosis-fallback-chip')).toBeVisible();
  await expect(
    page.getByText('Preliminary results', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText(/Preliminary — not technician verified/)
  ).toBeVisible();
  await expect(page.getByTestId('diagnosis-verify-button')).toBeVisible();
});

test('verifying the diagnosis flips the preliminary banner', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { url: 'repair/packets/901/' });
  const cited = diagnosisBlob({
    likely_cause: 'Probable drive-end bearing wear.',
    confidence: 0.8,
    confidence_label: 'high',
    generator: 'wf7',
    provider: 'azure_foundry_luna',
    evidence: [
      {
        snapshot_id: 'machine:44@r7',
        observation: 'Vibration doubled over two weeks on the drive end.',
        relation: 'supports',
        observed_at: GENERATED_AT
      }
    ]
  });
  const verifiedBlob = {
    ...cited,
    verified_by_user: true,
    verified_at: GENERATED_AT,
    verified_by: 'allaccess'
  };
  await mockPacket(page, cited, verifiedBlob);
  await page.reload();
  await openDiagnosisTab(page);

  await expect(
    page.getByText('Preliminary results', { exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('diagnosis-fallback-chip')).toHaveCount(0);
  await expect(
    page.getByText('Vibration doubled over two weeks on the drive end.')
  ).toBeVisible();

  await page.getByTestId('diagnosis-verify-button').click();

  await expect(
    page.getByText('Diagnosis', { exact: true }).last()
  ).toBeVisible();
  await expect(
    page.getByText(/Preliminary — not technician verified/)
  ).toHaveCount(0);
  await expect(page.getByTestId('diagnosis-verify-button')).toHaveCount(0);
});
