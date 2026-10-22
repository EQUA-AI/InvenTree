import { t } from '@lingui/core/macro';
import { ActionIcon, Box, Paper, Text, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { KeyboardEvent } from 'react';
import { alarmColor, alarmLabel, stateStyle } from './MimicSchematic';
import { MissingValue } from './MissingValue';
import classes from './StationSchematic.module.css';
import {
  byBayNumber,
  elementLabel,
  expandPointer,
  reasonLabel,
  stateLabel,
  statusText,
  usable,
  valueText
} from './format';
import type { MimicData } from './types';

/**
 * Readings live in a grid above the drawing, never on its pipes or pump bays.
 * A narrow viewport scrolls the drawing instead of shrinking its labels.
 */
export function StationSchematic({
  data,
  selected,
  onSelect
}: Readonly<{
  data: MimicData;
  selected: string | null;
  onSelect: (key: string) => void;
}>) {
  const bays = [...data.bays].sort(byBayNumber);
  const power = data.layout.elements.find(
    (element) => element.view === 'unit' && element.id === 'pump-power'
  );
  const readings = bays.map((bay) => {
    const point = power
      ? bay.points[expandPointer(power.pointer, bay.key)]
      : undefined;
    return usable(point) ? valueText(point) : stateLabel(bay.state);
  });
  // Reserve enough room for both lines in every translation and reading.
  const slot = Math.max(
    64,
    ...bays.map(
      (bay, index) =>
        Math.max(bay.key.length, readings[index].length) * 7.5 + 16
    )
  );
  const start = 154;
  const width = Math.max(520, start + slot * bays.length + 26);
  const bottom = 216;

  return (
    <>
      <div className={classes.header}>
        <Text size='sm' fw={600}>{t`Station schematic`}</Text>
        {data.layout.review_status !== 'approved' && (
          <Tooltip
            label={t`This schematic awaits plant review. Missing mappings are shown as unavailable.`}
            events={{ hover: true, focus: true, touch: true }}
            multiline
            maw={300}
          >
            <ActionIcon
              variant='subtle'
              color='gray'
              className={classes.info}
              aria-label={t`Schematic awaiting plant review`}
            >
              <IconInfoCircle size={18} />
            </ActionIcon>
          </Tooltip>
        )}
      </div>
      <div className={classes.metrics}>
        {data.layout.elements
          .filter((element) => element.view === 'station')
          .map((element) => {
            const point = data.station_points[element.pointer];
            const label = elementLabel(element);
            const missingReason = reasonLabel(point?.reason ?? 'not_bound');
            return (
              <Paper
                key={element.id}
                withBorder
                radius='sm'
                px='sm'
                py='xs'
                className={classes.metric}
                data-point={element.pointer}
                data-station-metric={element.id}
                aria-label={label}
              >
                <Text size='xs' className={classes.label}>
                  {label}
                </Text>
                <Text
                  component='div'
                  size='xl'
                  fw={650}
                  className={classes.reading}
                  data-reading-value
                >
                  {usable(point) ? (
                    element.role === 'status' ? (
                      statusText(point, data.layout)
                    ) : (
                      valueText(point)
                    )
                  ) : (
                    <MissingValue label={label} reason={missingReason} />
                  )}
                </Text>
              </Paper>
            );
          })}
      </div>
      {!!bays.length && (
        <Box
          component='section'
          className={classes.viewport}
          tabIndex={0}
          aria-label={t`Station pump bays`}
        >
          <svg
            viewBox={`0 0 ${width} 280`}
            className={classes.drawing}
            style={{ minWidth: width }}
            aria-label={t`Station schematic`}
          >
            <title>{t`Station schematic`}</title>
            <defs>
              <pattern
                id='station-mimic-hatch'
                width='8'
                height='8'
                patternUnits='userSpaceOnUse'
              >
                <rect width='8' height='8' fill='var(--mantine-color-body)' />
                <path
                  d='M-2 2L2 -2M0 8L8 0M6 10L10 6'
                  stroke='currentColor'
                  strokeWidth={1.5}
                />
              </pattern>
            </defs>
            <g
              fill='none'
              stroke='currentColor'
              strokeWidth={2}
              strokeLinecap='round'
              strokeLinejoin='round'
            >
              {/* The same illustrative forebay and two neutral headers. */}
              <path d='M18 60V228H126V60' />
              <path d='M18 108q9-7 18 0t18 0t18 0t18 0t18 0t18 0' />
              <path d={`M126 ${bottom}H${width - 18}v12H126`} />
              <path d={`M${start - 8} 32H${width - 10}v12H${start - 8}z`} />
              {bays.map((bay, index) => {
                const x = start + slot * (index + 0.5);
                const state = stateStyle(bay.state);
                const alarm = alarmColor(bay.alarm);
                const chosen = bay.key === selected;
                const press = (event: KeyboardEvent) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(bay.key);
                  }
                };
                return (
                  <g
                    key={bay.key}
                    // biome-ignore lint/a11y/useSemanticElements: SVG equipment selection needs SVG geometry and keyboard semantics
                    role='button'
                    tabIndex={0}
                    aria-pressed={chosen}
                    aria-label={`${bay.key}: ${stateLabel(bay.state)}${alarm ? `, ${alarmLabel(bay.alarm)}` : ''}`}
                    onClick={() => onSelect(bay.key)}
                    onKeyDown={press}
                    onFocus={(event) =>
                      event.currentTarget.scrollIntoView({
                        block: 'nearest',
                        inline: 'nearest'
                      })
                    }
                    className={classes.bay}
                    data-bay={bay.key}
                    data-alarm={bay.alarm ?? undefined}
                  >
                    <title>
                      {`${bay.name}: ${stateLabel(bay.state)}${bay.active ? '' : ` · ${t`Inactive equipment`}`}${alarm ? ` · ${alarmLabel(bay.alarm)}` : ''}`}
                    </title>
                    <rect
                      className={classes.focus}
                      x={x - slot / 2 + 3}
                      y={48}
                      width={slot - 6}
                      height={228}
                      rx={6}
                      style={
                        chosen
                          ? {
                              fill: 'var(--mantine-primary-color-light)',
                              stroke: 'var(--mantine-primary-color-filled)'
                            }
                          : undefined
                      }
                    />
                    <path d={`M${x} 196V${bottom}M${x + 20} 176h6V44`} />
                    <path d={`M${x} 144V156`} stroke={state.line} />
                    <rect
                      x={x - 15}
                      y={66}
                      width={30}
                      height={78}
                      rx={4}
                      fill={
                        bay.state === 'stale'
                          ? 'url(#station-mimic-hatch)'
                          : state.fill
                      }
                      stroke={state.line}
                      strokeDasharray={state.dashed ? '5 4' : undefined}
                    />
                    <circle
                      cx={x}
                      cy={176}
                      r={20}
                      fill={
                        bay.state === 'stale'
                          ? 'url(#station-mimic-hatch)'
                          : state.fill
                      }
                      stroke={state.line}
                      strokeDasharray={state.dashed ? '5 4' : undefined}
                    />
                    <circle
                      cx={x}
                      cy={176}
                      r={5}
                      fill={state.line}
                      stroke='none'
                    />
                    {alarm && (
                      <g data-alarm-marker={bay.alarm}>
                        <circle
                          cx={x}
                          cy={56}
                          r={9}
                          fill={alarm}
                          stroke='none'
                        />
                        <text
                          x={x}
                          y={60}
                          textAnchor='middle'
                          fontSize={14}
                          fontWeight={700}
                          fill={
                            bay.alarm === 'warning'
                              ? 'var(--mantine-color-black)'
                              : 'var(--mantine-color-white)'
                          }
                          stroke='none'
                        >
                          !
                        </text>
                      </g>
                    )}
                    <text
                      x={x}
                      y={250}
                      textAnchor='middle'
                      fontSize={14}
                      fontWeight={650}
                      fill='currentColor'
                      stroke='none'
                    >
                      {bay.key}
                    </text>
                    <text
                      x={x}
                      y={270}
                      textAnchor='middle'
                      fontSize={13}
                      fill='currentColor'
                      stroke='none'
                    >
                      {readings[index]}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </Box>
      )}
    </>
  );
}
