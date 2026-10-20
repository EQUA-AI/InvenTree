import type { WorkContinuity } from '../../lib/types/WorkExecution';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { revisionWork } from './scopeRevisionFixtures';

export const recordedPoint = {
  id: 88,
  device: 'Synthetic feeder point',
  energy_source: 'electrical',
  status: 'restored',
  applied_by: 'Synthetic first technician',
  verified_by: 'Synthetic verifier',
  verified_at: '2026-10-19T09:00:00Z',
  restored_at: '2026-10-20T07:00:00Z',
  note: 'Synthetic saved point record; job-wide authority not recorded'
};

export function continuityWork(
  held = true,
  executor = false
): WorkOrderOverview {
  const work = revisionWork();
  work.repair_packet = null;
  work.investigation!.packet = null;
  work.investigation!.intervention = null;
  work.investigation!.findings = [];
  work.investigation!.finding_history = 'not_applicable';
  work.investigation!.requests.items![0].source_finding = null;
  work.investigation!.requests.items!.push({
    ...work.investigation!.requests.items![0],
    id: 402,
    version: 1,
    purpose: 'Synthetic independent electrical review',
    next_check: 'Synthetic drawing review',
    priority: null,
    status: 'open',
    owner: 12,
    owner_name: 'Synthetic second technician',
    next_checkpoint: null,
    blocked_reason: '',
    required_artifacts: [
      {
        kind: 'observation',
        description: 'Synthetic drawing reference',
        conditions: 'Review the saved approved reference'
      }
    ],
    artifacts: []
  });
  work.prepared_package!.scopes = [];
  work.prepared_package!.revision_review = {
    ...work.prepared_package!.revision_review!,
    state: 'no_scope',
    current_scope_id: null,
    comparisons: []
  };
  for (const revision of work.prepared_package!.revision_review
    .request_revisions) {
    revision.before.source_finding = null;
    revision.after.source_finding = null;
  }
  work.lifecycle_status = held ? 'on_hold' : 'in_progress';
  work.canonical_commands_enabled = true;
  work.assigned_to = 17;
  work.assigned_to_name = 'Synthetic outgoing technician';
  work.assigned_to_username = 'synthetic-outgoing';
  work.work_continuity = continuity(held, executor);
  work.actual_started_at = '2026-10-19T06:00:00Z';
  work.updated_at = '2026-10-20T08:00:00Z';
  work.events = work.work_continuity.history.map((event) => ({
    id: event.event_id,
    actor: 9,
    event_type: event.action.toUpperCase(),
    from_status: event.from_status,
    to_status: event.to_status,
    reason: event.reason || '',
    created_at: event.recorded_at,
    metadata: { lifecycle_version: event.lifecycle_version }
  }));
  work.parts = [
    {
      id: 31,
      part: 44,
      part_name: 'Synthetic gasket requirement · long retained source label',
      part_ipn: 'SYN-GASKET',
      part_thumbnail: null,
      quantity: 2,
      allocated_quantity: 0,
      allocation_status: 'none',
      allocation_note: '',
      created_at: '2026-10-19T09:00:00Z',
      updated_at: '2026-10-19T09:00:00Z'
    }
  ];
  work.dependencies = [
    {
      id: 801,
      direction: 'successor',
      dependency_type: 'FS',
      lag_minutes: 0,
      card: {
        id: 74,
        reference: 'WO-74',
        title: 'Synthetic sibling electrical check',
        description: '',
        status: 'in-progress',
        priority: 'high',
        lifecycle_status: 'in_progress',
        lifecycle_version: 4,
        work_order_type: 'inspection',
        machine: 18,
        machine_name: 'Synthetic pump',
        assigned_to: 13,
        assigned_to_name: 'Synthetic sibling owner',
        scheduled_start: null,
        scheduled_end: null,
        estimated_minutes: null,
        is_active: true
      }
    }
  ];
  work.verification_return = {
    rule: 'verification_return.v1',
    procedures_state: 'read',
    tests: [],
    attempt_history: 'outcomes_only_previous_values_not_retained',
    lockouts: [],
    technical_acceptance: { available: false, reason: 'no_provider' },
    controlled_test_authority: { available: false, reason: 'no_provider' },
    operating_permission: { available: false, reason: 'no_provider' },
    observed_return: { available: false, reason: 'no_provider' },
    service_state: { available: false, reason: 'no_provider' }
  };
  return work;
}

function continuity(held: boolean, executor: boolean): WorkContinuity {
  return {
    rule: 'work_continuity.v1',
    lifecycle_version: 7,
    current_hold: held
      ? {
          reason: 'Synthetic equipment access unavailable',
          recorded_by: 'Synthetic outgoing technician',
          recorded_at: '2026-10-20T08:00:00Z',
          event_id: 701
        }
      : null,
    history: held
      ? [
          {
            event_id: 701,
            action: 'hold',
            from_status: 'in_progress',
            to_status: 'on_hold',
            reason: 'Synthetic equipment access unavailable',
            recorded_by: 'Synthetic outgoing technician',
            recorded_at: '2026-10-20T08:00:00Z',
            lifecycle_version: 7
          }
        ]
      : [],
    commands: {
      hold: executor && !held,
      resume: executor && held,
      unavailable_reason: executor ? null : 'permission_required'
    },
    assignment_acceptance: {
      available: false,
      reason: 'no_site_acceptance_provider'
    },
    accepted_handover: { available: false, reason: 'no_provider' },
    physical_service_state: { available: false, reason: 'no_provider' },
    cross_crew_restrictions: { available: false, reason: 'no_provider' }
  };
}

export function saveTransition(
  work: WorkOrderOverview,
  action: 'hold' | 'resume',
  body: { expected_version: number; idempotency_key: string; reason: string }
) {
  const version = body.expected_version + 1;
  const eventId = 700 + version;
  work.lifecycle_status = action === 'hold' ? 'on_hold' : 'in_progress';
  work.lifecycle_version = version;
  const event: WorkContinuity['history'][number] = {
    event_id: eventId,
    action,
    from_status: action === 'hold' ? 'in_progress' : 'on_hold',
    to_status: work.lifecycle_status,
    reason: body.reason,
    recorded_by: 'Synthetic command recorder',
    recorded_at: '2026-10-20T10:00:00Z',
    lifecycle_version: version
  };
  work.work_continuity!.lifecycle_version = version;
  work.work_continuity!.current_hold =
    action === 'hold'
      ? {
          reason: body.reason,
          recorded_by: event.recorded_by,
          recorded_at: event.recorded_at,
          event_id: eventId
        }
      : null;
  work.work_continuity!.history.unshift(event);
  work.updated_at = event.recorded_at;
  work.events.unshift({
    id: eventId,
    actor: 9,
    event_type: action.toUpperCase(),
    from_status: event.from_status,
    to_status: event.to_status,
    reason: body.reason,
    created_at: event.recorded_at,
    idempotency_key: body.idempotency_key,
    metadata: { lifecycle_version: version }
  });
  work.work_continuity!.commands = {
    hold: action === 'resume',
    resume: action === 'hold',
    unavailable_reason: null
  };
  return {
    work_order_id: 73,
    command: action,
    idempotency_key: body.idempotency_key,
    event_id: eventId,
    lifecycle_status: work.lifecycle_status,
    lifecycle_version: version,
    metadata: {}
  };
}
