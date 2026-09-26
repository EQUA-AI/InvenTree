import { t } from '@lingui/core/macro';
import {
  Badge,
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

import type {
  HealthState,
  SeriesEntry,
  SignalQuality
} from '@lib/types/MachineHealth';

import { HealthStateBadge } from '../health/common';
import { formatAge, formatClockOn, formatRange, formatValue } from './format';
import { type SeriesStats, type Trend, seriesStats, trendOf } from './series';

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
  /** A derived number - a sum of bays, say: limits cannot apply to it. */
  calculated?: boolean;
  series?: SeriesEntry;
  /** A second line under the value: "Highest: winding 7", say. */
  caption?: string;
  /** Why there is no value - and why the tile is still shown. */
  missingReason?: string;
}

const SPARK_W = 96;
const SPARK_H = 24;
//: Half the stroke width, so the line is inside the box it is drawn in.
const SPARK_PAD = 1;

export function Sparkline({ entry }: Readonly<{ entry: SeriesEntry }>) {
  const points = entry.samples
    .map((s) => s.v)
    .filter((v): v is number => v !== null && Number.isFinite(v));
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min;
  const step = SPARK_W / (points.length - 1);
  // Inset by half the stroke so a peak or a dip is drawn whole: pinned to
  // the viewBox edge, the svg's own clipping shaves it flat. A series that
  // never moved is drawn down the middle rather than along the bottom, where
  // a full-width rule reads as a divider between the value and its caption
  // instead of as a flat line; the tile's "Window" caption says it is flat.
  const path = points
    .map((v, i) => {
      const x = i * step;
      const y =
        span === 0
          ? SPARK_H / 2
          : SPARK_H -
            SPARK_PAD -
            ((v - min) / span) * (SPARK_H - 2 * SPARK_PAD);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <Box
      component='svg'
      width='100%'
      height={SPARK_H}
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      preserveAspectRatio='none'
      role='img'
      aria-label={t`Trend over the selected window`}
      style={{ display: 'block' }}
    >
      <path
        d={path}
        fill='none'
        stroke='currentColor'
        strokeWidth={1.5}
        strokeLinejoin='round'
        strokeLinecap='round'
        vectorEffect='non-scaling-stroke'
        opacity={0.6}
      />
    </Box>
  );
}

function TrendMark({ trend }: Readonly<{ trend: Trend | null }>) {
  if (trend === null) return null;
  const icon =
    trend === 'up' ? (
      <IconArrowUpRight size={14} />
    ) : trend === 'down' ? (
      <IconArrowDownRight size={14} />
    ) : (
      <IconMinus size={14} />
    );
  const label =
    trend === 'up'
      ? t`Rising over the window`
      : trend === 'down'
        ? t`Falling over the window`
        : t`Steady over the window`;
  return (
    <Tooltip label={label}>
      <Text component='span' c='dimmed' style={{ display: 'inline-flex' }}>
        {icon}
      </Text>
    </Tooltip>
  );
}

function Range({
  stats,
  decimals,
  unit
}: Readonly<{ stats: SeriesStats | null; decimals: number; unit: string }>) {
  if (!stats) return null;
  return (
    <Text size='xs' c='dimmed'>
      {t`Window`} {formatRange(stats.min, stats.max, decimals, unit)}
    </Text>
  );
}

export function KpiTileCard({
  tile,
  now
}: Readonly<{ tile: KpiTile; now: number }>) {
  const stats = tile.series ? seriesStats(tile.series) : null;
  const trend = tile.series ? trendOf(tile.series) : null;
  const missing = tile.value === null && !tile.valueText;
  const valueText =
    tile.valueText ??
    formatValue(
      tile.value,
      tile.decimals,
      undefined,
      stats ? stats.max - stats.min : null
    );

  return (
    <Paper withBorder radius='md' p='sm' data-kpi={tile.key}>
      <Stack gap={4}>
        {/* The label alone, always one line. A badge beside it wrapped the
            header on the one tile that carried a verdict, which dropped that
            tile's value a line below its neighbours' - so the badges sit on
            the age row at the foot instead, where the Stale badge also lands
            beside the yellow age it explains. */}
        <Text size='xs' c='dimmed' fw={500}>
          {tile.label}
        </Text>
        {missing ? (
          <Text size='sm' c='dimmed' py={6}>
            {tile.missingReason ?? t`No reading`}
          </Text>
        ) : (
          <Stack gap={2}>
            {/* Value, unit and trend may wrap; a long reading such as
                -0.00058 mH2O must never push its unit out of the tile. */}
            <Group gap={6} align='baseline' wrap='wrap' style={{ rowGap: 0 }}>
              <Text fz={valueText.length > 7 ? 20 : 24} fw={600} lh={1.1}>
                {valueText}
              </Text>
              <Group gap={4} align='center' wrap='nowrap'>
                {!tile.valueText && tile.unit && (
                  <Text size='sm' c='dimmed'>
                    {tile.unit}
                  </Text>
                )}
                <TrendMark trend={trend} />
              </Group>
            </Group>
            {tile.series && <Sparkline entry={tile.series} />}
          </Stack>
        )}
        {tile.caption && (
          <Text size='xs' c='dimmed'>
            {tile.caption}
          </Text>
        )}
        {/* A tile whose latest value is unusable still has a window and a
            reading age; dropping both left it saying only "Unavailable". */}
        {(!missing || stats !== null) && (
          <Range stats={stats} decimals={tile.decimals} unit={tile.unit} />
        )}
        {(!missing || tile.observedAt !== null) && (
          <Group gap={6} wrap='wrap' align='center'>
            <Tooltip
              label={t`Observed ${formatClockOn(tile.observedAt, now)}`}
              disabled={tile.observedAt === null}
            >
              <Text size='xs' c={tile.stale ? 'yellow.8' : 'dimmed'}>
                {tile.observedAt === null
                  ? t`No reading`
                  : formatAge(tile.observedAt, now)}
                {/* Only where a limit could exist and none does: an unusable
                    reading has limits all the same, and a calculated total
                    cannot have any. */}
                {!tile.limitsConfigured && !tile.calculated && !tile.valueText
                  ? ` · ${t`no limit configured`}`
                  : ''}
              </Text>
            </Tooltip>
            {tile.state !== 'unconfigured' && !missing && !tile.stale && (
              <Box style={{ flexShrink: 0 }}>
                <HealthStateBadge state={tile.state} size='xs' />
              </Box>
            )}
            {tile.quality && tile.quality !== 'good' && !missing && (
              <Badge size='xs' color='gray' variant='light'>
                {tile.quality === 'bad' ? t`Bad quality` : t`Uncertain quality`}
              </Badge>
            )}
            {tile.stale && !missing && (
              <Badge size='xs' color='yellow' variant='light'>
                {t`Stale`}
              </Badge>
            )}
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

/** Level 1 of the page: what is happening now. */
export function KpiStrip({
  tiles,
  now
}: Readonly<{ tiles: KpiTile[]; now: number }>) {
  if (tiles.length === 0) return null;
  return (
    <SimpleGrid
      cols={{ base: 2, sm: 3, md: 4, xl: Math.min(8, tiles.length) }}
      spacing='sm'
    >
      {tiles.map((tile) => (
        <KpiTileCard key={tile.key} tile={tile} now={now} />
      ))}
    </SimpleGrid>
  );
}

export default KpiStrip;
