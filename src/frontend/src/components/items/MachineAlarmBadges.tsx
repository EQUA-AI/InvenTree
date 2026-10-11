import { t } from '@lingui/core/macro';
import { Badge, Group, type GroupProps } from '@mantine/core';

/**
 * How many alarms stand on a machine, the criticals apart from the rest.
 *
 * One rendering for every list that says where there is something to look
 * at, so the Machines page and the dashboard read alike. Renders nothing when
 * no alarm stands; what an empty cell shows is the list's own business.
 */
export function MachineAlarmBadges({
  open: openAlarms,
  critical: criticalAlarms,
  justify
}: Readonly<{
  open?: number | null;
  critical?: number | null;
  justify?: GroupProps['justify'];
}>) {
  const open = openAlarms ?? 0;
  const critical = criticalAlarms ?? 0;

  if (!open) {
    return null;
  }

  return (
    <Group gap={6} wrap='nowrap' justify={justify}>
      {critical > 0 && (
        <Badge color='red' variant='filled' size='sm'>
          {t`${critical} critical`}
        </Badge>
      )}
      {open > critical && (
        <Badge color='yellow' variant='light' size='sm'>
          {t`${open - critical} warning`}
        </Badge>
      )}
    </Group>
  );
}
