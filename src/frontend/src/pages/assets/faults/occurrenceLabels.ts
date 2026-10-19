/**
 * Labels for the Fault History occurrences (R6 P12 / UI08). Kept apart from
 * the pure model so the model's unit tests need no translation runtime.
 */

import { t } from '@lingui/core/macro';

import type { AnomalyStatus } from '@lib/types/MachineHealth';
import { type OccurrenceWork, shiftParts } from './occurrenceModel';

export function occurrenceStatusLabel(status: AnomalyStatus): string {
  switch (status) {
    case 'open':
      return t`Active`;
    case 'acknowledged':
      return t`Active`;
    case 'resolved':
      return t`Resolved`;
    case 'suppressed':
      return t`Suppressed`;
    default:
      return status;
  }
}

/** Who raised it: the source system's own alarm, or a limit configured here. */
export function detectorLabel(detector: string): string {
  switch (detector) {
    case 'source_alarm':
      return t`Source system alarm`;
    case 'threshold':
      return t`Limit configured in AIMMS`;
    default:
      return detector || t`Not recorded`;
  }
}

export function snapshotReasonLabel(reason: string): string {
  switch (reason) {
    case 'manual_repair':
      return t`Captured for a manual repair`;
    case 'anomaly_repair':
      return t`Captured for the repair raised for it`;
    case 'ai_diagnosis':
      return t`Captured for a preliminary analysis`;
    case 'confirmation_test':
      return t`Captured for a confirmation test`;
    default:
      return reason || t`Reason not recorded`;
  }
}

export function workRelationLabel(
  relation: OccurrenceWork['relation']
): string {
  return relation === 'owning' ? t`Owning work` : t`Repair packet work`;
}

/** "3 d 4 h" for a display shift, or null when nothing was moved. */
export function shiftSpan(seconds: number): string | null {
  const shift = shiftParts(seconds);
  if (!shift) return null;
  const { days, hours, minutes } = shift;
  const parts = [
    days ? t`${days} d` : '',
    hours ? t`${hours} h` : '',
    !days && minutes ? t`${minutes} min` : ''
  ].filter(Boolean);
  return parts.join(' ') || t`under a minute`;
}

/** A retained value as the source wrote it; absence is said, not zeroed. */
export function retainedValue(value: unknown, unit: string): string {
  if (value === null || value === undefined) return t`No value retained`;
  if (typeof value === 'number')
    return unit ? `${String(value)} ${unit}` : String(value);
  if (typeof value === 'string' || typeof value === 'boolean')
    return unit ? `${String(value)} ${unit}` : String(value);
  return JSON.stringify(value);
}
