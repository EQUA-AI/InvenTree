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

import type { HealthState, SeriesEntry } from '@lib/types/MachineHealth';

import { HealthStateBadge } from '../health/common';
import { formatAge, formatClock, formatValue } from './format';
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
  /** The configured-limit verdict; 'unconfigured' when no limit exists. */
  state: HealthState | 'unconfigured';
  series?: SeriesEntry;
  /** A second line under the value: "Highest: winding 7", say. */
  caption?: string;
  /** Why there is no value - and why the tile is still shown. */
  missingReason?: string;
}

const SPARK_W = 96;
const SPARK_H = 24;

export function Sparkline({ entry }: Readonly<{ entry: SeriesEntry }>) {
  const points = entry.samples
    .map((s) => s.v)
    .filter((v): v is number => v !== null && Number.isFinite(v));
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = SPARK_W / (points.length - 1);
  const path = points
    .map((v, i) => {
      const x = i * step;
      const y = SPARK_H - ((v - min) / span) * SPARK_H;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <Box
      component='svg'
      width={SPARK_W}
      height={SPARK_H}
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      role='img'
      aria-label={t`Trend over the selected window`}
    >
      <path
        d={path}
        fill='none'
        stroke='currentColor'
        strokeWidth={1.5}
        strokeLinejoin='round'
        strokeLinecap='round'
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
      {t`Window`} {formatValue(stats.min, decimals)}–
      {formatValue(stats.max, decimals, unit)}
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

  return (
    <Paper withBorder radius='md' p='sm' data-kpi={tile.key}>
      <Stack gap={4}>
        <Group justify='space-between' wrap='nowrap' gap={4}>
          <Text size='xs' c='dimmed' fw={500} truncate>
            {tile.label}
          </Text>
          {tile.state !== 'unconfigured' && !missing && !tile.stale && (
            <Box style={{ flexShrink: 0 }}>
              <HealthStateBadge state={tile.state} size='xs' />
            </Box>
          )}
          {tile.stale && !missing && (
            <Badge size='xs' color='yellow' variant='light'>
              {t`Stale`}
            </Badge>
          )}
        </Group>
        {missing ? (
          <Text size='sm' c='dimmed' py={6}>
            {tile.missingReason ?? t`No reading`}
          </Text>
        ) : (
          <Group justify='space-between' align='flex-end' wrap='nowrap'>
            <Group gap={6} align='baseline' wrap='nowrap'>
              <Text fz={24} fw={600} lh={1.1}>
                {tile.valueText ?? formatValue(tile.value, tile.decimals)}
              </Text>
              {!tile.valueText && tile.unit && (
                <Text size='sm' c='dimmed'>
                  {tile.unit}
                </Text>
              )}
              <TrendMark trend={trend} />
            </Group>
            {tile.series && <Sparkline entry={tile.series} />}
          </Group>
        )}
        {tile.caption && (
          <Text size='xs' c='dimmed'>
            {tile.caption}
          </Text>
        )}
        {!missing && (
          <Range stats={stats} decimals={tile.decimals} unit={tile.unit} />
        )}
        {!missing && (
          <Tooltip
            label={t`Observed ${formatClock(tile.observedAt)}`}
            disabled={tile.observedAt === null}
          >
            <Text size='xs' c={tile.stale ? 'yellow.8' : 'dimmed'}>
              {tile.observedAt === null
                ? t`No reading`
                : formatAge(tile.observedAt, now)}
              {tile.state === 'unconfigured' && !tile.valueText
                ? ` · ${t`no limit configured`}`
                : ''}
            </Text>
          </Tooltip>
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
