import { t } from '@lingui/core/macro';
import { Group, type GroupProps } from '@mantine/core';
import { IconBellRinging, IconInfoCircle } from '@tabler/icons-react';
import { StatusBadge } from '../../pages/assets/health/common';

/** Counts include the machine and its child pumps. Missing severity is not warning. */
export function MachineAlarmBadges({
  open,
  critical,
  warning,
  justify
}: Readonly<{
  open?: number | null;
  critical?: number | null;
  warning?: number | null;
  justify?: GroupProps['justify'];
}>) {
  if (typeof open !== 'number' || open <= 0) return null;
  const knownCritical =
    typeof critical === 'number' &&
    Number.isSafeInteger(critical) &&
    critical >= 0 &&
    critical <= open;
  const c = knownCritical ? critical : 0;
  const w =
    typeof warning === 'number' &&
    Number.isSafeInteger(warning) &&
    warning >= 0 &&
    warning <= open - c
      ? warning
      : null;
  return (
    <Group gap={4} wrap='wrap' justify={justify}>
      {c > 0 && (
        <StatusBadge
          color='red.9'
          variant='filled'
          size='sm'
          leftSection={<IconBellRinging size={12} />}
        >
          {t`Critical`} · {c}
        </StatusBadge>
      )}
      {w !== null && w > 0 && (
        <StatusBadge
          color='yellow'
          size='sm'
          leftSection={<IconBellRinging size={12} />}
        >
          {t`Warning`} · {w}
        </StatusBadge>
      )}
      {w !== null && knownCritical && open > c + w && (
        <StatusBadge
          color='gray'
          size='sm'
          leftSection={<IconInfoCircle size={12} />}
        >
          {t`Other`} · {open - c - w}
        </StatusBadge>
      )}
      {w === null && knownCritical && open > c && (
        <StatusBadge color='gray' size='sm'>
          {t`Non-critical`} · {open - c}
        </StatusBadge>
      )}
      {!knownCritical && (
        <StatusBadge color='gray' size='sm'>
          {t`Active`} · {open} · {t`Severity unavailable`}
        </StatusBadge>
      )}
    </Group>
  );
}
