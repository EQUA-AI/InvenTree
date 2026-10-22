import { t } from '@lingui/core/macro';
import { ValueTooltip } from '../../../../components/items/ValueTooltip';
import { shiftSpan } from '../../faults/occurrenceLabels';
import { recordedTelemetryAsLive } from '../../telemetryPresentation';
import { StatusBadge } from '../common';
import type { MimicData } from './types';

/** O27 keeps the recorded source live; its clock adjustment remains inspectable. */
export function MimicLiveBadge({ data }: { data: MimicData }) {
  if (
    !recordedTelemetryAsLive(data) ||
    !data.enabled ||
    !data.reporting ||
    data.last_error_code
  )
    return null;
  const detail = data.display_shifted
    ? t`Live · recorded source · Times moved ${shiftSpan(data.display_shift_seconds ?? 0) ?? t`an unspecified interval`} onto the display clock; plant times are earlier`
    : t`Live readings`;
  return (
    <ValueTooltip label={detail} accessibleLabel={detail}>
      <StatusBadge color='blue' variant='outline'>{t`Live`}</StatusBadge>
    </ValueTooltip>
  );
}
