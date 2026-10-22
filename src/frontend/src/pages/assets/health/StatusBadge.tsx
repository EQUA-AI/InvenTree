import { t } from '@lingui/core/macro';
import { Badge, type BadgeProps } from '@mantine/core';
import {
  IconAlertTriangle,
  IconBellCheck,
  IconBellOff,
  IconBellRinging,
  IconBroadcast,
  IconCircleCheck,
  IconCircleHalf2,
  IconClockExclamation,
  IconHistory,
  IconInfoCircle,
  IconUrgent
} from '@tabler/icons-react';
import tokens from './OperationalTokens.module.css';

export type StatusTone =
  | 'critical'
  | 'warning'
  | 'info'
  | 'normal'
  | 'neutral'
  | 'stale'
  | 'live'
  | 'partial'
  | 'failed';
const icons = {
  critical: IconUrgent,
  warning: IconAlertTriangle,
  info: IconInfoCircle,
  normal: IconCircleCheck,
  stale: IconClockExclamation,
  live: IconBroadcast,
  partial: IconCircleHalf2,
  failed: IconAlertTriangle
};
const filledColors: Partial<Record<string, string>> = {
  red: 'red.9',
  yellow: 'yellow.6',
  gray: 'gray.7',
  green: 'green.6',
  blue: 'blue.8'
};
export function statusTone(state: string): StatusTone {
  return ['critical', 'warning', 'info', 'normal'].includes(state)
    ? (state as StatusTone)
    : state === 'offline'
      ? 'stale'
      : 'neutral';
}
export type StatusBadgeProps = BadgeProps & {
  tone: StatusTone;
  /** Descriptive evidence may wrap; short operational states stay atomic. */
  multiline?: boolean;
};
/** Semantic status only; statistics and provenance use the neutral tone. */
export function StatusBadge({
  tone,
  children,
  variant = 'outline',
  color = 'gray',
  leftSection,
  className,
  multiline = false,
  miw = multiline ? 0 : 'max-content',
  ...props
}: StatusBadgeProps) {
  const warning = tone === 'warning' || String(color).startsWith('yellow');
  const Icon = tone === 'neutral' ? null : icons[tone];
  const resolved =
    variant === 'filled' ? (filledColors[color] ?? color) : color;
  return (
    <Badge
      component='span'
      className={`${tokens.warning}${className ? ` ${className}` : ''}`}
      variant={variant}
      autoContrast
      color={resolved}
      c={
        warning && variant === 'filled'
          ? 'var(--aimms-warning-ink)'
          : variant === 'outline' || variant === 'light'
            ? 'var(--mantine-color-text)'
            : undefined
      }
      tt='none'
      h='auto'
      mih={24}
      py={3}
      maw='100%'
      miw={miw}
      styles={{
        root: {
          flexShrink: 0,
          ...(warning
            ? {
                borderColor: 'var(--aimms-warning)',
                ...(variant === 'filled'
                  ? { backgroundColor: 'var(--aimms-warning)' }
                  : {})
              }
            : {})
        },
        label: {
          whiteSpace: multiline ? 'normal' : 'nowrap',
          overflowWrap: multiline ? 'anywhere' : 'normal',
          overflow: multiline ? 'hidden' : 'visible'
        }
      }}
      leftSection={leftSection ?? (Icon ? <Icon size={14} /> : undefined)}
      data-tone={tone}
      {...props}
    >
      {children}
    </Badge>
  );
}

export function alarmStateVisual(status: string, severity: string) {
  const color =
    severity === 'critical'
      ? 'red'
      : severity === 'warning'
        ? 'yellow'
        : severity === 'info'
          ? 'blue'
          : 'gray';
  switch (status) {
    case 'open':
      return {
        label: t`Active · not acknowledged`,
        color,
        variant: 'filled' as const,
        icon: <IconBellRinging size={14} />
      };
    case 'acknowledged':
      return {
        label: t`Active · acknowledged`,
        color,
        variant: 'outline' as const,
        icon: <IconBellCheck size={14} />
      };
    case 'resolved':
      return {
        label: t`Resolved`,
        color: 'gray',
        variant: 'outline' as const,
        icon: <IconHistory size={14} />
      };
    case 'suppressed':
      return {
        label: t`Dismissed`,
        color: 'gray',
        variant: 'outline' as const,
        icon: <IconBellOff size={14} />
      };
    default:
      return {
        label: t`State unavailable`,
        color: 'gray',
        variant: 'outline' as const,
        icon: <IconInfoCircle size={14} />
      };
  }
}
