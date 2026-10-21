import type {
  InvestigationFinding,
  InvestigationRequest,
  PreparedPackage,
  ScopeRequestSnapshot
} from '../../lib/types/WorkExecution';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { workOrder } from './workExecutionFixtures';

export function revisionPackage(): PreparedPackage {
  return {
    rule: 'prepared_package.v1',
    scopes: [
      {
        id: 202,
        version: 2,
        current: true,
        verified_cause: 'Synthetic revised guard basis',
        lines: [
          { sequence: 1, action: 'Synthetic inspect guard fasteners' },
          { sequence: 2, action: 'Synthetic retained visual check' }
        ],
        crew_size: 2,
        planned_elapsed_minutes: 35,
        approved_by: 'Synthetic planner',
        approved_at: '2026-10-20T08:00:00Z',
        note: 'Synthetic approval note: guard inspection',
        superseded_at: null
      },
      {
        id: 201,
        version: 1,
        current: false,
        verified_cause: 'Synthetic earlier bearing basis',
        lines: [
          { sequence: 1, action: 'Synthetic inspect bearing' },
          { sequence: 2, action: 'Synthetic retained visual check' },
          { sequence: 3, action: 'Synthetic earlier follow-up' }
        ],
        crew_size: 1,
        planned_elapsed_minutes: 20,
        approved_by: 'Synthetic first planner',
        approved_at: '2026-10-19T07:00:00Z',
        note: 'Synthetic original approval',
        superseded_at: '2026-10-20T08:00:00Z'
      }
    ],
    procedures_state: 'read',
    applications: [],
    deviations: [],
    can_record_deviation: false,
    physical_authority: { available: false, reason: 'no_provider' },
    affected_dependencies: { available: false, reason: 'no_provider' },
    revision_review: {
      rule: 'scope_revision.v1',
      state: 'revised',
      current_scope_id: 202,
      comparisons: [
        {
          before_scope_id: 201,
          after_scope_id: 202,
          basis: 'retained_version_order'
        }
      ],
      dependency_impact: (
        ['tasks', 'materials', 'resources', 'permissions', 'criteria'] as const
      ).map((kind) => ({
        kind,
        available: false,
        reason: 'no_revision_dependency_links'
      })),
      evidence_validity: {
        available: false,
        reason: 'no_scope_evidence_links'
      },
      request_revisions: [
        {
          event_id: 601,
          request_id: 401,
          before: requestSnapshot('Synthetic first instrument', 2),
          after: requestSnapshot('Synthetic second instrument', 3),
          retained_evidence_ids: [501],
          recorded_by: 'Synthetic technician',
          recorded_at: '2026-10-20T08:05:00Z',
          scope_link: null,
          revision_reason: null
        }
      ]
    }
  };
}

function requestSnapshot(
  next_check: string,
  version: number
): ScopeRequestSnapshot {
  return {
    version,
    purpose: 'Synthetic distinguish guard noise',
    next_check,
    status: 'cannot_perform',
    owner_name: 'Synthetic request owner',
    next_checkpoint: '2026-10-20T12:00:00Z',
    blocked_reason: 'Synthetic first instrument unavailable',
    source_finding: version > 2 ? 101 : null,
    required_artifacts: [
      {
        kind: 'reading_set',
        description: 'Synthetic named readings',
        unit: 'g',
        conditions: 'Synthetic approved access point'
      }
    ]
  };
}

export function revisionRequest(): InvestigationRequest {
  return {
    id: 401,
    work_order: 73,
    schema_version: 1,
    ...requestSnapshot('Synthetic second instrument', 3),
    priority: 1,
    owner: 9,
    source_finding: 101,
    source_proposal: null,
    resolution: '',
    created_by: 9,
    updated_by: 9,
    updated_by_name: 'Synthetic technician',
    created_at: '2026-10-19T09:00:00Z',
    updated_at: '2026-10-20T08:05:00Z',
    artifacts: [
      {
        id: 501,
        request: 401,
        kind: 'observation',
        payload: { text: 'Synthetic retained original observation' },
        attachment: null,
        supersedes: null,
        recorded_by: 9,
        recorded_by_name: 'Synthetic original technician',
        created_at: '2026-10-19T09:00:00Z'
      }
    ]
  };
}

export function revisionWork(): WorkOrderOverview {
  const value = revisionPackage();
  const finding: InvestigationFinding = {
    id: 101,
    finding_key: 'SYN-SCOPE-1',
    category: 'inspection',
    observation: 'Synthetic guard inspection source',
    value: null,
    unit: '',
    evidence_source: 'Synthetic inspection note',
    snapshot_id: null,
    observed_at: '2026-10-20T07:00:00Z',
    verification: 'unverified',
    recorded_by: 'Synthetic technician',
    created_at: '2026-10-20T07:00:00Z',
    updated_at: '2026-10-20T07:00:00Z'
  };
  return {
    ...workOrder(),
    prepared_package: value,
    repair_packet: {
      id: 901,
      reference: 'RP-901',
      status: 'executing',
      criticality: 'medium',
      fault_summary: 'Synthetic guard noise',
      symptom: '',
      production_impact: '',
      generation_status: 'succeeded',
      diagnosis: {},
      diagnosis_is_preliminary: true,
      diagnosis_status: null,
      findings: [finding],
      gates: [],
      approved_scope: {
        id: 202,
        version: 2,
        verified_cause: value.scopes[0].verified_cause,
        scope_lines: value.scopes[0].lines,
        failure_codes: [],
        crew_size: 2,
        planned_elapsed_minutes: 35,
        approved_at: value.scopes[0].approved_at,
        approval_note: value.scopes[0].note
      }
    },
    investigation: {
      rule: 'investigation.v1',
      packet: { id: 901, reference: 'RP-901', generation_status: 'succeeded' },
      explanation: null,
      findings: [finding],
      finding_history: 'preserved_revisions',
      finding_revisions: [],
      intervention: {
        scope_id: 202,
        version: 2,
        verified_cause: value.scopes[0].verified_cause,
        failure_codes: [],
        scope_line_count: value.scopes[0].lines.length,
        approved_by: value.scopes[0].approved_by,
        approved_at: value.scopes[0].approved_at,
        superseded_versions: 1
      },
      requests: { available: true, reason: '', items: [revisionRequest()] }
    }
  };
}
