import { t } from '@lingui/core/macro';
import {
  FailedValue,
  MissingValue
} from '../../../../components/items/ValueTooltip';
import { freshnessText } from './FreshnessLabel';
import type { Display } from './MimicReadings';
import { reasonLabel, valueText } from './format';
import { formatInstant, useDisplayClock } from './observedTime';
import type { MimicPoint } from './types';

export function failedReading(point?: MimicPoint): boolean {
  return (
    !!point &&
    ((typeof point.value === 'number' && !Number.isFinite(point.value)) ||
      [
        'bad_quality',
        'over_range',
        'railed',
        'not_finite',
        'type_mismatch',
        'clock_skew',
        'mapping_changed'
      ].includes(point.reason ?? '') ||
      (!!point.quality && !['good', 'unknown'].includes(point.quality)))
  );
}

/** Keep a supplied last value visible when its quality cannot be trusted. */
export function UnavailableReading({
  point,
  label,
  display = valueText,
  focusable = true
}: Readonly<{
  point?: MimicPoint;
  label?: string;
  display?: Display;
  focusable?: boolean;
}>) {
  const clock = useDisplayClock();
  const observed = formatInstant(point?.observed_at, clock);
  const reason = [
    reasonLabel(point?.reason ?? null) || t`Reason not supplied`,
    ...(observed ? [observed, clock.zone, freshnessText(point, clock)] : [])
  ].join(' · ');
  const retained =
    point?.value != null &&
    point.reason !== 'not_finite' &&
    (typeof point.value !== 'number' || Number.isFinite(point.value));
  return failedReading(point) ? (
    <FailedValue
      label={label}
      reason={reason}
      value={retained ? display({ ...point, reason: null }) : undefined}
      focusable={focusable}
    />
  ) : (
    <MissingValue label={label} reason={reason} focusable={focusable} />
  );
}
