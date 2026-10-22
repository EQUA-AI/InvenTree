import type { MachineSignalLimits } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Box,
  Group,
  Paper,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconArrowDownRight,
  IconArrowUpRight,
  IconMinus
} from '@tabler/icons-react';
import {
  MissingValue,
  ValueTooltip
} from '../../../components/items/ValueTooltip';
import { StatusBadge } from '../health/StatusBadge';
import { Timestamp } from '../health/Timestamp';
import { MetaText } from '../health/common';
import { useDisplayClock } from '../health/mimic/observedTime';
import { relativeAge } from '../health/relativeTime';
import monitoring from '../monitoring.module.css';
import classes from './KpiStrip.module.css';
import { performanceSparkline } from './sparkline';

import type {
  HealthState,
  SeriesEntry,
  SignalQuality
} from '@lib/types/MachineHealth';

import {
  HealthStateBadge,
  Unit,
  formatSignalLimits,
  freshnessFacts
} from '../health/common';
import { formatValue } from './format';
import { type Trend, seriesStats, trendOf } from './series';

/**
 * One tile's worth of fact. Built by the panel from a signal and its series;
 * this file only draws.
 */
export interface KpiTile {
  key: string;
  label: string;
  value: number | null;
  /** Text shown instead of a number - a status word, say. */
  valueText?: string;
  denominator?: number;
  unit: string;
  decimals: number;
  observedAt: number | null;
  stale: boolean;
  /** The reading's quality; anything but good is shown as such, not judged. */
  quality?: SignalQuality;
  /** The configured-limit verdict; 'unconfigured' when no limit exists. */
  state: HealthState | 'unconfigured';
  /**
   * Whether the binding has any limit configured. `state` says 'unconfigured'
   * for an unusable reading too, so it cannot answer this on its own.
   */
  limitsConfigured?: boolean;
  limits?: MachineSignalLimits;
  freshnessSeconds?: number;
  /** A derived number - a sum of bays, say: limits cannot apply to it. */
  calculated?: boolean;
  series?: SeriesEntry;
  /** A second line under the value: "Highest: winding 7", say. */
  caption?: string;
  /** Why there is no value - and why the tile is still shown. */
  missingReason?: string;
}

