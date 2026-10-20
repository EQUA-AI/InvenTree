import { readFile } from 'node:fs/promises';
import type { Page } from '@playwright/test';
import { continuityWork } from '../s4/continuityFixtures';

export const filename =
  'Synthetic retained nameplate and reference with a deliberately long file name.png';
export async function workAttachment(page: Page) {
  await page.route('**/api/attachment/**', (route) =>
    route.fulfill({
      json: {
        count: 1,
        next: null,
        results: [
          {
            pk: 901,
            filename,
            is_image: true,
            attachment: `/media/attachments/workorder/73/${filename}`,
            link: null,
            model_type: 'workorder',
            model_id: 73,
            comment: 'Synthetic reference; applicability not verified',
            thumbnail: null,
            upload_date: '2026-10-18T12:00:00Z',
            upload_user: 11,
            user_detail: { pk: 11, username: 'Synthetic uploader' },
            file_size: 1024,
            tags: []
          }
        ]
      }
    })
  );
  await page.route('**/api/aichat/evidence/media/901/metadata/**', (route) =>
    route.fulfill({
      json: {
        revision: 'a'.repeat(64),
        content_type: 'image/png',
        page_count: null,
        page_labels: []
      }
    })
  );
  await page.route('**/api/aichat/evidence/media/901/?*', async (route) =>
    route.fulfill({
      contentType: 'image/png',
      body: await readFile('tests/fixtures/playwright_custom_logo.png')
    })
  );
}

export function workScenario(canRecordDeviation = false) {
  const work = continuityWork(false);
  work.title =
    'Synthetic inspection of the transfer pump guard, retained measurement evidence and the maintenance account before the next planned service window';
  work.machine_name =
    'Synthetic North transfer pump · EQUIPMENT-REFERENCE-0123456789';
  work.investigation!.requests.items![0].required_artifacts[0].unit = 'mm/s';
  const step = {
    key: 'step-1',
    sequence: 1,
    step_type: 'verification',
    title: 'Synthetic applicable test',
    instruction:
      'Use the retained synthetic instruction; this fixture grants no physical permission.',
    required: true,
    required_permission: '',
    value_type: 'number',
    unit: 'mm/s',
    min_value: '0',
    max_value: '4',
    allowed_values: []
  };
  work.verification_return = {
    rule: 'verification_return.v1',
    procedures_state: 'read',
    attempt_history: 'events_and_notes_previous_values_not_retained',
    tests: [
      {
        id: 1,
        application_id: 7,
        procedure_revision: 3,
        version: 4,
        status: 'completed',
        value: { number: 0 },
        passed: true,
        note: 'Synthetic current result',
        disposition_reason: '',
        recorded_by: 'Synthetic technician',
        recorded_at: '2026-10-18T12:00:00Z',
        step,
        events: [
          {
            id: 91,
            event_type: 'STEP_FAILED',
            version: 2,
            recorded_by: 'Synthetic technician',
            recorded_at: '2026-10-18T11:00:00Z',
            reason: '',
            recorded_note: 'Synthetic retained failed attempt',
            evidence_ids: [],
            historical_value_available: false
          }
        ]
      }
    ],
    lockouts: [],
    technical_acceptance: { available: false, reason: 'no_provider' },
    controlled_test_authority: { available: false, reason: 'no_provider' },
    operating_permission: { available: false, reason: 'no_provider' },
    observed_return: { available: false, reason: 'no_provider' },
    service_state: { available: false, reason: 'no_provider' }
  };
  work.prepared_package!.procedures_state = 'read';
  work.prepared_package!.can_record_deviation = canRecordDeviation;
  work.prepared_package!.applications = [
    {
      id: 7,
      primary: true,
      procedure_id: 11,
      revision_id: 15,
      revision: 3,
      change_summary: 'Synthetic retained package',
      snapshot_hash: 'a'.repeat(64),
      drift_status: 'current',
      drift_basis: 'catalogue_at_read',
      current_catalogue_code: 'SYN-PROC',
      current_catalogue_name: 'Synthetic procedure',
      applied_by: 'Synthetic technician',
      applied_at: '2026-10-18T12:00:00Z',
      steps: [step],
      resources: []
    }
  ];
  return work;
}

export async function textZoom(page: Page, zoom?: boolean) {
  if (!zoom) return;
  await page.evaluate(() => {
    type Font = { node: HTMLElement; value: string; priority: string };
    const state = window as typeof window & { p31Fonts?: Font[] };
    // Restore the baseline before measuring newly mounted panels and overlays.
    for (const previous of state.p31Fonts ?? []) {
      if (previous.value)
        previous.node.style.setProperty(
          'font-size',
          previous.value,
          previous.priority
        );
      else previous.node.style.removeProperty('font-size');
    }
    const fonts = Array.from(document.querySelectorAll<HTMLElement>('*')).map(
      (node) => ({
        node,
        value: node.style.getPropertyValue('font-size'),
        priority: node.style.getPropertyPriority('font-size'),
        size: Number.parseFloat(getComputedStyle(node).fontSize)
      })
    );
    state.p31Fonts = fonts;
    for (const { node, size } of fonts)
      node.style.setProperty('font-size', `${size * 2}px`, 'important');
  });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
}
