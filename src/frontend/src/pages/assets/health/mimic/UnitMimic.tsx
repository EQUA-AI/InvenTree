import { t } from '@lingui/core/macro';
import {
  Box,
  Group,
  Paper,
  ScrollArea,
  Stack,
  Table,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import { IconClockExclamation } from '@tabler/icons-react';
import { type ReactNode, useRef, useState } from 'react';
import operations from '../../operations.module.css';

import { shiftSpan } from '../../faults/occurrenceLabels';
import { StatusBadge as Badge } from '../common';
import { FreshnessLabel } from './FreshnessLabel';
import type { PartState } from './MimicParts';
import { type ReadingSection, ReadingSections } from './MimicReadings';
import { MimicSchematic } from './MimicSchematic';
import {
  dischargeFlowReading,
  displayFor,
  elementLabel,
  expandPointer,
  pollErrorLabel,
  reasonLabel,
  stateLabel,
  usable
} from './format';
import type { MimicData, MimicPoint } from './types';

export function UnitMimicHeader({
  data,
  unit,
  actions,
  headingId
}: { data: MimicData; unit: string; actions?: ReactNode; headingId?: string }) {
  const bay = data.bays.find((b) => b.key === unit);
  const statusElement = data.layout.elements.find(
    (e) => e.view === 'unit' && e.role === 'status'
  );
  const statusPoint = statusElement
    ? data.points[expandPointer(statusElement.pointer, unit)]
    : undefined;
  return (
    <Group justify='space-between'>
      <Group gap={4}>
        <Title order={headingId ? 4 : 5} id={headingId}>
          {t`Pump unit`}: {unit}
        </Title>
        {bay && (
          <Badge
            variant={bay.state === 'stale' ? 'outline' : 'light'}
            leftSection={
              bay.state === 'stale' ? (
                <IconClockExclamation size={14} />
              ) : undefined
            }
            color={
              bay.state === 'running'
                ? 'green'
                : bay.state === 'fault'
                  ? 'red'
                  : bay.state === 'stale'
                    ? 'gray'
                    : 'gray'
            }
          >
            {stateLabel(bay.state)}
          </Badge>
        )}
        <FreshnessLabel point={statusPoint} />
        {data.display_shifted && (
          <Badge
            color='gray'
            variant='outline'
          >{t`Recorded history shown as current`}</Badge>
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
export const PANEL = 'var(--mantine-color-default-hover)';

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
  header = true
}: Readonly<{
  data: MimicData;
  unit: string;
  /** Links and buttons for the heading, which differ by where this is shown. */
  actions?: ReactNode;
  header?: boolean;
}>) {
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const display = displayFor(data, unit);
  const flowValue = dischargeFlowReading(data, unit);

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
    const group = point.group || t`Other reviewed points`;
    groups[group] ??= [];
    groups[group].push(point);
  }
  // Drawn parts first, in the order the drawing numbers them; then the
  // systems that are not drawn, by name; then the points no system claims.
  const unnamed = t`Other reviewed points`;
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
    const target = [
      ...(list.current?.querySelectorAll('[data-section]') ?? [])
    ].find((node) => node.getAttribute('data-section') === keys[0]);
    target?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'instant'
        : 'smooth',
      block: 'center'
    });
  };

  return (
    <Stack gap='md'>
      {header && <UnitMimicHeader data={data} unit={unit} actions={actions} />}
      {data.display_shifted && (
        <Text
          className={operations.meta}
        >{t`Times moved ${shiftSpan(data.display_shift_seconds ?? 0) ?? t`an unspecified interval`} onto the display clock; plant times are earlier`}</Text>
      )}
      <Box hiddenFrom='sm'>
        <Title order={5}>{t`Key readings`}</Title>
        <Table>
          <Table.Tbody>
            {data.layout.elements
              .filter((e) => e.view === 'unit')
              .map((e) => {
                const point = data.points[expandPointer(e.pointer, unit)];
                return (
                  <Table.Tr key={e.id}>
                    <Table.Td>{elementLabel(e)}</Table.Td>
                    <Table.Td>
                      {usable(point)
                        ? display(point)
                        : `${t`Unavailable`} · ${reasonLabel(data.points[expandPointer(e.pointer, unit)]?.reason ?? 'not_bound')}`}
                      <br />
                      <FreshnessLabel point={point} />
                    </Table.Td>
                  </Table.Tr>
                );
              })}
          </Table.Tbody>
        </Table>
        <Text className={operations.meta}>{t`Drawing scrolls sideways`}</Text>
      </Box>
      <Paper withBorder p='xs' className={operations.mimicPanel}>
        <ScrollArea type='auto'>
          <Box miw={{ base: 720, sm: 600 }}>
            <MimicSchematic
              data={data}
              unit={unit}
              display={display}
              onPart={openPart}
            />
            {!!data.layout.parts?.length && (
              <Text size='xs' className={operations.meta} ta='center' mt='xs'>
                {t`A numbered part is listed below under the same number. A system without a number has no place on the drawing.`}
              </Text>
            )}
          </Box>
        </ScrollArea>
        <Group
          component='fieldset'
          bd={0}
          p={0}
          mx={0}
          mb={0}
          miw={0}
          gap='sm'
          mt='xs'
          aria-label={t`Schematic state legend`}
        >
          {['running', 'idle', 'fault', 'stale', 'unknown'].map((state) => (
            <Badge
              key={state}
              color={
                state === 'running'
                  ? 'green'
                  : state === 'fault'
                    ? 'red'
                    : 'gray'
              }
              variant='outline'
            >
              {stateLabel(state)}
              {state === 'stale' ? ' ▨' : state === 'unknown' ? ' ┄' : ''}
            </Badge>
          ))}
        </Group>
        <Text
          className={operations.meta}
        >{t`Pipes and valves drawn neutral: flow direction and valve position are not supplied.`}</Text>
      </Paper>
      <Box
        component='dl'
        aria-label={t`Process data`}
        m={0}
        className={operations.meta}
      >
        <Group gap='sm' align='start'>
          {[
            [
              t`Discharge flow`,
              flowValue ??
                `${t`Unavailable`} · ${reasonLabel(data.points[expandPointer(data.layout.elements.find((e) => e.id === 'pump-flow')?.pointer ?? '', unit)]?.reason ?? 'not_bound')}`
            ],
            [t`Flow direction`, t`Unavailable · not supplied`],
            [t`Communication status`, t`Unavailable · not supplied`],
            [
              t`Polling`,
              !data.enabled
                ? t`disabled`
                : data.last_error_code
                  ? t`Reported error (${pollErrorLabel(data.last_error_code)})`
                  : t`Enabled · no error reported`
            ]
          ].map(([label, value]) => (
            <Box key={label}>
              <Text component='dt' size='sm' fw={600}>
                {label}
              </Text>
              <Text component='dd' size='sm' m={0}>
                {value}
              </Text>
            </Box>
          ))}
        </Group>
      </Box>

      <Text
        size='xs'
        className={operations.meta}
      >{t`Open a reading group for values, units, quality and observation times.`}</Text>
      <TextInput
        classNames={{ input: operations.input }}
        label={t`Filter unit readings`}
        value={search}
        onChange={(event) => setSearch(event.currentTarget.value)}
      />
      <div ref={list}>
        <ReadingSections
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
