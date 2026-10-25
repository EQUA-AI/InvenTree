import { t } from '@lingui/core/macro';
import {
  Group,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
  UnstyledButton
} from '@mantine/core';
import { IconClockExclamation, IconSearch } from '@tabler/icons-react';
import { type ReactNode, useRef, useState } from 'react';
import { factTooltipProps } from '../../../../components/items/ValueTooltip';
import operations from '../../operations.module.css';
import { showRecordedTelemetry } from '../../telemetryPresentation';

import { shiftSpan } from '../../faults/occurrenceLabels';
import { StatusBadge as Badge } from '../common';
import { FreshnessLabel } from './FreshnessLabel';
import { MimicAlarmCoverage, unconfiguredCount } from './MimicAlarmCoverage';
import { MimicLiveBadge } from './MimicLiveBadge';
import type { PartState } from './MimicParts';
import { type ReadingSection, ReadingSections } from './MimicReadings';
import { MimicSchematic } from './MimicSchematic';
import classes from './UnitMimic.module.css';
import { displayFor, expandPointer, stateLabel } from './format';
import { knownNoActiveAlarms, operatingState } from './mimicVisuals';
import type { MimicData, MimicPoint } from './types';

export function UnitMimicHeader({
  data,
  unit,
  actions,
  headingId,
  headingRef,
  information
}: {
  data: MimicData;
  unit: string;
  actions?: ReactNode;
  headingId?: string;
  headingRef?: React.Ref<HTMLHeadingElement>;
  information?: ReactNode;
}) {
  const bay = data.bays.find((b) => b.key === unit);
  const operation = operatingState(data, bay);
  const statusElement = data.layout.elements.find(
    (e) => e.view === 'unit' && e.role === 'status'
  );
  const statusPoint = statusElement
    ? data.points[expandPointer(statusElement.pointer, unit)]
    : undefined;
  return (
    <Group justify='space-between' data-unit-header>
      <Group gap={4}>
        <Group gap={4} wrap='nowrap' miw={0}>
          <Title
            order={headingId ? 4 : 5}
            id={headingId}
            ref={headingRef}
            tabIndex={headingRef ? -1 : undefined}
            style={
              headingRef
                ? { scrollMarginBlockStart: 'var(--mantine-spacing-xs)' }
                : undefined
            }
          >
            {bay?.name?.split('/').pop()?.trim() || t`Pump`}
          </Title>
          {information}
        </Group>
        {bay && (
          <Badge
            variant={operation === 'stale' ? 'outline' : 'light'}
            leftSection={
              operation === 'stale' ? (
                <IconClockExclamation size={14} />
              ) : undefined
            }
            color={
              operation === 'running'
                ? 'green'
                : operation === 'fault'
                  ? 'red'
                  : operation === 'stale'
                    ? 'gray'
                    : 'gray'
            }
          >
            {stateLabel(operation)}
          </Badge>
        )}
        <MimicLiveBadge data={data} />
        <FreshnessLabel
          point={statusPoint}
          preferJustNow={data.recorded_telemetry_as_live !== false}
        />
        {showRecordedTelemetry(data) && (
          <Tooltip
            label={t`Times moved ${shiftSpan(data.display_shift_seconds ?? 0) ?? t`an unspecified interval`} onto the display clock; plant times are earlier`}
            {...factTooltipProps}
          >
            <UnstyledButton
              className={operations.phoneTarget}
              aria-label={t`Recorded history shown as current`}
            >
              <Badge color='gray' variant='outline'>
                {t`Recorded history shown as current`}
              </Badge>
            </UnstyledButton>
          </Tooltip>
        )}
        {bay && !bay.active && (
          <Badge variant='outline' color='gray'>
            {t`Inactive equipment`}
          </Badge>
        )}
      </Group>
      {actions && <Group gap='sm'>{actions}</Group>}
    </Group>
  );
}

/** The panel a drawing sits on: a shade off the page, in either theme. */
export const PANEL =
  'var(--aimms-panel, light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-8)))';

/**
 * One pump: its drawing, and its readings by the part they belong to.
 *
 * Shown under a station when one of its bays is chosen, and as a pump's own
 * mimic. The drawing numbers the pump's parts; the list beneath has the same
 * numbers, and whatever has none is a system with no place on a drawing - an
 * electrical or control grouping rather than a thing bolted to the floor.
 */
