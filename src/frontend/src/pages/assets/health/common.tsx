import { plural, t } from '@lingui/core/macro';
import { Badge, type BadgeProps, Group, Text } from '@mantine/core';
import {
  IconAlertTriangle,
  IconBellCheck,
  IconBellOff,
  IconBellRinging,
  IconCircleCheck,
  IconHelpCircle,
  IconHistory,
  IconInfoCircle,
  IconPlugConnectedX,
  IconUrgent
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import type { ReactNode } from 'react';

import operations from '../operations.module.css';
import { displayClock, formatInstant } from './mimic/observedTime';

import type {
  AnomalySeverity,
  HealthSourceType,
  HealthState,
  MachineSignal,
  SignalQuality
} from '@lib/types/MachineHealth';

dayjs.extend(relativeTime);

/**
 * Shared presentation for health state, severity, quality and freshness.
 *
 * Every indicator pairs an icon and a word with its colour. Colour alone is not
 * an accessible severity cue, and a control-room screen is exactly where a
 * colour-blind or glare-washed reader must still be able to tell critical from
 * normal.
 */

interface StateVisual {
  color: string;
  label: string;
  icon: ReactNode;
}

export function healthStateVisual(state: HealthState): StateVisual {
  switch (state) {
    case 'normal':
      return {
        color: 'green',
        label: t`Normal`,
        icon: <IconCircleCheck size={16} />
      };
    case 'warning':
      return {
        color: 'yellow',
        label: t`Warning`,
        icon: <IconAlertTriangle size={16} />
      };
    case 'critical':
      return {
        color: 'red',
        label: t`Critical`,
        icon: <IconUrgent size={16} />
      };
    case 'offline':
      return {
        color: 'gray',
        label: t`Offline`,
        icon: <IconPlugConnectedX size={16} />
      };
    default:
      return {
        color: 'gray',
        label: t`Unknown`,
        icon: <IconHelpCircle size={16} />
      };
  }
}

export function severityVisual(
  severity: string | null | undefined
): StateVisual {
  switch (severity) {
    case 'critical':
      return {
        color: 'red',
        label: t`Critical`,
        icon: <IconUrgent size={16} />
      };
    case 'warning':
      return {
        color: 'yellow',
        label: t`Warning`,
        icon: <IconAlertTriangle size={16} />
      };
    case 'info':
      return {
        color: 'blue',
        label: t`Info`,
        icon: <IconInfoCircle size={16} />
      };
    default:
      return {
        color: 'gray',
        label: t`Severity unavailable`,
        icon: <IconHelpCircle size={16} />
      };
  }
}

/** Shared badge contrast and vocabulary; palette remains native Mantine. */
export function StatusBadge({
  children,
  variant = 'light',
  leftSection,
  color = 'gray',
  maw = 'none',
  miw = 'max-content',
  ...props
}: BadgeProps & { children: ReactNode; 'data-alarm-status'?: string }) {
  return (
    <Badge
      {...props}
      variant={variant}
      color={
        variant === 'filled' && !color.includes('.') ? `${color}.9` : color
      }
      autoContrast={variant === 'filled'}
      c={variant === 'filled' ? undefined : 'var(--mantine-color-text)'}
      tt='none'
      maw={maw}
      miw={miw}
      leftSection={leftSection ?? <IconInfoCircle size={14} />}
    >
      {children}
    </Badge>
  );
}
export function alarmStateVisual(
  status: string | null,
  severity?: string | null
) {
  const color = severityVisual(severity).color;
  switch (status) {
    case 'open':
      return {
        label: t`Active · not acknowledged`,
        color: severity === 'critical' ? 'red.9' : color,
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
        label: t`Alarm state unavailable`,
        color: 'gray',
        variant: 'outline' as const,
        icon: <IconHelpCircle size={14} />
      };
  }
}
export function AlarmStateBadge({
  status,
  severity
}: { status: string | null; severity?: string | null }) {
  const visual = alarmStateVisual(status, severity);
  return (
    <StatusBadge
      data-alarm-status={status ?? 'unknown'}
      color={visual.color}
      variant={visual.variant}
      leftSection={visual.icon}
    >
      {visual.label}
    </StatusBadge>
  );
}
export function Unavailable({ reason }: { reason: string }) {
  return (
    <Text size='sm'>
      {t`Unavailable`} · {reason}
    </Text>
  );
}

export function HealthStateBadge({
  state,
  size = 'md'
}: Readonly<{ state: HealthState; size?: string }>) {
  const visual = healthStateVisual(state);
  return (
    <StatusBadge
      flex='0 0 auto'
      maw='none'
      miw='max-content'
      color={visual.color}
      variant='light'
      size={size}
      leftSection={visual.icon}
    >
      {visual.label}
    </StatusBadge>
  );
}

export function SeverityBadge({
  severity
}: Readonly<{ severity: AnomalySeverity | string | null }>) {
  const visual = severityVisual(severity);
  return (
    <StatusBadge color={visual.color} variant='light' leftSection={visual.icon}>
      {visual.label}
    </StatusBadge>
  );
}

export function qualityLabel(quality: SignalQuality): string {
  switch (quality) {
    case 'good':
      return t`Good`;
    case 'uncertain':
      return t`Uncertain`;
    case 'bad':
      return t`Bad`;
    default:
      return t`Unknown`;
  }
}

const SOURCE_TYPE_LABELS: Record<HealthSourceType, () => string> = {
  iot: () => t`IoT`,
  scada: () => t`SCADA`,
  plc: () => t`PLC`,
  dcs: () => t`DCS`,
  mes: () => t`MES`,
  bas_bms: () => t`BAS / BMS`,
  ems: () => t`EMS`,
  iiot: () => t`IIoT`,
  historian: () => t`Historian`,
  webhook: () => t`Webhook`,
  manual: () => t`Manual`
};

export function sourceTypeLabel(type: HealthSourceType | null): string {
  if (!type) {
    return '';
  }
  return SOURCE_TYPE_LABELS[type]?.() ?? type;
}

export function SourceTypeBadge({
  type
}: Readonly<{ type: HealthSourceType | null }>) {
  if (!type) {
    return null;
  }
  return (
    <StatusBadge variant='outline' color='gray'>
      {sourceTypeLabel(type)}
    </StatusBadge>
  );
}

/**
 * Render an observation time with how long ago it was.
 *
 * Freshness is never implied: a stale reading says so in words next to the
 * timestamp, so nobody reads an hours-old number as the machine's current state.
 */
export function ObservedAt({
  observedAt,
  stale
}: Readonly<{ observedAt: string | null; stale: boolean }>) {
  if (!observedAt) {
    return (
      <Text size='sm' className={operations.meta}>
        {t`No reading`}
      </Text>
    );
  }

  return (
    <Group
      gap={6}
      wrap='wrap'
      title={formatInstant(observedAt, displayClock()) ?? t`Time unavailable`}
    >
      <Text size='sm'>
        {formatInstant(observedAt, displayClock()) ?? t`Time unavailable`}
      </Text>
      <Text size='xs' className={operations.meta}>
        {stale ? t`Stale` : dayjs(observedAt).fromNow()}
      </Text>
    </Group>
  );
}

/** Bounds follow the native classifier: a minimum is breached below, a maximum above. */
export function formatSignalLimits(signal: MachineSignal): string | null {
  const unit = signal.unit ? ` ${signal.unit}` : '';
  const parts: string[] = [];
  for (const [label, min, max] of [
    [t`Normal`, signal.limits.normal_min, signal.limits.normal_max],
    [t`Warning`, signal.limits.warn_min, signal.limits.warn_max],
    [t`Critical`, signal.limits.critical_min, signal.limits.critical_max]
  ] as const) {
    if (label === t`Normal`) {
      if (min !== null) parts.push(t`Normal minimum ${min}${unit}`);
      if (max !== null) parts.push(t`Normal maximum ${max}${unit}`);
    } else if (min !== null && max !== null) {
      parts.push(t`${label} below ${min}${unit} or above ${max}${unit}`);
    } else if (min !== null) {
      parts.push(t`${label} below ${min}${unit}`);
    } else if (max !== null) {
      parts.push(t`${label} above ${max}${unit}`);
    }
  }
  return parts.length ? parts.join(' · ') : null;
}

/** An unassociated machine anomaly cannot be ruled out for this signal. */
export function unassociatedAnomalyText(count: number): string {
  return plural(count, {
    one: '# active machine anomaly does not name its signals; this signal may be affected.',
    other:
      '# active machine anomalies do not name their signals; this signal may be affected.'
  });
}
