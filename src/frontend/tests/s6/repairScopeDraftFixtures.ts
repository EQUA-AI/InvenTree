import type { Page } from '@playwright/test';
import type { RepairScopeDraftResponse } from '../../lib/types/RepairScopeDraft';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { mockExecutionWork, workOrder } from '../s4/workExecutionFixtures';

export function scopeWork(): WorkOrderOverview {
  return {
    ...workOrder(),
    repair_packet: {
      id: 901,
      reference: 'RP-901',
      status: 'diagnosed',
      criticality: 'critical',
      fault_summary: 'Synthetic winding-temperature investigation',
      symptom: 'Synthetic winding-six high indication',
      production_impact: 'Not established',
      generation_status: 'succeeded',
      diagnosis: {},
      diagnosis_is_preliminary: true,
      diagnosis_status: 'requires_review',
      findings: [],
      approved_scope: null,
      gates: []
    }
  };
}

export function scopeResponse(): RepairScopeDraftResponse {
  return {
    work_order_id: 73,
    machine_id: 18,
    repair_packet_id: 901,
    lifecycle_version: 7,
    draft: {
      id: 804,
      revision: 2,
      work_order_id: 73,
      machine_id: 18,
      repair_packet_id: 901,
      source_event_id: 92,
      component_code: 'PS-MOTOR:1',
      actor_id: 9,
      saved_at: '2026-10-28T08:00:00Z',
      lifecycle_version: 7,
      status: 'proposed',
      gates: {
        supervisor_review: 'pending',
        site_safety_authorization: 'pending',
        physical_execution: 'not_started',
        post_repair_verification: 'not_started'
      },
      scope: {
        suspected_cause:
          'Synthetic prepared E03 suggests restricted cooler airflow; loading and sensor error remain alternatives.',
        steps: [
          'Review source event 92 and prepared E03 with the supervisor.',
          'After site safety authorization, verify isolation and inspect cooling passages.',
          'Clean only if fouling is confirmed and an approved procedure permits it.'
        ],
        tools_and_supplies: [
          'Approved inspection light and temperature instrument',
          'Procedure-approved cleaning materials, if required'
        ],
        required_ppe: ['Site-approved electrical and task-specific PPE'],
        isolation_checks: ['Authorized isolation and zero-energy verification'],
        post_work_checks: [
          'Record post-work winding readings under approved conditions'
        ],
        replacement_parts: [],
        parts_decision:
          'No replacement parts proposed. Raise a separate reviewed requirement only if inspection identifies damage.',
        open_questions: [
          'Confirm the related manual applies to the fitted motor.'
        ]
      },
      sources: {
        e03: {
          id: 503,
          title: 'Synthetic prepared cooler inspection',
          filename: 'P08-TEMP-E03.txt',
          sha256: '1'.repeat(64),
          label: 'P08-TEMP-E03',
          prepared: true
        },
        manuals: [
          {
            id: 504,
            title: 'Synthetic related motor reference',
            filename: 'related-motor-reference.pdf',
            sha256: '2'.repeat(64),
            applicability: 'related_reference_unverified'
          }
        ]
      }
    }
  };
}

export async function mockScopeDraft(
  page: Page,
  response:
    | RepairScopeDraftResponse
    | Record<string, unknown> = scopeResponse(),
  status = 200
) {
  const requests = await mockExecutionWork(page, scopeWork());
  const reads: string[] = [];
  await page.route(
    '**/api/tasks/work-orders/73/repair-scope-draft/',
    (route) => {
      reads.push(route.request().method());
      return route.fulfill({ status, json: response });
    }
  );
  return { requests, reads };
}
