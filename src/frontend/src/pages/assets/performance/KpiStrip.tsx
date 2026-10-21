import type { MachineSignalLimits } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Box,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconArrowDownRight,
  IconArrowUpRight,
  IconMinus
} from '@tabler/icons-react';
import { StatusBadge } from '../health/StatusBadge';
import { MetaText } from '../health/common';
import monitoring from '../monitoring.module.css';
import { performanceSparkline } from './sparkline';

import type {
  HealthState,
  SeriesEntry,
  SignalQuality
} from '@lib/types/MachineHealth';

import {
  FreshnessText,
  HealthStateBadge,
  Unit,
  formatSignalLimits
} from '../health/common';
import { formatRange, formatValue } from './format';
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
    <Paper withBorder p='sm' data-kpi={tile.key} h='100%'>
      <Stack gap={6} h='100%' justify='space-between'>
        <Stack gap={4}>
          <MetaText size='xs' fw={500}>
            {tile.label}
          </MetaText>
          {missing ? (
            <MetaText>
              {tile.missingReason || t`Unavailable · no usable observation`}
            </MetaText>
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
                  {tile.stale ? t`Last known ` : ''}
                  {shown}
                </Text>
                {!tile.valueText && (
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
          {tile.caption && <MetaText size='xs'>{tile.caption}</MetaText>}
        </Stack>
        <Stack gap={4} data-kpi-footer>
          {stats && (
            <MetaText size='xs'>
              {t`Window`}{' '}
              {formatRange(stats.min, stats.max, tile.decimals, tile.unit)}
            </MetaText>
          )}
          {configuredText ? (
            <MetaText size='xs'>
              {configuredText} · {t`on signal mapping`}
            </MetaText>
          ) : (
            !tile.calculated &&
            !tile.valueText && (
              <MetaText size='xs'>
                {tile.limitsConfigured
                  ? t`Limits apply to other family channels; see their signal mappings.`
                  : t`no limit configured`}
              </MetaText>
            )
          )}
          {(!missing || tile.observedAt != null) && (
            <FreshnessText
              observedAt={tile.observedAt}
              stale={tile.stale}
              thresholdSeconds={tile.freshnessSeconds}
              now={now}
            />
          )}
          {!tile.stale && !missing && tile.state !== 'unconfigured' && (
            <HealthStateBadge state={tile.state} size='sm' />
          )}
          {!missing && tile.quality && tile.quality !== 'good' && (
            <StatusBadge tone='neutral' color='gray'>
              {tile.quality === 'bad' ? t`Bad quality` : t`Uncertain quality`}
            </StatusBadge>
          )}
        </Stack>
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
    <SimpleGrid
      type='container'
      cols={{ base: 2, '560px': 3, '760px': 4, '1140px': 8 }}
      spacing='sm'
      data-testid='kpi-strip'
    >
      {tiles.map((tile) => (
        <KpiTileCard key={tile.key} tile={tile} now={now} window={window} />
      ))}
    </SimpleGrid>
  );
}
export default KpiStrip;
