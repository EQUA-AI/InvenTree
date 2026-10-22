import { t } from '@lingui/core/macro';
import { Timestamp } from '../Timestamp';
import { ageDuration, relativeAge } from '../relativeTime';
import {
  type DisplayClock,
  formatInstant,
  useDisplayClock
} from './observedTime';
import type { MimicPoint } from './types';

export function ageLabel(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return t`age unavailable`;
  return ageDuration(seconds);
}
type FreshnessPoint = Pick<
  MimicPoint,
  'observed_at' | 'age_seconds' | 'reason'
>;
export function freshnessText(
  point: FreshnessPoint | undefined,
  clock: DisplayClock,
  preferJustNow = false
): string {
  const time = formatInstant(point?.observed_at, clock, { timeOnly: true });
  if (!time) return t`Observation time unavailable`;
  const ageText = relativeAge(
    point?.age_seconds ?? null,
    preferJustNow,
    point?.reason === 'stale'
  );
  return point?.reason === 'stale'
    ? t`Stale · last observed ${time} (${ageText})`
    : `${ageText} · ${time}`;
}
export function FreshnessLabel({
  point,
  preferJustNow = false
}: { point?: FreshnessPoint; preferJustNow?: boolean }) {
  const clock = useDisplayClock();
  const relative = relativeAge(
    point?.age_seconds ?? null,
    preferJustNow,
    point?.reason === 'stale'
  );
  return (
    <Timestamp
      value={point?.observed_at}
      clock={clock}
      compactOnPhone
      relative={point?.reason === 'stale' ? t`Stale · ${relative}` : relative}
      stale={point?.reason === 'stale'}
      label={t`Last observation`}
    />
  );
}