export function Sparkline({
  entry,
  window
}: Readonly<{
  entry: SeriesEntry;
  window?: { start: number; end: number; resolutionSeconds?: number };
}>) {
  const path = performanceSparkline(entry, window);
  if (!path) return null;
  return (
    <Box
      component='svg'
      width='100%'
      height={24}
      viewBox='0 0 96 24'
      preserveAspectRatio='none'
      role='img'
      aria-label={t`Trend over the selected window`}
      style={{ display: 'block' }}
    >
      <path
        d={path}
        fill='none'
        stroke='var(--mantine-color-blue-6)'
        strokeWidth={1.5}
        vectorEffect='non-scaling-stroke'
      />
    </Box>
  );
}
export function TrendMark({ trend }: Readonly<{ trend: Trend | null }>) {
  if (trend == null) return null;
  const label =
    trend === 'up'
      ? t`Rising over the window`
      : trend === 'down'
        ? t`Falling over the window`
        : t`Steady over the window`;
  return (
    <Tooltip label={label} events={{ hover: true, focus: true, touch: false }}>
      <ActionIcon
        variant='transparent'
        className={monitoring.link}
        aria-label={label}
      >
        <span role='img' aria-label={label}>
          {trend === 'up' ? (
            <IconArrowUpRight size={14} />
          ) : trend === 'down' ? (
            <IconArrowDownRight size={14} />
          ) : (
            <IconMinus size={14} />
          )}
        </span>
      </ActionIcon>
    </Tooltip>
  );
}
export function KpiTileCard({
  tile,
  now,
  window
}: Readonly<{
  tile: KpiTile;
  now: number;
  window?: { start: number; end: number; resolutionSeconds?: number };
}>) {
  const clock = useDisplayClock();
  const freshness = freshnessFacts(
    tile.observedAt,
    tile.stale,
    tile.freshnessSeconds,
    now,
    clock
  );
  const age = freshness
    ? relativeAge(
        freshness.future ? 0 : freshness.seconds,
        true,
        tile.stale,
        false
      )
    : undefined;
  const stats = tile.series ? seriesStats(tile.series) : null;
  const missing = tile.value == null && !tile.valueText;
  const shown =
    tile.valueText ??
    formatValue(
      tile.value,
      tile.decimals,
      undefined,
      stats ? stats.max - stats.min : null
    );
  const configuredText = tile.limits
    ? formatSignalLimits({ limits: tile.limits, unit: tile.unit })
    : null;
  return (
    <Paper withBorder p='sm' data-kpi={tile.key} className={classes.card}>
      <Stack gap={6}>
        <Stack gap={4}>
          <MetaText size='xs' fw={500}>
            <ValueTooltip
              focusable={false}
              className={classes.label}
              label={[
                tile.caption ?? tile.label,
                stats
                  ? t`Range ${formatValue(stats.min, tile.decimals, tile.unit)} – ${formatValue(stats.max, tile.decimals, tile.unit)}`
                  : null,
                configuredText
              ]
                .filter(Boolean)
                .join(' · ')}
            >
              {tile.label}
            </ValueTooltip>
          </MetaText>
          {missing ? (
            <Text size='xl' data-kpi-value>
              <MissingValue
                label={tile.label}
                reason={tile.missingReason || t`No usable observation`}
              />
            </Text>
          ) : (
            <Stack gap={2}>
              <Group gap={4} align='baseline'>
                <Text
                  data-kpi-value
                  fz={shown.length > 7 ? 20 : 24}
                  fw={tile.stale ? 400 : 600}
                  lh={1.1}
                  className={tile.stale ? monitoring.meta : undefined}
                  style={{ fontVariantNumeric: 'tabular-nums' }}
                >
                  {!tile.unit && tile.key === 'vibration' ? (
                    <ValueTooltip label={t`Unit not confirmed`}>
                      {shown}
                    </ValueTooltip>
                  ) : (
                    shown
                  )}
                  {tile.denominator != null
                    ? ` ${t`of`} ${tile.denominator}`
                    : ''}
                </Text>
                {!tile.valueText && !!tile.unit && tile.unit !== '1' && (
                  <MetaText component='span' size='sm'>
                    <Unit code={tile.unit} />
                  </MetaText>
                )}
                {!tile.stale && (
                  <TrendMark
                    trend={tile.series ? trendOf(tile.series) : null}
                  />
                )}
              </Group>
              {tile.series && <Sparkline entry={tile.series} window={window} />}
            </Stack>
          )}
        </Stack>
        <Group gap={6} data-kpi-footer>
          {tile.stale && !missing && (
            <ValueTooltip
              label={
                freshness
                  ? `${freshness.serverFlag ? t`Stale (server flag)` : t`Stale`} · ${freshness.budget}${freshness.future && freshness.seconds > 0 ? ` · ${relativeAge(freshness.seconds, false, true, true)}` : ''}`
                  : t`Observation time unavailable`
              }
            >
              <StatusBadge
                tone='stale'
                size='xs'
                styles={{ label: { whiteSpace: 'nowrap' } }}
              >{t`Stale`}</StatusBadge>
            </ValueTooltip>
          )}
          {(!missing || tile.observedAt != null) && (
            <div className={classes.observed} data-kpi-observed>
              <Timestamp
                value={tile.observedAt}
                timeOnly
                relative={age}
                label={freshness?.budget}
              />
            </div>
          )}
          {!tile.stale && !missing && tile.state !== 'unconfigured' && (
            <HealthStateBadge state={tile.state} size='sm' />
          )}
          {!missing && tile.quality && tile.quality !== 'good' && (
            <StatusBadge tone='neutral' color='gray'>
              {tile.quality === 'bad' ? t`Bad quality` : t`Uncertain quality`}
            </StatusBadge>
          )}
        </Group>
      </Stack>
    </Paper>
  );
}
export function KpiStrip({
  tiles,
  now,
  window
}: Readonly<{
  tiles: KpiTile[];
  now: number;
  window?: { start: number; end: number; resolutionSeconds?: number };
}>) {
  if (!tiles.length) return null;
  return (
    <div className={classes.strip} data-testid='kpi-strip'>
      {tiles.map((tile) => (
        <KpiTileCard key={tile.key} tile={tile} now={now} window={window} />
      ))}
    </div>
  );
}
export default KpiStrip;
