import { plural, t } from '@lingui/core/macro';
import {
  Alert,
  type AlertProps,
  Anchor,
  type AnchorProps,
  Text,
  type TextProps,
  createPolymorphicComponent
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconBellCheck,
  IconBellOff,
  IconBellRinging,
  IconCircleCheck,
  IconClockOff,
  IconHelpCircle,
  IconHistory,
  IconInfoCircle,
  IconUrgent
} from '@tabler/icons-react';
import { type ReactNode, forwardRef } from 'react';
import { ageDuration, relativeAge } from './relativeTime';

import type {
  AnomalySeverity,
  HealthSourceType,
  HealthState,
  MachineSignal,
  SignalQuality
} from '@lib/types/MachineHealth';

import monitoring from '../monitoring.module.css';
import { unitLabel } from '../performance/format';
import { useNow } from '../useNow';
import {
  StatusBadge as MonitoringStatusBadge,
  type StatusBadgeProps,
  statusTone
} from './StatusBadge';
import { Timestamp } from './Timestamp';
import {
  type DisplayClock,
  displayClock,
  formatInstant,
  useDisplayClock
} from './mimic/observedTime';

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
        label: t`No current data`,
        icon: <IconClockOff size={16} />
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
  variant = 'outline',
  leftSection,
  color = 'gray',
  maw = '100%',
  multiline = false,
  miw = multiline ? 0 : 'max-content',
  ...props
}: Omit<StatusBadgeProps, 'tone'> & {
  children: ReactNode;
  'data-alarm-status'?: string;
}) {
  return (
    <MonitoringStatusBadge
      tone='neutral'
      {...props}
      variant={variant}
      color={color}
      maw={maw}
      miw={miw}
      multiline={multiline}
      leftSection={leftSection}
    >
      {children}
    </MonitoringStatusBadge>
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
      size='sm'
      maw='none'
      h={24}
      styles={{
        root: {
          flexShrink: 0,
          ...(visual.color === 'yellow'
            ? {
                borderColor: 'var(--aimms-warning)',
                ...(visual.variant === 'filled'
                  ? { backgroundColor: 'var(--aimms-warning)' }
                  : {})
              }
            : {})
        },
        label: {
          whiteSpace: 'nowrap',
          overflowWrap: 'normal',
          overflow: 'visible'
        }
      }}
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
  size = 'md',
  neutralNormal = false,
  partial = false,
  coverageUnknown = false,
  compactCoverage = false,
  coverage,
  describedBy
}: Readonly<{
  state: HealthState;
  size?: string;
  neutralNormal?: boolean;
  partial?: boolean;
  coverageUnknown?: boolean;
  compactCoverage?: boolean;
  coverage?: { evaluated: number; mapped: number } | null;
  describedBy?: string;
}>) {
  const visual = healthStateVisual(state);
  const limited =
    state === 'normal' &&
    (coverageUnknown ||
      partial ||
      neutralNormal ||
      (!!coverage && coverage.evaluated < coverage.mapped));
  const label =
    limited && !compactCoverage
      ? coverageUnknown
        ? t`Normal · coverage unknown`
        : coverage
          ? t`Normal · ${coverage.evaluated}/${coverage.mapped}`
          : t`Normal · partial`
      : visual.label;
  return (
    <MonitoringStatusBadge
      tone={limited ? 'partial' : statusTone(state)}
      aria-describedby={describedBy}
      style={{ flexShrink: 0 }}
      maw='100%'
      miw={0}
      color={
        limited
          ? 'gray'
          : state === 'critical'
            ? 'red.9'
            : state === 'warning'
              ? 'yellow.6'
              : visual.color
      }
      variant={
        state === 'critical' || state === 'warning' ? 'filled' : 'outline'
      }
      size={size}
      leftSection={state === 'offline' ? visual.icon : undefined}
    >
      {label}
    </MonitoringStatusBadge>
  );
}

export function SeverityBadge({
  severity
}: Readonly<{ severity: AnomalySeverity | string | null }>) {
  const visual = severityVisual(severity);
  return (
    <MonitoringStatusBadge
      tone={statusTone(severity ?? '')}
      leftSection={visual.icon}
      color={visual.color}
      variant={
        severity === 'critical' || severity === 'warning' ? 'filled' : 'outline'
      }
    >
      {visual.label}
    </MonitoringStatusBadge>
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
    <MonitoringStatusBadge tone='neutral' variant='outline' color='gray'>
      {sourceTypeLabel(type)}
    </MonitoringStatusBadge>
  );
}

