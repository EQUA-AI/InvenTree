import { t } from '@lingui/core/macro';
import { Accordion, Badge, Group, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';

import {
  numberText,
  reasonLabel,
  unchangedLabel,
  usable,
  valueText
} from './format';
import type { MimicPoint } from './types';

export type ReadingSection = {
  key: string;
  title: string;
  points: MimicPoint[];
  /** The number the section's part carries on the drawing, if it is drawn. */
  number?: number;
};

/** How a reading's value is written; a status code may be given in words. */
export type Display = (point: MimicPoint) => string;

type Verdict = 'critical' | 'warning' | 'normal' | 'unknown' | null;

/**
 * What a group of readings amounts to, without opening it.
 *
 * Only what can be said of every group, whatever it measures: how many
 * readings it has, how many cannot be shown, the span of each unit it reports
 * in, and the worst verdict among the readings that have a limit to be judged
 * against. Nothing here ranks a temperature against a vibration, or calls a
 * group healthy because nobody has given it limits.
 */
export function summarize(points: MimicPoint[], display: Display = valueText) {
  const shown = points.filter(usable);
  const byUnit = new Map<string, MimicPoint[]>();
  for (const point of shown) {
    if (typeof point.value !== 'number') continue;
    byUnit.set(point.unit, [...(byUnit.get(point.unit) ?? []), point]);
  }

  const lines: string[] = [];
  const units = [...byUnit].sort((a, b) => b[1].length - a[1].length);
  for (const [unit, group] of units) {
    // Two readings are two facts; only a family is worth a span.
    if (group.length <= 2) {
      for (const point of group) {
        lines.push(`${point.label}: ${display(point)}`);
      }
      continue;
    }
    const values = group.map((point) => point.value as number);
    const span = `${numberText(Math.min(...values), 2)} – ${numberText(Math.max(...values), 2)}`;
    const count = group.length;
    lines.push(`${t`${count} readings`}: ${span}${unit ? ` ${unit}` : ''}`);
  }
  for (const point of shown) {
    if (typeof point.value !== 'number') {
      lines.push(`${point.label}: ${display(point)}`);
    }
  }

  const limited = points.filter((point) => point.thresholds_configured);
  const judged = limited.filter(usable).map((point) => point.condition);
  const verdict: Verdict = !limited.length
    ? null
    : judged.includes('critical')
      ? 'critical'
      : judged.includes('warning')
        ? 'warning'
        : judged.includes('normal')
          ? 'normal'
          : 'unknown';

  return {
    lines,
    verdict,
    unavailable: points.length - shown.length
  };
}

function VerdictBadge({ verdict }: Readonly<{ verdict: Verdict }>) {
  if (verdict === null) {
    return (
      <Badge variant='outline' color='gray'>{t`No threshold configured`}</Badge>
    );
  }
  const look: Record<string, { color: string; label: string }> = {
    critical: { color: 'red', label: t`Critical` },
    warning: { color: 'yellow', label: t`Warning` },
    normal: { color: 'green', label: t`Normal` },
    unknown: { color: 'gray', label: t`Unknown` }
  };
  return (
    <Badge variant='light' color={look[verdict].color}>
      {look[verdict].label}
    </Badge>
  );
}

export function PointTable({
  points,
  display = valueText
}: Readonly<{ points: MimicPoint[]; display?: Display }>) {
  return (
    <Table.ScrollContainer minWidth={650}>
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Signal`}</Table.Th>
            <Table.Th>{t`Value`}</Table.Th>
            <Table.Th>{t`Quality`}</Table.Th>
            <Table.Th>{t`Observed at`}</Table.Th>
            <Table.Th>{t`Condition`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {points.map((point) => (
            <Table.Tr key={point.pointer} data-point={point.pointer}>
              <Table.Td>
                {point.label}
                <Text size='xs' c='dimmed'>
                  {point.pointer}
                </Text>
              </Table.Td>
              <Table.Td>
                {display(point)}
                {point.reason && (
                  <Text size='xs'>{reasonLabel(point.reason)}</Text>
                )}
                {/* A status given in words keeps the code it was read from. */}
                {usable(point) && display(point) !== valueText(point) && (
                  <Text size='xs' c='dimmed'>
                    {t`Source code`}: {valueText(point)}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                {point.quality === 'good' ? t`Good` : t`Unusable or unknown`}
              </Table.Td>
              <Table.Td>
                {point.observed_at
                  ? new Date(point.observed_at).toLocaleString()
                  : t`No reading`}
                {point.age_seconds !== null && (
                  <Text size='xs'>
                    {t`Age at response`}: {Math.round(point.age_seconds)}{' '}
                    {t`seconds`}
                  </Text>
                )}
                {/* How long the reading has held the same number. Shown as a
                    fact rather than a verdict: a stopped bay's run status is
                    correctly constant for ever, while a winding temperature
                    that has not moved in days is an acquisition nobody is
                    watching. Only the person reading it can tell which. */}
                {point.unchanged_for_seconds !== null &&
                  point.unchanged_for_seconds > 0 && (
                    <Text size='xs' c='dimmed'>
                      {t`Unchanged for`}:{' '}
                      {unchangedLabel(point.unchanged_for_seconds)}
                    </Text>
                  )}
              </Table.Td>
              <Table.Td>
                {!point.thresholds_configured
                  ? t`No threshold configured`
                  : point.reason
                    ? t`Unknown`
                    : point.condition === 'critical'
                      ? t`Critical`
                      : point.condition === 'warning'
                        ? t`Warning`
                        : point.condition === 'normal'
                          ? t`Normal`
                          : t`Unknown`}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/**
 * Groups of readings, each shown as a line that opens into its table.
 *
 * A pump reports sixty-odd readings in a dozen systems. Laid end to end as
 * tables they ran to several screens, and the one that mattered had to be
 * scrolled for. Closed, each system is a line saying what it holds; the table
 * is drawn only once it is asked for, so a closed group costs nothing.
 */
export function ReadingSections({
  sections,
  display = valueText,
  open: held,
  onOpen
}: Readonly<{
  sections: ReadingSection[];
  display?: Display;
  /** Which sections are open, when the page above decides that. */
  open?: string[];
  onOpen?: (keys: string[]) => void;
}>) {
  const [own, setOwn] = useState<string[]>([]);
  const open = held ?? own;
  const setOpen = onOpen ?? setOwn;

  return (
    <Accordion
      multiple
      value={open}
      onChange={setOpen}
      variant='separated'
      chevronPosition='left'
    >
      {sections.map((section) => {
        const summary = summarize(section.points, display);
        const shown = summary.lines.slice(0, 3);
        const more = summary.lines.length - shown.length;
        const unavailable = summary.unavailable;
        return (
          <Accordion.Item
            key={section.key}
            value={section.key}
            data-section={section.key}
          >
            <Accordion.Control aria-label={section.title}>
              <Group justify='space-between' wrap='nowrap' gap='sm'>
                <Stack gap={2}>
                  <Group gap={8} wrap='nowrap'>
                    {section.number !== undefined && (
                      <Badge
                        size='sm'
                        variant='filled'
                        color='gray'
                        radius='xl'
                        px={6}
                        miw={22}
                        data-number={section.number}
                      >
                        {section.number}
                      </Badge>
                    )}
                    <Text fw={600} size='sm'>
                      {section.title}
                    </Text>
                  </Group>
                  <Text size='xs' c='dimmed'>
                    {[
                      ...shown,
                      ...(more > 0 ? [t`and ${more} more`] : [])
                    ].join(' · ') || t`No reading`}
                  </Text>
                </Stack>
                <Group gap='xs' wrap='nowrap'>
                  {unavailable > 0 && (
                    <Badge variant='light' color='gray'>
                      {t`${unavailable} unavailable`}
                    </Badge>
                  )}
                  <VerdictBadge verdict={summary.verdict} />
                </Group>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              {open.includes(section.key) && (
                <PointTable points={section.points} display={display} />
              )}
            </Accordion.Panel>
          </Accordion.Item>
        );
      })}
    </Accordion>
  );
}
