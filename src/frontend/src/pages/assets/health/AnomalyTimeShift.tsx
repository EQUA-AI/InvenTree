import type { MachineAnomaly } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Stack } from '@mantine/core';
import { IconClockShare } from '@tabler/icons-react';
import { displayInstant } from '../displayClock';
import { shiftSpan } from '../faults/occurrenceLabels';
import { showRecordedTelemetry } from '../telemetryPresentation';
import { StatusBadge } from './StatusBadge';
import { MetaText } from './common';
import { shiftFacts } from './shiftFacts';

export function AnomalyTimeShift({
  anomaly,
  time
}: Readonly<{ anomaly: MachineAnomaly; time?: string | null }>) {
  if (!showRecordedTelemetry(anomaly)) return null;
  const fact = shiftFacts(anomaly.display_shift_seconds, time);
  const span = fact.seconds == null ? null : shiftSpan(fact.seconds);
  return (
    <Stack gap={2} data-testid='anomaly-time-shift'>
      <StatusBadge
        multiline
        tone='neutral'
        color='gray'
        variant='outline'
        leftSection={<IconClockShare size={14} />}
      >
        {span
          ? t`Shifted ${fact.sign}${span}`
          : t`Display shift reported; amount unavailable`}
      </StatusBadge>
      <MetaText size='xs'>
        {t`Recorded`}: {displayInstant(fact.recorded) ?? t`Time unavailable`}
      </MetaText>
    </Stack>
  );
}
