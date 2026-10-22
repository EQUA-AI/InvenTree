import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import {
  FailedValue,
  MissingValue,
  ValueTooltip
} from '../../../components/items/ValueTooltip';
import { StatusBadge } from './StatusBadge';
import classes from './Timestamp.module.css';
import {
  type DisplayClock,
  formatInstant,
  useDisplayClock
} from './mimic/observedTime';

/** Operational times share one validated clock, date spelling and tooltip. */
export function Timestamp({
  value,
  clock: suppliedClock,
  relative,
  stale = false,
  label,
  compactOnPhone = false,
  timeOnly = false,
  dateOnly = false,
  focusable = true
}: Readonly<{
  value: string | number | null | undefined;
  clock?: DisplayClock;
  relative?: string;
  stale?: boolean;
  label?: string;
  compactOnPhone?: boolean;
  timeOnly?: boolean;
  dateOnly?: boolean;
  focusable?: boolean;
}>) {
  const inherited = useDisplayClock();
  const clock = suppliedClock ?? inherited;
  const full = formatInstant(value, clock);
  if (!full)
    return value == null || value === '' ? (
      <MissingValue
        label={label ?? t`Time`}
        reason={t`Observation time unavailable`}
        focusable={focusable}
      />
    ) : (
      <FailedValue
        label={label ?? t`Time`}
        reason={t`Invalid observation time`}
        focusable={focusable}
      />
    );
  const text = formatInstant(value, clock, { timeOnly, dateOnly });
  const short = formatInstant(value, clock, { timeOnly: true });
  const contents = (
    <Text
      component='time'
      dateTime={new Date(value!).toISOString()}
      size='sm'
      style={{ fontVariantNumeric: 'tabular-nums' }}
    >
      {compactOnPhone && !dateOnly && !timeOnly ? (
        <>
          <span className={classes.full}>{text}</span>
          <span className={classes.phone}>{short}</span>
        </>
      ) : (
        text
      )}
      {relative ? ` · ${relative}` : ''}
    </Text>
  );
  return (
    <ValueTooltip
      label={`${label ? `${label}: ` : ''}${full} · ${clock.label}`}
      focusable={focusable}
    >
      {stale ? (
        <StatusBadge tone='stale' color='gray' variant='outline'>
          {contents}
        </StatusBadge>
      ) : (
        contents
      )}
    </ValueTooltip>
  );
}
