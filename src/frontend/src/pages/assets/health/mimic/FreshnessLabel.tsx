import { t } from '@lingui/core/macro';
import { Timestamp } from '../Timestamp';
import {
  type DisplayClock,
  formatInstant,
  useDisplayClock
} from './observedTime';
import type { MimicPoint } from './types';

export function ageLabel(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return t`age unavailable`;
  const s = Math.max(0, Math.floor(seconds));
  if (s >= 86400) return `${Math.floor(s / 86400)} d`;
  if (s >= 3600) return `${Math.floor(s / 3600)} h`;
  if (s >= 60) return `${Math.floor(s / 60)} min`;
  return `${s} s`;
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
  const age = ageLabel(point?.age_seconds ?? null);
  const ageText =
    point?.age_seconds == null
      ? age
      : preferJustNow &&
          point.reason !== 'stale' &&
          point.age_seconds >= 0 &&
          point.age_seconds < 60
        ? t`just now`
        : `${age} old`;
  return point?.reason === 'stale'
    ? t`Stale · last observed ${time} (${ageText})`
    : `${ageText} · ${time}`;
}
export function FreshnessLabel({
  point,
  preferJustNow = false
}: { point?: FreshnessPoint; preferJustNow?: boolean }) {
  const clock = useDisplayClock();
  const seconds = point?.age_seconds;
  const age = ageLabel(seconds ?? null);
  const relative =
    seconds != null &&
    preferJustNow &&
    point?.reason !== 'stale' &&
    seconds >= 0 &&
    seconds < 60
      ? t`just now`
      : seconds == null
        ? age
        : t`${age} old`;
  return (
    <Timestamp
      value={point?.observed_at}
      clock={clock}
      timeOnly
      relative={point?.reason === 'stale' ? t`Stale · ${relative}` : relative}
      stale={point?.reason === 'stale'}
      label={t`Last observation`}
    />
  );
}
