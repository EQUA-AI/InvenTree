import { t } from '@lingui/core/macro';
import { Alert, Badge, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { useMemo } from 'react';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import { SensorGroupCard } from '../SensorGroupCard';
import { TrendChart, type TrendSeries, familyColors } from '../TrendChart';
import { formatValue } from '../format';
import { type ResolvedParameter, withRole } from '../resolve';
import {
  alignRows,
  differenceSeries,
  gapThresholdMs,
  seriesStats,
  syntheticEntry
} from '../series';
import { ChartCard, type SectionProps } from './common';

/**
 * Motor winding temperature: the stator RTDs as one family, the core RTDs as
 * another. This is where the plant's configured limits first appear as lines.
 */
export function WindingSection({
  parameters,
  series,
  window,
  syncId,
  onZoom
}: Readonly<SectionProps>) {
  const winding = withRole(parameters, 'winding');
  const core = withRole(parameters, 'core');
  const misc = withRole(parameters, 'motor_misc');
  if (winding.length + core.length + misc.length === 0) {
    return null;
  }
  return (
    <Stack gap='md'>
      {winding.length > 0 && (
        <SensorGroupCard
          title={t`Stator winding temperature`}
          description={t`Every winding RTD on one axis. The highest sensor and the spread between sensors are the two numbers that change first.`}
          parameters={winding}
          series={series}
          window={window}
          syncId={syncId}
          hue='orange'
          heatmap
          comparison
          onZoom={onZoom}
        />
      )}
      <SimpleGrid
        cols={{ base: 1, lg: core.length > 0 && misc.length > 0 ? 2 : 1 }}
        spacing='md'
      >
        {core.length > 0 && (
          <SensorGroupCard
            title={t`Stator core temperature`}
            parameters={core}
            series={series}
            window={window}
            syncId={syncId}
            hue='red'
            comparison
            onZoom={onZoom}
          />
        )}
        {misc.length > 0 && (
          <SensorGroupCard
            title={t`Other motor temperatures`}
            parameters={misc}
            series={series}
            window={window}
            syncId={syncId}
            hue='gray'
            onZoom={onZoom}
          />
        )}
      </SimpleGrid>
    </Stack>
  );
}

/**
 * Vibration and mechanical health.
 *
 * Every approved vibration channel in one card, so an abnormal sensor is a
 * bright row in the heatmap and the top bar in the comparison without anyone
 * opening a chart. When the pump has no approved vibration channel the card
 * says so and says why: the tags exist, but their unit has not been confirmed
 * in review, and an unconfirmed unit is not a measurement.
 */
export function VibrationSection({
  parameters,
  series,
  window,
  syncId,
  onZoom
}: Readonly<SectionProps>) {
  const vibration = withRole(
    parameters,
    'nde_vibration',
    'de_vibration',
    'thrust_vibration',
    'vibration',
    'velometer'
  );
  if (vibration.length === 0) {
    return (
      <Alert
        color='gray'
        variant='light'
        icon={<IconInfoCircle size={16} />}
        title={t`No approved vibration channel`}
      >
        <Text size='sm'>
          {t`This pump's vibration tags are in the dictionary but have not passed review, because the source does not say whether they report displacement (µm) or velocity (mm/s). Until that is confirmed they are not shown as live readings, so this section has nothing it can honestly draw.`}
        </Text>
      </Alert>
    );
  }
  const units = new Set(vibration.map((p) => p.signal.unit));
  // A channel approved without a unit is the plant's raw word, not a
  // magnitude against a standard: say so where the numbers are.
  const unconfirmed = vibration.some((p) => !p.signal.unit);
  if (units.size === 1) {
    return (
      <SensorGroupCard
        title={t`Vibration sensors`}
        description={
          unconfirmed
            ? t`Raw source values, drawn without a unit: the plant has not confirmed whether these channels report displacement (µm) or velocity (mm/s), and the readings can be signed. The chart shows how each channel moves, not a magnitude to judge against a standard.`
            : t`Motor NDE, DE and thrust bearing channels on one axis.`
        }
        parameters={vibration}
        series={series}
        window={window}
        syncId={syncId}
        hue='indigo'
        heatmap
        comparison
        onZoom={onZoom}
      />
    );
  }
  // Mixed units cannot share an axis; one card per family.
  const families = [
    { role: 'nde_vibration', title: t`Motor NDE bearing vibration` },
    { role: 'de_vibration', title: t`Motor DE vibration` },
    { role: 'thrust_vibration', title: t`Thrust bearing vibration` },
    { role: 'vibration', title: t`Vibration sensors` },
    { role: 'velometer', title: t`Motor velometers` }
  ];
  return (
    <SimpleGrid cols={{ base: 1, lg: 2 }} spacing='md'>
      {families.map((f) => {
        const group = withRole(parameters, f.role);
        return group.length > 0 ? (
          <SensorGroupCard
            key={f.role}
            title={f.title}
            parameters={group}
            series={series}
            window={window}
            syncId={syncId}
            hue='indigo'
            comparison
            onZoom={onZoom}
          />
        ) : null;
      })}
    </SimpleGrid>
  );
}

/** Pair sensors of two families by index: inlet 2 with outlet 2. */
function pairByIndex(
  a: ResolvedParameter[],
  b: ResolvedParameter[]
): { a: ResolvedParameter; b: ResolvedParameter }[] {
  const byIndex = new Map<number, ResolvedParameter>();
  for (const p of b) {
    if (p.index !== null) byIndex.set(p.index, p);
  }
  const pairs: { a: ResolvedParameter; b: ResolvedParameter }[] = [];
  for (const p of a) {
    const other = p.index !== null ? byIndex.get(p.index) : undefined;
    if (other) pairs.push({ a: p, b: other });
  }
  // Two lone sensors with no indexes still make one pair.
  if (pairs.length === 0 && a.length === 1 && b.length === 1) {
    pairs.push({ a: a[0], b: b[0] });
  }
  return pairs;
}

/**
 * A calculated ΔT per pair, drawn as its own trend with the current values as
 * badges. Computed only where both sensors were read at the same instant, and
 * labelled as calculated wherever it appears.
 */
function DifferenceCard({
  title,
  description,
  pairs,
  series,
  window,
  syncId,
  hue
}: Readonly<{
  title: string;
  description: string;
  pairs: { a: ResolvedParameter; b: ResolvedParameter }[];
  series: Map<number, SeriesEntry>;
  window: SectionProps['window'];
  syncId: string;
  hue: string;
}>) {
  const unit = pairs[0]?.a.signal.unit ?? '';
  const decimals = pairs[0]?.a.definition.decimals ?? 1;
  const colors = familyColors(pairs.length, [hue]);

  const computed = useMemo(
    () =>
      pairs.map((pair, i) => {
        const label =
          pair.a.index !== null
            ? t`ΔT ${pair.a.index}`
            : t`ΔT ${pair.a.label} − ${pair.b.label}`;
        const samples = differenceSeries(
          series.get(pair.a.signal.binding_id),
          series.get(pair.b.signal.binding_id)
        );
        const entry = syntheticEntry(label, unit, samples);
        const current =
          typeof pair.a.signal.value === 'number' &&
          typeof pair.b.signal.value === 'number'
            ? pair.a.signal.value - pair.b.signal.value
            : null;
        return {
          key: `delta-${i}`,
          label,
          entry,
          current,
          stale: pair.a.signal.stale || pair.b.signal.stale,
          color: colors[i]
        };
      }),
    [pairs, series, unit, colors]
  );

  const rows = useMemo(
    () =>
      alignRows(
        computed.map((c) => ({ key: c.key, entry: c.entry })),
        gapThresholdMs(window.resolutionSeconds)
      ),
    [computed, window.resolutionSeconds]
  );
  const lines: TrendSeries[] = computed.map((c) => ({
    key: c.key,
    label: c.label,
    color: c.color,
    unit,
    decimals
  }));

  if (pairs.length === 0) return null;

  return (
    <ChartCard title={title} description={description}>
      <Group gap='xs' wrap='wrap'>
        {computed.map((c) => {
          const stats = seriesStats(c.entry);
          return (
            <Badge
              key={c.key}
              variant='light'
              color={c.stale ? 'gray' : hue}
              size='sm'
            >
              {c.label}:{' '}
              {c.current === null
                ? t`no reading`
                : formatValue(
                    c.current,
                    decimals,
                    unit,
                    stats ? stats.max - stats.min : null
                  )}
              {stats
                ? ` (${t`window`} ${formatValue(stats.min, decimals, undefined, stats.max - stats.min)}–${formatValue(stats.max, decimals, undefined, stats.max - stats.min)})`
                : ''}
            </Badge>
          );
        })}
        <Badge variant='outline' color='gray' size='sm'>
          {t`Calculated`}
        </Badge>
      </Group>
      <TrendChart
        rows={rows}
        series={lines}
        windowStart={window.start}
        windowEnd={window.end}
        windowSeconds={window.seconds}
        unit={unit}
        syncId={syncId}
        toggleable={lines.length > 2}
        height={200}
      />
    </ChartCard>
  );
}

/**
 * Cooling and thermal management: the water circuit as inlet, outlet and the
 * calculated rise across the cooler; the air circuit as hot and cold sides.
 */
export function CoolingSection({
  parameters,
  series,
  window,
  syncId,
  onZoom
}: Readonly<SectionProps>) {
  const inlet = withRole(parameters, 'water_inlet');
  const outlet = withRole(parameters, 'water_outlet');
  const water = withRole(parameters, 'water');
  const cold = withRole(parameters, 'cold_air');
  const hot = withRole(parameters, 'hot_air');

  const waterPairs = useMemo(() => pairByIndex(outlet, inlet), [outlet, inlet]);
  const airPairs = useMemo(() => pairByIndex(hot, cold), [hot, cold]);

  if (
    inlet.length + outlet.length + water.length + cold.length + hot.length ===
    0
  ) {
    return null;
  }

  return (
    <Stack gap='md'>
      {(inlet.length > 0 || outlet.length > 0) && (
        <SimpleGrid
          cols={{ base: 1, lg: waterPairs.length > 0 ? 3 : 2 }}
          spacing='md'
        >
          {inlet.length > 0 && (
            <SensorGroupCard
              title={t`Cooling water inlet`}
              parameters={inlet}
              series={series}
              window={window}
              syncId={syncId}
              hue='cyan'
              comparison={inlet.length > 2}
              onZoom={onZoom}
            />
          )}
          {outlet.length > 0 && (
            <SensorGroupCard
              title={t`Cooling water outlet`}
              parameters={outlet}
              series={series}
              window={window}
              syncId={syncId}
              hue='orange'
              comparison={outlet.length > 2}
              onZoom={onZoom}
            />
          )}
          {waterPairs.length > 0 && (
            <DifferenceCard
              title={t`Cooling water rise (outlet − inlet)`}
              description={t`How much heat the water carries away. Calculated from sensors paired by number, only where both were read at the same instant.`}
              pairs={waterPairs}
              series={series}
              window={window}
              syncId={syncId}
              hue='grape'
            />
          )}
        </SimpleGrid>
      )}
      {water.length > 0 && (
        <SensorGroupCard
          title={t`Cooling water RTDs`}
          description={t`The dictionary does not say which of these are inlet and which outlet, so they are drawn as one family.`}
          parameters={water}
          series={series}
          window={window}
          syncId={syncId}
          hue='cyan'
          comparison
          onZoom={onZoom}
        />
      )}
      {(cold.length > 0 || hot.length > 0) && (
        <SimpleGrid
          cols={{ base: 1, lg: airPairs.length > 0 ? 3 : 2 }}
          spacing='md'
        >
          {cold.length > 0 && (
            <SensorGroupCard
              title={t`Cooling air, cold side`}
              parameters={cold}
              series={series}
              window={window}
              syncId={syncId}
              hue='blue'
              comparison={cold.length > 2}
              onZoom={onZoom}
            />
          )}
          {hot.length > 0 && (
            <SensorGroupCard
              title={t`Cooling air, hot side`}
              parameters={hot}
              series={series}
              window={window}
              syncId={syncId}
              hue='red'
              comparison={hot.length > 2}
              onZoom={onZoom}
            />
          )}
          {airPairs.length > 0 && (
            <DifferenceCard
              title={t`Air rise (hot − cold)`}
              description={t`Calculated from hot and cold sensors paired by number.`}
              pairs={airPairs}
              series={series}
              window={window}
              syncId={syncId}
              hue='grape'
            />
          )}
        </SimpleGrid>
      )}
    </Stack>
  );
}

/** Bearing pads and the oil that lubricates them. */
export function BearingSection({
  parameters,
  series,
  window,
  syncId,
  onZoom
}: Readonly<SectionProps>) {
  const thrust = withRole(parameters, 'thrust_pad');
  const guide = withRole(parameters, 'guide_pad');
  const bearing = withRole(parameters, 'bearing');
  const oil = withRole(parameters, 'oil');
  if (thrust.length + guide.length + bearing.length + oil.length === 0) {
    return null;
  }
  return (
    <Stack gap='md'>
      {thrust.length > 0 && (
        <SensorGroupCard
          title={t`Thrust bearing pads`}
          parameters={thrust}
          series={series}
          window={window}
          syncId={syncId}
          hue='grape'
          heatmap
          comparison
          onZoom={onZoom}
        />
      )}
      {guide.length > 0 && (
        <SensorGroupCard
          title={t`Guide bearing pads`}
          parameters={guide}
          series={series}
          window={window}
          syncId={syncId}
          hue='violet'
          heatmap
          comparison
          onZoom={onZoom}
        />
      )}
      <SimpleGrid
        cols={{ base: 1, lg: bearing.length > 0 && oil.length > 0 ? 2 : 1 }}
        spacing='md'
      >
        {bearing.length > 0 && (
          <SensorGroupCard
            title={t`Bearing temperatures`}
            parameters={bearing}
            series={series}
            window={window}
            syncId={syncId}
            hue='pink'
            comparison
            onZoom={onZoom}
          />
        )}
        {oil.length > 0 && (
          <SensorGroupCard
            title={t`Lubricating oil`}
            description={t`Reservoir, guide bearing and cooler outlet oil temperatures.`}
            parameters={oil}
            series={series}
            window={window}
            syncId={syncId}
            hue='teal'
            comparison
            onZoom={onZoom}
          />
        )}
      </SimpleGrid>
    </Stack>
  );
}
