import { plural, t } from '@lingui/core/macro';
import { Box, Button, Group, Stack, Text } from '@mantine/core';
import { IconBellRinging } from '@tabler/icons-react';
import { StatusBadge } from '../health/common';
import operations from '../operations.module.css';
import type { LocatedMachine } from './locationTypes';
import { pageFacts } from './pageFacts';

export function MachinePageSummary({
  rows,
  total,
  alarmsOnly,
  onAlarmsChange
}: Readonly<{
  rows: LocatedMachine[];
  total: number;
  updatedAt?: number;
  updating?: boolean;
  alarmsOnly?: boolean;
  onAlarmsChange?: () => void;
}>) {
  const facts = pageFacts(rows);
  if (total === 0) return null;
  return (
    <Stack
      gap={4}
      className={operations.phoneSummary}
      component='section'
      aria-label={t`Loaded page summary`}
    >
      <Group gap={2} component='output'>
        <Text
          size='sm'
          lh={1.2}
          fw={600}
        >{t`This page · ${rows.length} of ${total} machines`}</Text>
        <Box visibleFrom='sm' className={operations.inline}>
          {(
            [
              [t`Critical`, facts.critical, 'red'],
              [t`Warning`, facts.warning, 'yellow'],
              [t`Offline`, facts.offline, 'gray'],
              [t`Condition unknown (assessed)`, facts.unknown, 'gray'],
              [
                t`Condition unavailable (not judged)`,
                facts.unavailable,
                'gray'
              ],
              [t`Normal`, facts.normal, 'green']
            ] as const
          ).map(([label, count, color]) => (
            <StatusBadge size='sm' key={label} color={color}>
              {label} · {count}
            </StatusBadge>
          ))}
        </Box>
        <Button
          variant='default'
          size='compact-xs'
          h={20}
          aria-pressed={!!alarmsOnly}
          leftSection={<IconBellRinging size={13} />}
          onClick={onAlarmsChange}
        >
          {t`In alarm`}: {facts.alarmRows ?? t`Unavailable`} {t`rows`}
        </Button>
      </Group>
      <Text className={operations.meta}>
        {(facts.stale > 0 || facts.unavailable === 0) &&
          t`Stale signals on ${facts.stale} machines (also counted above)`}
        {facts.unavailable > 0 && (
          <>
            {facts.stale > 0 && ' · '}
            {plural(facts.unavailable, {
              one: 'Stale-signal coverage unavailable for # machine',
              other: 'Stale-signal coverage unavailable for # machines'
            })}
          </>
        )}
      </Text>
      <Text
        className={operations.caveat}
      >{t`Page counts only. Telemetry, run/stop and communication status unavailable here.`}</Text>
    </Stack>
  );
}
