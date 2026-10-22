import { Tooltip, UnstyledButton } from '@mantine/core';
import classes from './MissingValue.module.css';

/** A missing fact stays quiet, with its cause available to pointer and keyboard. */
export function MissingValue({
  reason,
  label
}: Readonly<{ reason: string; label?: string }>) {
  const explanation = label ? `${label}: ${reason}` : reason;
  return (
    <Tooltip
      label={explanation}
      events={{ hover: true, focus: true, touch: true }}
    >
      <UnstyledButton aria-label={explanation} className={classes.value}>
        —
      </UnstyledButton>
    </Tooltip>
  );
}
