import { plural, t } from '@lingui/core/macro';
import {
  Alert,
  type AlertProps,
  Anchor,
  type AnchorProps,
  Group,
  Text,
  type TextProps,
  createPolymorphicComponent
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconClockOff,
  IconHelpCircle,
  IconUrgent
} from '@tabler/icons-react';
import { type ReactNode, forwardRef } from 'react';

import type {
  AnomalySeverity,
  HealthSourceType,
  HealthState,
  MachineSignal,
  SignalQuality
} from '@lib/types/MachineHealth';

import { displayInstant } from '../displayClock';
import monitoring from '../monitoring.module.css';
import { unitLabel } from '../performance/format';
import { useNow } from '../useNow';
import { StatusBadge, statusTone } from './StatusBadge';

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

export function severityVisual(severity: AnomalySeverity): StateVisual {
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
        icon: <IconHelpCircle size={16} />
      };
    default:
      return {
        color: 'gray',
        label: t`Severity unavailable`,
        icon: <IconHelpCircle size={16} />
      };
  }
}

export function HealthStateBadge({
  state,
  size = 'md',
  neutralNormal = false,
  describedBy
}: Readonly<{
  state: HealthState;
  size?: string;
  neutralNormal?: boolean;
  describedBy?: string;
}>) {
  const visual = healthStateVisual(state);
  return (
    <StatusBadge
      tone={state === 'normal' && neutralNormal ? 'neutral' : statusTone(state)}
      aria-describedby={describedBy}
      style={{ flexShrink: 0, maxWidth: 'none' }}
      color={
        state === 'normal' && neutralNormal
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
      {visual.label}
    </StatusBadge>
  );
}

export function SeverityBadge({
  severity
}: Readonly<{ severity: AnomalySeverity }>) {
  const visual = severityVisual(severity);
  return (
    <StatusBadge
      tone={statusTone(severity)}
      color={visual.color}
      variant={
        severity === 'critical' || severity === 'warning' ? 'filled' : 'outline'
      }
    >
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
    <StatusBadge tone='neutral' variant='outline' color='gray'>
      {sourceTypeLabel(type)}
    </StatusBadge>
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
  now: number
) {
  const time = displayInstant(observedAt);
  if (!time) return null;
  const ms =
    typeof observedAt === 'number' ? observedAt : Date.parse(observedAt!);
  const future = ms > now;
  const seconds = Math.floor(Math.abs(now - ms) / 1000);
  const age =
    seconds < 60
      ? `${seconds} s`
      : seconds < 86400
        ? `${Math.floor(seconds / 60)} min`
        : `${Math.floor(seconds / 86400)} d`;
  const budget =
    thresholdSeconds == null
      ? 'budget not supplied'
      : `freshness budget ${thresholdSeconds} s`;
  const serverFlag =
    stale &&
    (future || (thresholdSeconds != null && seconds <= thresholdSeconds));
  return { time, age, budget, serverFlag, future };
}
export function FreshnessText({
  observedAt,
  stale,
  thresholdSeconds,
  now: suppliedNow
}: Readonly<{
  observedAt: string | number | null;
  stale: boolean;
  thresholdSeconds?: number | null;
  now?: number;
}>) {
  const pageNow = useNow(1000, suppliedNow == null);
  const fact = freshnessFacts(
    observedAt,
    stale,
    thresholdSeconds,
    suppliedNow ?? pageNow
  );
  if (!fact) return <MetaText>{t`Observation time unavailable`}</MetaText>;
  return (
    <Group gap={4} style={{ fontVariantNumeric: 'tabular-nums' }}>
      {stale ? (
        <StatusBadge tone='stale' color='gray'>
          {fact.serverFlag ? t`Stale (server flag)` : t`Stale`} ·{' '}
          {fact.future
            ? t`${fact.age} ahead of your clock`
            : t`${fact.age} old`}{' '}
          · {fact.budget}
        </StatusBadge>
      ) : (
        <MetaText>
          {fact.time} ·{' '}
          {fact.future
            ? t`${fact.age} ahead of your clock`
            : t`${fact.age} old`}
        </MetaText>
      )}
    </Group>
  );
}
/** Kept for native callers outside this pass; new code uses FreshnessText. */
export const ObservedAt = FreshnessText;

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