export const MetaText = createPolymorphicComponent<'p', TextProps>(
  forwardRef<HTMLParagraphElement, TextProps>(
    ({ className, ...props }, ref) => (
      <Text
        ref={ref}
        size='sm'
        maw='70ch'
        className={[monitoring.meta, className].filter(Boolean).join(' ')}
        {...props}
      />
    )
  )
);
export const MonitoringLink = createPolymorphicComponent<'a', AnchorProps>(
  forwardRef<HTMLAnchorElement, AnchorProps>(({ className, ...props }, ref) => (
    <Anchor
      ref={ref}
      className={[monitoring.link, className].filter(Boolean).join(' ')}
      {...props}
    />
  ))
);
export function AttentionAlert(props: AlertProps) {
  return (
    <Alert
      variant='outline'
      color='yellow'
      icon={<IconAlertTriangle size={16} />}
      maw='70ch'
      c='var(--mantine-color-text)'
      {...props}
      classNames={{ label: monitoring.attentionTitle }}
    />
  );
}
export function Unit({ code }: Readonly<{ code: string | null | undefined }>) {
  return (
    <abbr title={code || undefined} style={{ textDecoration: 'none' }}>
      {unitLabel(code)}
    </abbr>
  );
}
/** Age is judged against the same observation and budget as the server flag. */
export function freshnessFacts(
  observedAt: string | number | null,
  stale: boolean,
  thresholdSeconds: number | null | undefined,
  now: number,
  clock: DisplayClock = displayClock()
) {
  const time =
    typeof observedAt === 'number' && observedAt <= 0
      ? null
      : formatInstant(observedAt, clock);
  if (!time) return null;
  const ms =
    typeof observedAt === 'number' ? observedAt : Date.parse(observedAt!);
  const future = ms > now;
  const seconds = Math.floor(Math.abs(now - ms) / 1000);
  const age = ageDuration(seconds);
  const budget =
    thresholdSeconds == null
      ? 'budget not supplied'
      : `freshness budget ${thresholdSeconds} s`;
  const serverFlag =
    stale &&
    (future || (thresholdSeconds != null && seconds <= thresholdSeconds));
  return { time, age, seconds, budget, serverFlag, future };
}
export function FreshnessText({
  observedAt,
  stale,
  thresholdSeconds,
  now: suppliedNow,
  preferJustNow = false,
  compactOnPhone = false,
  relativeOnly = false,
  label = t`Observed`,
  focusable = relativeOnly
}: Readonly<{
  observedAt: string | number | null;
  stale: boolean;
  thresholdSeconds?: number | null;
  now?: number;
  preferJustNow?: boolean;
  compactOnPhone?: boolean;
  relativeOnly?: boolean;
  label?: string;
  focusable?: boolean;
}>) {
  const pageNow = useNow(1000, suppliedNow == null);
  const clock = useDisplayClock();
  const fact = freshnessFacts(
    observedAt,
    stale,
    thresholdSeconds,
    suppliedNow ?? pageNow,
    clock
  );
  const age = fact
    ? relativeAge(fact.seconds, preferJustNow, stale, fact.future)
    : undefined;
  const relative =
    fact && stale
      ? `${fact.serverFlag ? t`Stale (server flag)` : t`Stale`} · ${age} · ${fact.budget}`
      : age;
  return (
    <Timestamp
      value={observedAt}
      clock={clock}
      relative={relativeOnly ? age : relative}
      relativeOnly={relativeOnly}
      stale={stale && !!fact}
      compactOnPhone={compactOnPhone}
      label={label}
      focusable={focusable}
    />
  );
}

/**
 * Render an observation time with how long ago it was.
 *
 * Freshness is never implied: a stale reading says so in words next to the
 * timestamp, so nobody reads an hours-old number as the machine's current state.
 */
export function ObservedAt(props: Parameters<typeof FreshnessText>[0]) {
  return <FreshnessText {...props} />;
}

/** Bounds follow the native classifier: a minimum is breached below, a maximum above. */
export function formatSignalLimits(
  signal: Pick<MachineSignal, 'limits' | 'unit'>
): string | null {
  const unit = ` ${unitLabel(signal.unit)}`;
  const parts: string[] = [];
  for (const [label, min, max] of [
    [t`Normal`, signal.limits.normal_min, signal.limits.normal_max],
    [t`Warning`, signal.limits.warn_min, signal.limits.warn_max],
    [t`Critical`, signal.limits.critical_min, signal.limits.critical_max]
  ] as const) {
    if (label === t`Normal`) {
      if (min != null) parts.push(t`Normal minimum ${min}${unit}`);
      if (max != null) parts.push(t`Normal maximum ${max}${unit}`);
    } else if (min != null && max != null) {
      parts.push(t`${label} below ${min}${unit} or above ${max}${unit}`);
    } else if (min != null) {
      parts.push(t`${label} below ${min}${unit}`);
    } else if (max != null) {
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
