import { t } from '@lingui/core/macro';
import { Group, Text, Tooltip, UnstyledButton } from '@mantine/core';
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react';
import { StatusBadge } from '../../pages/assets/health/StatusBadge';
import classes from './ValueTooltip.module.css';

type ValueTooltipProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  children: ReactNode;
  accessibleLabel?: string;
  focusable?: boolean;
};

export const factTooltipProps = {
  multiline: true,
  maw: 360,
  withArrow: true,
  interactive: true,
  closeDelay: 150,
  style: { whiteSpace: 'pre-line' },
  events: { hover: true, focus: true, touch: true }
} as const;

/** Keeps the visible value's name; the tooltip describes it on hover or focus. */
export function ValueTooltip({
  label,
  children,
  accessibleLabel,
  focusable = true,
  className,
  ...props
}: Readonly<ValueTooltipProps>) {
  return (
    <Tooltip
      label={label}
      {...factTooltipProps}
      events={{ hover: true, focus: true, touch: !props.onClick }}
    >
      {!focusable && !props.onClick ? (
        <span
          {...(props as HTMLAttributes<HTMLSpanElement>)}
          className={`${classes.target}${className ? ` ${className}` : ''}`}
        >
          {children}
        </span>
      ) : (
        <UnstyledButton
          type='button'
          {...props}
          tabIndex={focusable ? props.tabIndex : -1}
          aria-label={accessibleLabel ?? props['aria-label']}
          className={`${classes.target}${className ? ` ${className}` : ''}`}
        >
          {children}
        </UnstyledButton>
      )}
    </Tooltip>
  );
}

/** A missing fact is a plain dash; its reason is available without adding prose. */
export function MissingValue({
  reason,
  label,
  focusable = true
}: Readonly<{ reason: string; label?: string; focusable?: boolean }>) {
  const detail = reason.trim() || t`Reason not supplied`;
  const explanation = label ? `${label}: ${detail}` : detail;
  return (
    <ValueTooltip
      label={explanation}
      accessibleLabel={explanation}
      focusable={focusable}
    >
      —
    </ValueTooltip>
  );
}

/** A failed read remains visible and never discards a supplied last value. */
export function FailedValue({
  reason,
  value,
  label,
  focusable = true
}: Readonly<{
  reason: string;
  value?: ReactNode;
  label?: string;
  focusable?: boolean;
}>) {
  const hasValue = value !== null && value !== undefined;
  const status = hasValue ? t`Bad quality` : t`Read failed`;
  const detail = reason.trim() || t`Reason not supplied`;
  return (
    <Group component='span' gap={6} wrap='nowrap'>
      {hasValue && (
        <Text component='span' inherit>
          {value}
        </Text>
      )}
      <ValueTooltip
        label={detail}
        accessibleLabel={label ? `${label}: ${status}` : undefined}
        focusable={focusable}
      >
        <StatusBadge tone='failed' color='red' variant='outline' size='sm'>
          {status}
        </StatusBadge>
      </ValueTooltip>
    </Group>
  );
}
