import { t } from '@lingui/core/macro';
import { Badge, Group, Text } from '@mantine/core';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconHelpCircle,
  IconPlugConnectedX,
  IconUrgent
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import type { ReactNode } from 'react';

import type {
  AnomalySeverity,
  HealthSourceType,
  HealthState,
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
    default:
      return {
        color: 'blue',
        label: t`Info`,
        icon: <IconHelpCircle size={16} />
      };
  }
}

export function HealthStateBadge({
  state,
  size = 'md'
}: Readonly<{ state: HealthState; size?: string }>) {
  const visual = healthStateVisual(state);
  return (
    <Badge
      color={visual.color}
      variant='light'
      size={size}
      leftSection={visual.icon}
    >
      {visual.label}
    </Badge>
  );
}

export function SeverityBadge({
  severity
}: Readonly<{ severity: AnomalySeverity }>) {
  const visual = severityVisual(severity);
  return (
    <Badge color={visual.color} variant='light' leftSection={visual.icon}>
      {visual.label}
    </Badge>
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
    <Badge variant='outline' color='gray'>
      {sourceTypeLabel(type)}
    </Badge>
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
      <Text size='sm' c='dimmed'>
        {t`No reading`}
      </Text>
    );
  }

  return (
    <Group gap={6} wrap='nowrap'>
      <Text size='sm'>{dayjs(observedAt).format('MMM D, HH:mm')}</Text>
      <Text size='xs' c={stale ? undefined : 'dimmed'}>
        {stale ? t`Stale` : dayjs(observedAt).fromNow()}
      </Text>
    </Group>
  );
}
