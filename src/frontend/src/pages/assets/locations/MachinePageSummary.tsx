import { plural, t } from '@lingui/core/macro';
import { Group, UnstyledButton } from '@mantine/core';
import { IconBellRinging } from '@tabler/icons-react';
import { ValueTooltip } from '../../../components/items/ValueTooltip';
import { StatusBadge } from '../health/StatusBadge';
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
  const countLabel =
    rows.length < total
      ? t`${rows.length} of ${total}`
      : plural(total, { one: '# machine', other: '# machines' });
  const alarmToggle = (
    <UnstyledButton
      className={classes.summaryAction}
      aria-pressed={!!alarmsOnly}
      onClick={onAlarmsChange}
    >
      <StatusBadge
        tone='warning'
        color='yellow'
        size='sm'
        variant='outline'
        leftSection={<IconBellRinging size={13} />}
      >
        {t`In alarm`} · {facts.alarmRows ?? t`Unavailable`}
      </StatusBadge>
    </UnstyledButton>
  );
  return (
    <Group
      gap='xs'
      className={classes.pageSummary}
      component='section'
      aria-label={t`Loaded page summary`}
    >
      <Group className={classes.summaryHeading} gap={8} justify='space-between'>
        <ValueTooltip
          label={pageDescription}
          accessibleLabel={`${countLabel} · ${pageDescription}`}
          data-testid='machine-page-count'
          className={classes.summaryCount}
        >
          {countLabel}
        </ValueTooltip>
        {alarmToggle}
      </Group>
      <Group
        gap={8}
        wrap='wrap'
        className={classes.summaryStatuses}
        component='fieldset'
        aria-label={t`Condition totals`}
        bd={0}
        p={0}
        m={0}
        miw={0}
      >
        {(
          [
            ['critical', t`Critical`, facts.critical, 'critical'],
            ['warning', t`Warning`, facts.warning, 'warning'],
            ['unavailable', t`Read failed`, facts.unavailable, 'failed']
          ] as const
        ).map(([kind, label, count, tone]) => (
          <UnstyledButton
            key={kind}
            className={classes.summaryAction}
            disabled={count === 0}
            aria-label={`${label} · ${count}`}
            onClick={() => {
              const row = rows.find((item) => {
                const condition = machineCondition(item.condition);
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
            <StatusBadge
              tone={count ? tone : 'neutral'}
              color={count ? (kind === 'warning' ? 'yellow' : 'red') : 'gray'}
              size='sm'
            >
              {label} · {count}
            </StatusBadge>
          </UnstyledButton>
        ))}
      </Group>
    </Group>
  );
}
