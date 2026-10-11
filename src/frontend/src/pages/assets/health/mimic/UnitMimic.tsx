import { t } from '@lingui/core/macro';
import {
  Badge,
  Group,
  Paper,
  Stack,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import { type ReactNode, useRef, useState } from 'react';

import type { PartState } from './MimicParts';
import { type ReadingSection, ReadingSections } from './MimicReadings';
import { MimicSchematic } from './MimicSchematic';
import { displayFor, stateLabel } from './format';
import type { MimicData, MimicPoint } from './types';

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
  actions
}: Readonly<{
  data: MimicData;
  unit: string;
  /** Links and buttons for the heading, which differ by where this is shown. */
  actions?: ReactNode;
}>) {
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const bay = data.bays.find((b) => b.key === unit);
  const display = displayFor(data, unit);

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
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  return (
    <Stack gap='md'>
      <Group justify='space-between'>
        <Group gap='sm'>
          <Title order={4}>
            {t`Pump unit`}: {unit}
          </Title>
          {bay && (
            <Badge
              variant='light'
              color={
                bay.state === 'running'
                  ? 'green'
                  : bay.state === 'fault'
                    ? 'red'
                    : bay.state === 'stale'
                      ? 'yellow'
                      : 'gray'
              }
            >
              {stateLabel(bay.state)}
            </Badge>
          )}
          {bay && !bay.active && (
            <Badge variant='outline' color='gray'>
              {t`Inactive equipment`}
            </Badge>
          )}
        </Group>
        <Group gap='sm'>{actions}</Group>
      </Group>
      <Paper withBorder radius='md' p='sm' bg={PANEL}>
        <MimicSchematic
          data={data}
          unit={unit}
          display={display}
          onPart={openPart}
        />
        {!!data.layout.parts?.length && (
          <Text size='xs' c='dimmed' ta='center' mt='xs'>
            {t`A numbered part is listed below under the same number. A system without a number has no place on the drawing.`}
          </Text>
        )}
      </Paper>
      <TextInput
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
