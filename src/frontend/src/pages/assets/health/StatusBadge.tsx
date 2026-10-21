import { t } from '@lingui/core/macro';
import { Badge, type BadgeProps } from '@mantine/core';
import {
  IconAlertTriangle,
  IconBellCheck,
  IconBellOff,
  IconBellRinging,
  IconBroadcast,
  IconCircleCheck,
  IconClockExclamation,
  IconHistory,
  IconInfoCircle,
  IconUrgent
} from '@tabler/icons-react';

export type StatusTone =
  | 'critical'
  | 'warning'
  | 'info'
  | 'normal'
  | 'neutral'
  | 'stale'
  | 'live';
const icons = {
  critical: IconUrgent,
  warning: IconAlertTriangle,
  info: IconInfoCircle,
  normal: IconCircleCheck,
  stale: IconClockExclamation,
  live: IconBroadcast
};
export function statusTone(state: string): StatusTone {
  return ['critical', 'warning', 'info', 'normal'].includes(state)
    ? (state as StatusTone)
    : state === 'offline'
      ? 'stale'
      : 'neutral';
}
export type StatusBadgeProps = Omit<BadgeProps, 'variant'> & {
  tone: StatusTone;
  variant?: 'filled' | 'outline';
};
/** Semantic status only; statistics and provenance use the neutral tone. */
export function StatusBadge({
  tone,
  children,
  variant = 'outline',
  color = 'gray',
  leftSection,
  ...props
}: StatusBadgeProps) {
  const Icon = tone === 'neutral' ? null : icons[tone];
  const resolved =
    color === 'red' && variant === 'filled'
      ? 'red.9'
      : color === 'yellow' && variant === 'filled'
        ? 'yellow.6'
        : color;
  return (
    <Badge
      variant={variant}
      autoContrast
      color={resolved}
      c={variant === 'outline' ? 'var(--mantine-color-text)' : undefined}
      tt='none'
      h='auto'
      mih={24}
      py={3}
      maw='100%'
      styles={{
        root: { flexShrink: 0 },
        label: { whiteSpace: 'normal', overflowWrap: 'anywhere' }
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
