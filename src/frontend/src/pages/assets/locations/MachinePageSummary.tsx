import { plural, t } from '@lingui/core/macro';
import { Box, Button, Group, Stack, Text } from '@mantine/core';
import { IconBellRinging } from '@tabler/icons-react';
import { StatusBadge } from '../health/common';
import operations from '../operations.module.css';
import type { LocatedMachine } from './locationTypes';
import { machineCondition } from './machineCondition';
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
        <Group
          gap={4}
          className={operations.conditionCounts}
          aria-label={t`Condition totals`}
          component='fieldset'
          bd={0}
          p={0}
          m={0}
          miw={0}
        >
          {(
            [
              ['critical', t`Critical`, facts.critical],
              ['warning', t`Warning`, facts.warning],
              ['unavailable', t`Unavailable`, facts.unavailable]
            ] as const
          ).map(([kind, label, count]) => (
            <Button
              key={kind}
              size='compact-xs'
              variant='default'
              className={operations.phoneTarget}
              disabled={count === 0}
              aria-label={`${label} · ${count}`}
              onClick={() => {
                const row = rows.find((row) => {
                  const condition = machineCondition(row.condition);
                  return kind === 'unavailable'
                    ? condition.kind === kind
                    : condition.kind === 'assessed' && condition.state === kind;
                });
                const target =
                  row && document.getElementById(`machine-row-${row.pk}`);
                target?.focus({ preventScroll: true });
                target?.scrollIntoView({ block: 'center' });
              }}
            >
              {kind === 'unavailable' ? (
                <>
                  <Box
                    component='span'
                    visibleFrom='sm'
                  >{t`Condition unavailable (not judged)`}</Box>
                  <Box component='span' hiddenFrom='sm'>
                    {label}
                  </Box>
                </>
              ) : (
                label
              )}{' '}
              · {count}
            </Button>
          ))}
        </Group>
        <Box visibleFrom='sm' className={operations.inline}>
          {(
            [
              [t`Offline`, facts.offline, 'gray'],
              [t`Condition unknown (assessed)`, facts.unknown, 'gray'],
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
          className={`${operations.phoneTarget} ${operations.alarmFilter}`}
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
