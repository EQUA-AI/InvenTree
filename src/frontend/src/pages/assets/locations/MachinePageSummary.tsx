import { plural, t } from '@lingui/core/macro';
import { Button, Group, Text, Tooltip } from '@mantine/core';
import { IconBellRinging } from '@tabler/icons-react';
import operations from '../operations.module.css';
import type { LocatedMachine } from './locationTypes';
import classes from './locations.module.css';
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
  const coverage = plural(facts.unavailable, {
    one: 'Stale-signal coverage unavailable for # machine',
    other: 'Stale-signal coverage unavailable for # machines'
  });
  const pageDescription = t`Page counts only. ${facts.normal} normal; ${facts.unknown} condition unknown; ${facts.offline} with no current data. Stale signals on ${facts.stale} machines. ${coverage}. Telemetry, run/stop and communication status unavailable here.`;
  return (
    <Group
      gap='xs'
      className={`${operations.phoneSummary} ${classes.pageSummary}`}
      component='section'
      aria-label={t`Loaded page summary`}
    >
      <Group gap='xs' component='output'>
        <Tooltip
          label={pageDescription}
          multiline
          maw={360}
          events={{ hover: true, focus: true, touch: true }}
        >
          <Text
            component='span'
            tabIndex={0}
            data-testid='machine-page-count'
            size='sm'
            lh={1.2}
            fw={600}
            aria-label={pageDescription}
          >{t`This page · ${rows.length} of ${total} machines`}</Text>
        </Tooltip>
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
              {label} · {count}
            </Button>
          ))}
        </Group>
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
    </Group>
  );
}
