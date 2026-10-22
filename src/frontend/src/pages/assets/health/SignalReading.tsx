import type { MachineSignal } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Text, Tooltip } from '@mantine/core';
import type { ReactNode } from 'react';
import classes from './SignalTable.module.css';
import { signalReading } from './readingPresentation';

/** The reason is available on hover, keyboard focus and to assistive tech. */
export function SignalTooltip({
  label,
  children = '—'
}: Readonly<{ label: string; children?: ReactNode }>) {
  return (
    <Tooltip
      label={label}
      multiline
      maw={320}
      withArrow
      events={{ hover: true, focus: true, touch: true }}
    >
      <Text
        component='span'
        role='img'
        aria-label={label}
        data-signal-tooltip
        tabIndex={0}
        size='sm'
        className={classes.tooltipTarget}
      >
        {children}
      </Text>
    </Tooltip>
  );
}

export function SignalValue({
  signal
}: Readonly<{ signal: Pick<MachineSignal, 'value' | 'unit' | 'stale'> }>) {
  const reading = signalReading(signal);
  if (reading.reason) return <SignalTooltip label={reading.reason} />;
  return (
    <>
      {signal.stale ? t`Last known ` : ''}
      {reading.text}
    </>
  );
}
