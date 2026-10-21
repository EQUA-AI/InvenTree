import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';

export type ContinuityAttention = 'handover' | 'hold';
export type AssignmentAttention = 'open_at_closure' | 'own_running';

export interface WorkAttention {
  continuity: ContinuityAttention | null;
  assignment: AssignmentAttention | null;
  /**
   * One key per actionable record. A disclosure is opened again only when a
   * key it has not shown before appears, so a person can still collapse it.
   */
  keys: string[];
}

/**
 * Actionable states inside the Work context disclosures, read only from the
 * server projection: a pending handover the server says this person can
 * accept, held Work, this person's own running interval, and Work closed with
 * an open interval. Nothing is inferred from assignment or browser activity.
 */
export function workAttention(
  work: Pick<
    WorkOrderOverview,
    | 'lifecycle_status'
    | 'lifecycle_version'
    | 'work_continuity'
    | 'time_assignment'
  >,
  userId: number | null | undefined
): WorkAttention {
  const keys: string[] = [];
  let continuity: ContinuityAttention | null = null;
  let assignment: AssignmentAttention | null = null;

  const value =
    work.work_continuity?.rule === 'work_continuity.v1'
      ? work.work_continuity
      : null;
  const handover = value?.accepted_handover;
  if (
    handover?.available &&
    handover.state === 'pending' &&
    handover.can_accept === true &&
    typeof handover.id === 'number'
  ) {
    continuity = 'handover';
    keys.push(`continuity:handover:${handover.id}`);
  }
  if (work.lifecycle_status === 'on_hold') {
    continuity ??= 'hold';
    keys.push(`continuity:hold:${work.lifecycle_version}`);
  }

  const time = work.time_assignment?.time_records;
  if (time?.open_at_closure) {
    assignment = 'open_at_closure';
    keys.push(`assignment:closure:${work.lifecycle_version}`);
  }
  const own =
    userId == null
      ? undefined
      : time?.intervals.find(
          (row) => !row.superseded && !row.ended_at && row.person_id === userId
        );
  if (own) {
    assignment ??= 'own_running';
    keys.push(`assignment:running:${own.id}`);
  }
  return { continuity, assignment, keys };
}
