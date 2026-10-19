/**
 * R6 P22: a job's material readout (`tasks/work-orders/:id/materials/`).
 *
 * Every stage is its own recorded fact with its own recorder; a missing
 * recorder or stage is "not recorded", never filled in from a neighbour.
 */

export interface MaterialPart {
  pk: number;
  name: string;
  ipn: string;
  units: string;
  virtual: boolean;
}

export interface MaterialStock {
  pk: number | null;
  hidden: boolean;
  serial?: string;
  batch?: string;
  location?: string;
  location_pk?: number | null;
}

export interface MaterialUsage {
  id: number;
  part: MaterialPart | null;
  stock_item: MaterialStock | null;
  planned_quantity: number | null;
  issued_quantity: number | null;
  used_quantity: number | null;
  disposition: string | null;
  variance_reason: string;
  source: string;
  candidate_text: string;
  state: string;
  resolved_by: string | null;
  updated_at: string | null;
}

interface Recorded {
  by: string | null;
  at: string | null;
}

export interface MaterialReservation {
  id: number;
  quantity: number;
  status: string;
  stock_item: MaterialStock | null;
  reserved: Recorded;
  staged: Recorded | null;
  issued: Recorded | null;
  closed: (Recorded & { status: string; tracking_id: number | null }) | null;
  usage: MaterialUsage[];
}

export interface MaterialRequirement {
  key: string;
  part: MaterialPart | null;
  requested_part: MaterialPart | null;
  quantity: number;
  required: boolean;
  fit_basis: {
    source: string;
    substitution_policy: string;
    note: string;
  } | null;
  reservations: MaterialReservation[] | null;
  shortages:
    | {
        id: number;
        quantity: number;
        status: string;
        purchase_order_line: number | null;
        reason: string;
      }[]
    | null;
  availability_check: {
    status: string;
    quantity: number;
    note: string;
    checked_at: string | null;
  } | null;
}

export interface MaterialReadout {
  work_order: number;
  source: 'job_kit' | 'requirements';
  kits_enabled: boolean;
  stock_visible: boolean;
  component: {
    ref: string;
    pk: number | null;
    name: string;
    machine: number | null;
  } | null;
  requirements: MaterialRequirement[];
  unmatched_usage: MaterialUsage[];
}

export type StageKey = 'reserved' | 'issued' | 'used' | 'returned' | 'released';

export interface Stage {
  key: StageKey;
  /** done: recorded; skipped: a later stage is recorded but this one is not. */
  state: 'done' | 'pending' | 'skipped';
  by: string | null;
  at: string | null;
}

/** Reserved → Issued → Used (or Returned/Released) for one reservation. */
export function reservationStages(reservation: MaterialReservation): Stage[] {
  const closed = reservation.closed;
  const end: StageKey =
    closed?.status === 'returned'
      ? 'returned'
      : closed?.status === 'released'
        ? 'released'
        : 'used';
  const ended = !!closed && closed.status !== 'exception';
  const issued = reservation.issued;
  return [
    {
      key: 'reserved',
      state: 'done',
      by: reservation.reserved.by,
      at: reservation.reserved.at
    },
    // A release ends a reservation before any issue; nothing was issued.
    ...(end === 'released'
      ? []
      : [
          {
            key: 'issued' as const,
            state: issued
              ? ('done' as const)
              : ended
                ? ('skipped' as const)
                : ('pending' as const),
            by: issued?.by ?? null,
            at: issued?.at ?? null
          }
        ]),
    {
      key: end,
      state: ended ? 'done' : 'pending',
      by: ended ? closed!.by : null,
      at: ended ? closed!.at : null
    }
  ];
}

/** A shortage still owned by someone (receipt is not reserved stock). */
export const OPEN_SHORTAGE = ['open', 'requested', 'ordered', 'partial'];

/** Quantity held by reservations still live or consumed for this job. */
export function reservedQuantity(
  requirement: MaterialRequirement
): number | null {
  if (!requirement.reservations) return null;
  return requirement.reservations
    .filter((r) => !['released', 'returned', 'exception'].includes(r.status))
    .reduce((total, r) => total + r.quantity, 0);
}

/**
 * The neutral line for the whole job: stock covers every required line and
 * nothing is short. False whenever that is not what the records say.
 */
export function coveredByStock(readout: MaterialReadout): boolean {
  if (readout.source !== 'job_kit' || readout.requirements.length === 0)
    return false;
  return readout.requirements
    .filter((requirement) => requirement.required)
    .every(
      (requirement) =>
        (reservedQuantity(requirement) ?? 0) >= requirement.quantity &&
        !(requirement.shortages ?? []).some((shortage) =>
          OPEN_SHORTAGE.includes(shortage.status)
        )
    );
}
