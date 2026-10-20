import type { MachineAnomaly } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import { shiftSpan } from '../faults/occurrenceLabels';

/** Observation display offsets are separate from timezone formatting and human action times. */
export function AnomalyTimeShift({
  anomaly
}: Readonly<{ anomaly: MachineAnomaly }>) {
  if (!anomaly.display_shifted) return null;
  const seconds = anomaly.display_shift_seconds;
  const span =
    typeof seconds === 'number' && Number.isFinite(seconds)
      ? shiftSpan(Math.abs(seconds))
      : null;
  return (
    <Text size='sm' data-testid='anomaly-time-shift'>
      {span
        ? (seconds ?? 0) < 0
          ? t`Shown ${span} earlier than recorded`
          : t`Shown ${span} later than recorded`
        : t`Display shift reported; amount unavailable`}
    </Text>
  );
}
