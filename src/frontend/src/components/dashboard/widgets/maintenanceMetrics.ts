import { t } from '@lingui/core/macro';

/** Stable metric IDs: the removed creation/completion comparison is absent. */
export function maintenanceMetrics() {
  return [
    {
      id: 'open',
      title: t`Open work orders`,
      description: t`Current authorized work from planned through verification. Drafts are counted separately.`,
      core: true
    },
    {
      id: 'completed',
      title: t`Completed work orders`,
      description: t`Actual completions in the selected period, including archived work. Missing completion dates remain outside the period.`,
      period: true,
      core: true
    },
    {
      id: 'overdue',
      title: t`Overdue work orders`,
      description: t`Open work orders due before today in the plant timezone.`,
      core: true
    },
    {
      id: 'preventive',
      title: t`Preventive maintenance due`,
      description: t`Existing preventive work orders that are overdue, due today, or due in the selected upcoming window.`,
      core: true
    },
    {
      id: 'assignment',
      title: t`Assignment queue`,
      description: t`Open work by assigned user. Estimates are elapsed work estimates, not labor utilization.`,
      core: true
    },
    {
      id: 'age',
      title: t`Backlog age`,
      description: t`Calendar days since creation for currently open work, including time spent as a draft.`,
      core: true
    },
    {
      id: 'alerts',
      title: t`Machines with active alerts`,
      description: t`Distinct active machines with open or acknowledged warning/critical alerts. Alerts require investigation; they do not establish failure or downtime.`,
      machine: true,
      core: true
    },
    {
      id: 'holds',
      title: t`On hold and parts readiness`,
      description: t`Open jobs on hold or with parts not fully allocated. Groups overlap. Allocation gaps do not establish a stock shortage.`,
      core: false
    },
    {
      id: 'verification',
      title: t`Verification queue`,
      description: t`Open work awaiting verification. Age starts at the latest recorded entry into verification.`,
      core: false
    },
    {
      id: 'pm-on-time',
      title: t`PM on-time completion`,
      description: t`Completed by the currently recorded due date divided by eligible preventive jobs due in the period, including unfinished jobs. Due-date edits can restate history; today is provisional.`,
      period: true,
      core: false
    },
    {
      id: 'elapsed',
      title: t`Elapsed execution time`,
      description: t`Median time from actual start to completion. Includes holds and waiting; this is not hands-on repair time or MTTR.`,
      period: true,
      core: false
    },
    {
      id: 'mix',
      title: t`Maintenance type mix`,
      description: t`Completed jobs by maintenance type. Job-count percentages are not planned maintenance labor percentages.`,
      period: true,
      core: false
    },
    {
      id: 'repeat',
      title: t`Repeated corrective work`,
      description: t`Machines with at least two completed corrective jobs in the period. This does not prove repeat failure.`,
      period: true,
      core: false
    },
    {
      id: 'downtime',
      title: t`Recorded downtime`,
      description: t`Effective amended downtime recorded on jobs completed in the period. Entries may overlap or fall outside the period; this is not unique outage duration.`,
      period: true,
      core: false
    },
    {
      id: 'data-health',
      title: t`Machine data health`,
      description: t`Active machines grouped by mapping, signal freshness and quality. Current data does not guarantee healthy equipment.`,
      machine: true,
      core: false
    }
  ];
}
export type MaintenanceMetric = ReturnType<typeof maintenanceMetrics>[number];

export function metricLabels(): Record<string, string> {
  return {
    planned: t`Planned`,
    ready: t`Ready`,
    in_progress: t`In progress`,
    on_hold: t`On hold`,
    verifying: t`Verifying`,
    completed: t`Completed`,
    corrective: t`Corrective`,
    preventive: t`Preventive`,
    inspection: t`Inspection`,
    calibration: t`Calibration`,
    other: t`Other`,
    high: t`High`,
    medium: t`Medium`,
    low: t`Low`,
    critical: t`Critical`,
    warning: t`Warning`,
    overdue: t`Overdue`,
    today: t`Today`,
    upcoming: t`Upcoming`,
    unassigned: t`Unassigned`,
    parts_gap: t`Parts not fully allocated`,
    on_time: t`On time`,
    late: t`Late`,
    unfinished: t`Unfinished`,
    unknown: t`Unknown`,
    invalid: t`Invalid date`,
    unmapped: t`No mapped signals`,
    stale: t`All signals stale or missing`,
    degraded: t`Degraded data`,
    current: t`Current good data`,
    completion_date_unknown_period: t`Completed without a date — period unknown`,
    due_date: t`Missing due date`,
    estimate: t`Missing estimate`,
    verification_start: t`Missing verification entry`,
    actual_start: t`Missing actual start`,
    invalid_duration: t`Invalid duration`,
    machine_not_visible: t`Machine unavailable for grouping`,
    downtime: t`Downtime not recorded`,
    drafts: t`Drafts (excluded)`,
    previous: t`Previous interval completions`,
    change: t`Change from previous interval`,
    legacy_assignment: t`Legacy assignment needs review`,
    estimated_minutes: t`Estimated elapsed minutes`,
    overlap: t`Orders in both groups`,
    numerator: t`Completed on time`,
    denominator: t`Eligible jobs due`,
    mean_minutes: t`Mean elapsed minutes`,
    population: t`Eligible population`,
    high_priority: t`High-priority jobs`,
    connector_issues: t`Machines with connector issues (overlapping)`
  };
}