export function UnitMimic({
  data,
  unit,
  actions,
  header = true,
  alarmPanel,
  headingRef
}: Readonly<{
  data: MimicData;
  unit: string;
  /** Links and buttons for the heading, which differ by where this is shown. */
  actions?: ReactNode;
  header?: boolean;
  alarmPanel?: ReactNode;
  headingRef?: React.Ref<HTMLHeadingElement>;
}>) {
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const display = displayFor(data, unit);
  const knownEmptyAlarms = knownNoActiveAlarms(data);

  const numbers = new Map(
    (data.layout.parts ?? []).map((part, index) => [part.code, index + 1])
  );
  const groups: Record<string, MimicPoint[]> = {};
  for (const point of Object.values(data.points)) {
    if (
      !`${point.label} ${point.pointer}`
        .toLowerCase()
        .includes(search.toLowerCase())
    )
      continue;
    const group = point.group || t`Other readings`;
    groups[group] ??= [];
    groups[group].push(point);
  }
  // Drawn parts first, in the order the drawing numbers them; then the
  // systems that are not drawn, by name; then the points no system claims.
  const unnamed = t`Other readings`;
  const numberOf = (points: MimicPoint[]) =>
    numbers.get(points.find((point) => point.part_code)?.part_code ?? '');
  const sections: ReadingSection[] = Object.entries(groups)
    .map(([title, points]) => ({
      key: title,
      title,
      points,
      number: numberOf(points)
    }))
    .sort(
      (a, b) =>
        (a.number ?? 1000) - (b.number ?? 1000) ||
        Number(a.title === unnamed) - Number(b.title === unnamed) ||
        a.title.localeCompare(b.title)
    );

  // Choosing a part on the drawing opens its readings and brings them up.
  const openPart = (state: PartState) => {
    const keys = sections
      .filter((section) =>
        section.points.some((point) => point.part_code === state.part.code)
      )
      .map((section) => section.key);
    if (!keys.length) return;
    setOpen((current) => [...new Set([...current, ...keys])]);
    requestAnimationFrame(() => {
      const target = [
        ...(list.current?.querySelectorAll('[data-section]') ?? [])
      ].find((node) => node.getAttribute('data-section') === keys[0]);
      target?.querySelector('button')?.focus({ preventScroll: true });
      target?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? 'instant'
          : 'smooth',
        block: 'center'
      });
    });
  };

  return (
    <Stack gap='md'>
      {header && (
        <UnitMimicHeader
          data={data}
          unit={unit}
          actions={
            <Group gap='xs'>
              {actions}
              <MimicAlarmCoverage
                count={unconfiguredCount(Object.values(data.points))}
                knownEmpty={knownEmptyAlarms}
              />
            </Group>
          }
          headingRef={headingRef}
        />
      )}

      <Paper withBorder p='md' className={operations.mimicPanel}>
        <MimicSchematic
          data={data}
          unit={unit}
          display={display}
          onPart={openPart}
        />
        <fieldset
          className={classes.legend}
          aria-label={t`Schematic state legend`}
        >
          <fieldset className={classes.legendGroup} aria-label={t`Pump states`}>
            {['running', 'idle', 'fault', 'stale', 'unknown'].map((state) => (
              <span key={state} className={classes.legendItem}>
                <span
                  className={classes.legendMark}
                  data-state={state}
                  aria-hidden='true'
                />
                {stateLabel(state)}
              </span>
            ))}
          </fieldset>
          <fieldset
            className={classes.legendGroup}
            aria-label={t`Reading states`}
          >
            {[
              ['critical', t`Critical`],
              ['warning', t`Warning`],
              ['normal', t`Normal`],
              ['unconfigured', t`No limits`],
              ['unavailable', t`No readings`]
            ].map(([verdict, label]) => (
              <span className={classes.legendItem} key={verdict}>
                <span
                  className={classes.readingLegendMark}
                  data-verdict={verdict}
                  aria-hidden='true'
                >
                  {verdict === 'normal' ? '✓' : null}
                </span>
                {label}
              </span>
            ))}
          </fieldset>
        </fieldset>
      </Paper>
      {alarmPanel}
      <TextInput
        classNames={{ input: `${operations.input} ${classes.filterInput}` }}
        aria-label={t`Filter unit readings`}
        placeholder={t`Filter readings`}
        leftSection={<IconSearch size={16} />}
        maw={360}
        value={search}
        onChange={(event) => setSearch(event.currentTarget.value)}
      />
      <div ref={list}>
        <ReadingSections
          exercises={data.alarms}
          sections={sections}
          display={display}
          open={open}
          onOpen={setOpen}
        />
      </div>
      {!sections.length && <Text>{t`No matching readings.`}</Text>}
    </Stack>
  );
}
