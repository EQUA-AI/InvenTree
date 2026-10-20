import { t } from '@lingui/core/macro';
import { Badge, type BadgeProps } from '@mantine/core';
import {
  IconBellCheck,
  IconBellOff,
  IconBellRinging,
  IconHistory,
  IconInfoCircle
} from '@tabler/icons-react';

/** Shared monitoring badge: text contrast does not depend on the severity hue. */
export function StatusBadge({
  children,
  leftSection,
  color = 'gray',
  variant = 'outline',
  ...props
}: BadgeProps) {
  return (
    <Badge
      {...props}
      variant={variant === 'filled' ? 'filled' : 'outline'}
      autoContrast
      color={variant === 'filled' ? `${color}.9` : color}
      c={
        variant === 'filled'
          ? color === 'green'
            ? 'black'
            : undefined
          : 'var(--mantine-color-text)'
      }
      tt='none'
      h='auto'
      mih={24}
      py={3}
      maw='100%'
      styles={{
        root: { flexShrink: 0 },
        label: { whiteSpace: 'normal', overflowWrap: 'anywhere' }
      }}
      leftSection={leftSection ?? <IconInfoCircle size={14} />}
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
