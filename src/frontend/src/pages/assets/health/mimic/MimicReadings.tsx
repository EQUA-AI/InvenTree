import { t } from '@lingui/core/macro';
import {
  Accordion,
  Badge,
  Group,
  Stack,
  Table,
  Text,
  Tooltip
} from '@mantine/core';
import { useState } from 'react';
import {
  MissingValue,
  ValueTooltip
} from '../../../../components/items/ValueTooltip';
import operations from '../../operations.module.css';
import { unitLabel } from '../../performance/format';
import { StatusBadge, statusTone } from '../StatusBadge';
import { readingSeverity } from './mimicVisuals';
import visual from './mimicVisuals.module.css';
import { formatInstant, useDisplayClock } from './observedTime';

import { UnavailableReading, failedReading } from './MimicValue';
import {
  numberText,
  readingLabel,
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
  const shown = points.filter(
    (point) => usable(point) && !failedReading(point)
  );
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
        lines.push(`${readingLabel(point)}: ${display(point)}`);
      }
      continue;
    }
    const values = group.map((point) => point.value as number);
    const low = Math.min(...values);
    const high = Math.max(...values);
    const span =
      low === high
        ? numberText(low, 2)
        : `${numberText(low, 2)} – ${numberText(high, 2)}`;
    const count = group.length;
    lines.push(
      `${t`${count} readings`}: ${span}${unit ? ` ${unitLabel(unit)}` : ''}`
    );
  }
  for (const point of shown) {
    if (typeof point.value !== 'number') {
      lines.push(`${readingLabel(point)}: ${display(point)}`);
    }
  }

  const limited = points.filter((point) => point.thresholds_configured);
  const judged = limited
    .filter((point) => usable(point) && !failedReading(point))
    .map((point) => point.condition);
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
  if (verdict === null) return null;
  const look: Record<string, { color: string; label: string }> = {
    critical: { color: 'red', label: t`Critical` },
    warning: { color: 'yellow', label: t`Warning` },
    normal: { color: 'green', label: t`Normal` },
    unknown: { color: 'gray', label: t`Unknown` }
  };
  return (
    <StatusBadge tone={statusTone(verdict)} color={look[verdict].color}>
      {look[verdict].label}
    </StatusBadge>
  );
}

/** A reading's verdict against its limits, in words. */
export function conditionText(point: MimicPoint): string {
  return !point.thresholds_configured
    ? t`No threshold configured`
    : point.reason
      ? t`Unknown`
      : point.condition === 'critical'
        ? t`Critical`
        : point.condition === 'warning'
          ? t`Warning`
          : point.condition === 'normal'
            ? t`Normal`
            : t`Unknown`;
}

export function PointTable({
  points,
  display = valueText
}: Readonly<{ points: MimicPoint[]; display?: Display }>) {
  const clock = useDisplayClock();
  return (
    <Table.ScrollContainer minWidth={650}>
      <Table striped className={visual.surface}>
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
          {points.map((point) => {
            const shown =
              point.value !== null && !point.reason && !failedReading(point);
            const value = display(point);
            const observed = formatInstant(point.observed_at, clock);
            const valueDetail =
              shown && value !== valueText(point)
                ? `${t`Source code`}: ${valueText(point)}`
                : null;
            const timeDetail = [
              clock.label,
              ...(point.age_seconds != null
                ? [
                    `${t`Age at response`}: ${Math.max(0, Math.round(point.age_seconds))} ${t`seconds`}`
                  ]
                : []),
              ...(point.unchanged_for_seconds != null &&
              point.unchanged_for_seconds > 0
                ? [
                    `${t`Unchanged for`}: ${unchangedLabel(point.unchanged_for_seconds)}`
                  ]
                : [])
            ].join(' · ');
            return (
              <Table.Tr key={point.pointer} data-point={point.pointer}>
                <Table.Td>
                  <ValueTooltip
                    focusable={false}
                    label={[point.pointer, observed, timeDetail]
                      .filter(Boolean)
                      .join(' · ')}
                    className={operations.phoneTarget}
                  >
                    {readingLabel(point)}
                  </ValueTooltip>
                </Table.Td>
                <Table.Td
                  className={visual.value}
                  data-severity={readingSeverity(point)}
                  ta={typeof point.value === 'number' ? 'right' : 'left'}
                >
                  {shown ? (
                    valueDetail ? (
                      <ValueTooltip
                        focusable={false}
                        label={valueDetail}
                        className={operations.phoneTarget}
                        accessibleLabel={`${value} · ${valueDetail}`}
                      >
                        {value}
                      </ValueTooltip>
                    ) : (
                      value
                    )
                  ) : (
                    <UnavailableReading
                      point={point}
                      label={readingLabel(point)}
                      display={display}
                    />
                  )}
                </Table.Td>
                <Table.Td>
                  {point.quality === 'good' ? (
                    t`Good`
                  ) : point.quality && point.quality !== 'unknown' ? (
                    t`Unusable quality`
                  ) : (
                    <MissingValue
                      reason={t`Quality not reported by the plant`}
                    />
                  )}
                </Table.Td>
                <Table.Td>
                  {observed ? (
                    <time dateTime={point.observed_at ?? undefined}>
                      {observed}
                    </time>
                  ) : (
                    <MissingValue
                      reason={t`Observation time not reported by the plant`}
                    />
                  )}
                </Table.Td>
                <Table.Td>
                  {!point.thresholds_configured || point.reason ? (
                    point.reason ? (
                      <UnavailableReading
                        point={{ ...point, value: null }}
                        label={t`Condition`}
                      />
                    ) : (
                      <MissingValue reason={conditionText(point)} />
                    )
                  ) : (
                    conditionText(point)
                  )}
                </Table.Td>
              </Table.Tr>
            );
          })}
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
        const detail =
          summary.lines.join(' · ') ||
          [
            ...new Set(
              section.points.map(
                (point) => reasonLabel(point.reason) || t`Reason not supplied`
              )
            )
          ].join(' · ');
        return (
          <Accordion.Item
            key={section.key}
            value={section.key}
            data-section={section.key}
          >
            <Tooltip
              label={detail}
              multiline
              maw={480}
              interactive
              events={{ hover: true, focus: true, touch: false }}
            >
              <Accordion.Control>
                <Group justify='space-between' wrap='wrap' gap='sm'>
                  <Stack gap={2} flex='1 1 220px' miw={0}>
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
                  </Stack>
                  <Group gap='xs' wrap='wrap'>
                    {section.points.length > 0 &&
                    section.points.every(failedReading) ? (
                      <StatusBadge
                        tone='failed'
                        color='red'
                      >{t`Read failed`}</StatusBadge>
                    ) : summary.unavailable === section.points.length ? (
                      <MissingValue
                        reason={detail || t`Reason not supplied`}
                        focusable={false}
                      />
                    ) : (
                      <VerdictBadge verdict={summary.verdict} />
                    )}
                  </Group>
                </Group>
              </Accordion.Control>
            </Tooltip>
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
