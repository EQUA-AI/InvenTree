import { t } from '@lingui/core/macro';
import { Group, Paper, Stack, Text } from '@mantine/core';
import operations from '../operations.module.css';
import type { LocatedMachine } from './locationTypes';
import { pageFacts } from './pageFacts';

/** A summary of loaded records with its own scope and read time. */
export function MachinePageSummary({
  rows,
  total,
  updatedAt,
  updating
}: Readonly<{
  rows: LocatedMachine[];
  total: number;
  updatedAt: number;
  updating: boolean;
}>) {
  const facts = pageFacts(rows);
  const metrics = [
    [t`Loaded machines`, `${rows.length} / ${total}`],
    [t`Health warning / critical`, facts.alerts],
    [t`No current data`, facts.offline],
    [t`Condition unknown`, facts.unknown],
    [t`Rows with active alarms`, facts.alarmRows ?? t`Unavailable`]
  ];
  return (
    <Paper
      component='section'
      aria-label={t`Loaded page summary`}
      className={operations.band}
    >
      <Stack gap='xs'>
        <Group justify='space-between' gap='xs'>
          <Text size='sm' fw={600}>{t`Loaded page · operational context`}</Text>
          <Text className={operations.caveat}>
            {updating ? t`Updating records…` : t`Records read`}{' '}
            {updatedAt
              ? `${new Date(updatedAt).toLocaleTimeString()} (${Intl.DateTimeFormat().resolvedOptions().timeZone})`
              : t`Time unavailable`}
          </Text>
        </Group>
        <div className={operations.metrics}>
          {metrics.map(([label, value]) => (
            <div key={label} className={operations.metric}>
              <Text className={operations.label}>{label}</Text>
              <Text className={operations.value}>{value}</Text>
            </div>
          ))}
        </div>
        <Text
          className={operations.caveat}
        >{t`Counts describe this page only; not the whole station. ${facts.stale} loaded machines report stale signals.`}</Text>
        <Text
          className={operations.caveat}
        >{t`Telemetry timestamps, run / stop and communication status are unavailable in this list. Active record is an asset setting.`}</Text>
      </Stack>
    </Paper>
  );
}
