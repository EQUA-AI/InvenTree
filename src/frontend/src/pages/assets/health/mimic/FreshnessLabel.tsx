import { t } from '@lingui/core/macro';
import { Text, Tooltip } from '@mantine/core';
import { IconClockExclamation } from '@tabler/icons-react';
import operations from '../../operations.module.css';
import { StatusBadge } from '../common';
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
  clock: DisplayClock
): string {
  const time = formatInstant(point?.observed_at, clock, { timeOnly: true });
  if (!time) return t`Observation time unavailable`;
  const age = ageLabel(point?.age_seconds ?? null);
  const ageText = point?.age_seconds == null ? age : `${age} old`;
  return point?.reason === 'stale'
    ? t`Stale · last observed ${time} (${ageText})`
    : `${ageText} · ${time}`;
}
export function FreshnessLabel({ point }: { point?: FreshnessPoint }) {
  const clock = useDisplayClock();
  const label = freshnessText(point, clock);
  return (
    <Tooltip
      label={`${formatInstant(point?.observed_at, clock) ?? t`Time unavailable`} · ${clock.zone}`}
    >
      {point?.reason === 'stale' ? (
        <StatusBadge
          variant='outline'
          color='gray'
          miw={0}
          maw='100%'
          h='auto'
          styles={{
            label: { whiteSpace: 'normal', lineHeight: 1.4 },
            root: { paddingBlock: 3 }
          }}
          leftSection={<IconClockExclamation size={14} />}
        >
          {label}
        </StatusBadge>
      ) : (
        <Text component='span' size='sm' className={operations.meta}>
          {label}
        </Text>
      )}
    </Tooltip>
  );
}
