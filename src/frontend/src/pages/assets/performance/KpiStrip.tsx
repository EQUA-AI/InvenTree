import type { MachineSignalLimits } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Box, Group, Paper, Stack, Text } from '@mantine/core';
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
import { tileIsStale } from './liveState';
import { endpointDelta, performanceSparkline } from './sparkline';

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
import { formatDuration, formatInstant, formatValue } from './format';
import { seriesStats } from './series';

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
export function KpiTileCard({
  tile,
  now,
  readAt,
  window
}: Readonly<{
  tile: KpiTile;
  now: number;
  readAt?: number | null;
  window?: { start: number; end: number; resolutionSeconds?: number };
}>) {
  const clock = useDisplayClock();
  const stale = tileIsStale(tile, now);
  const freshness = freshnessFacts(
    tile.observedAt,
    stale,
    tile.freshnessSeconds,
    now,
    clock
  );
  const age = freshness
    ? relativeAge(
        freshness.seconds,
        true,
        stale,
        freshness.future && freshness.seconds >= 1
      )
    : undefined;
  const stats = tile.series ? seriesStats(tile.series) : null;
  const delta = tile.series ? endpointDelta(tile.series, window) : null;
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
                  fw={stale ? 400 : 600}
                  lh={1.1}
                  className={stale ? monitoring.meta : undefined}
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
              </Group>
              {tile.series && <Sparkline entry={tile.series} window={window} />}
              {delta && (
                <MetaText size='xs' data-kpi-delta>
                  {`Δ ${delta.change > 0 ? '+' : ''}${formatValue(delta.change, 1, delta.percent ? '%' : tile.unit)} / ${formatDuration(delta.seconds)}`}
                </MetaText>
              )}
            </Stack>
          )}
        </Stack>
        <Group gap={6} data-kpi-footer>
          {stale && !missing && (
            <ValueTooltip
              label={
                freshness
                  ? [
                      tile.stale ? t`Source flagged stale` : t`Stale`,
                      t`Observed ${formatInstant(tile.observedAt)}`,
                      readAt != null ? t`Read ${formatInstant(readAt)}` : null,
                      freshness.budget
                    ]
                      .filter(Boolean)
                      .join(' · ')
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
          {!stale && !missing && tile.state !== 'unconfigured' && (
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
  readAt,
  window
}: Readonly<{
  tiles: KpiTile[];
  now: number;
  readAt?: number | null;
  window?: { start: number; end: number; resolutionSeconds?: number };
}>) {
  if (!tiles.length) return null;
  return (
    <div className={classes.strip} data-testid='kpi-strip'>
      {tiles.map((tile) => (
        <KpiTileCard
          key={tile.key}
          tile={tile}
          now={now}
          readAt={readAt}
          window={window}
        />
      ))}
    </div>
  );
}
export default KpiStrip;
