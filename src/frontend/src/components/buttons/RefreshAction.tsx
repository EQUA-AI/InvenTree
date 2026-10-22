import { t } from '@lingui/core/macro';
import { ActionIcon, type ActionIconProps, Tooltip } from '@mantine/core';
import { IconRefresh } from '@tabler/icons-react';
import type { MouseEventHandler } from 'react';
import classes from './RefreshAction.module.css';

/** Shared read-only refresh affordance for AIMMS operational panels. */
export function RefreshAction({
  onClick,
  ...props
}: Pick<ActionIconProps, 'loading' | 'visibleFrom' | 'hiddenFrom'> & {
  onClick: MouseEventHandler<HTMLButtonElement>;
}) {
  return (
    <Tooltip
      label={t`Refresh`}
      events={{ hover: true, focus: true, touch: true }}
    >
      <ActionIcon
        {...props}
        variant='default'
        size={36}
        className={classes.refresh}
        aria-label={t`Refresh`}
        onClick={onClick}
      >
        <IconRefresh size={18} />
      </ActionIcon>
    </Tooltip>
  );
}
