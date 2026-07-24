import { UserRoles } from '@lib/enums/Roles';
import type { KanbanCard, KanbanPriority } from '@lib/types/Tasks';
import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Box,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconChevronLeft,
  IconChevronRight
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useScheduleWindow } from '../../hooks/UseScheduleWindow';
import { useTaskSchedule } from '../../hooks/UseTaskSchedule';
import { useUserState } from '../../states/UserState';

type Zoom = 'day' | 'week' | 'month';
type GroupBy = 'machine' | 'assignee';

const ZOOM: Record<Zoom, { px: number; days: number }> = {
  day: { px: 42, days: 21 },
  week: { px: 12, days: 84 },
  month: { px: 4, days: 200 }
};

const LABEL_W = 180;
const HEADER_H = 30;
const GROUP_H = 26;
const ROW_H = 30;
const BAR_H = 18;

const priorityColors: Record<KanbanPriority, string> = {
  low: 'var(--mantine-color-teal-6)',
  medium: 'var(--mantine-color-yellow-7)',
  high: 'var(--mantine-color-red-6)'
};

interface CardGeometry {
  card: KanbanCard;
  rowY: number;
  barLeft: number;
  barRight: number;
}

interface GroupLayout {
  label: string;
  y: number;
  cards: KanbanCard[];
}

/**
 * Custom timeline / Gantt for scheduled work orders (S8).
 *
 * Built directly on Mantine + SVG rather than a third-party Gantt: the plan
 * gated the SVAR renderer on an interaction spike that cannot be validated in
 * this environment, so this takes the sanctioned dependency-free alternative.
 * It consumes the schedule window (cards + dependencies + conflict warnings),
 * groups rows by machine or assignee, draws dependency links and conflict
 * badges, supports day/week/month zoom, and — for users with the change role —
 * drag-to-reschedule through the same governed command path as the calendar.
 */
export default function TaskGantt() {
  const navigate = useNavigate();
  const user = useUserState();
  const canEdit = user.hasChangeRole(UserRoles.work_order);
  const { moveWorkOrder, notifySaved, notifyError } = useTaskSchedule();

  const [zoom, setZoom] = useState<Zoom>('week');
  const [groupBy, setGroupBy] = useState<GroupBy>('machine');
  const [rangeStart, setRangeStart] = useState<Date>(() =>
    dayjs().subtract(3, 'day').startOf('day').toDate()
  );

  const { px, days } = ZOOM[zoom];
  const rangeEnd = useMemo(
    () => dayjs(rangeStart).add(days, 'day').toDate(),
    [rangeStart, days]
  );

  const query = useScheduleWindow(rangeStart, rangeEnd);
  const data = query.data;

  // Cards placed on the timeline are those with a scheduled start.
  const scheduledCards = useMemo(
    () => (data?.cards ?? []).filter((c) => c.scheduled_start),
    [data]
  );

  const conflictIds = useMemo(() => {
    const ids = new Set<number>();
    for (const warning of data?.warnings ?? []) {
      for (const id of warning.card_ids) {
        ids.add(id);
      }
    }
    return ids;
  }, [data]);

  const groups: GroupLayout[] = useMemo(() => {
    const byKey = new Map<string, KanbanCard[]>();
    for (const card of scheduledCards) {
      const key =
        groupBy === 'machine'
          ? (card.machine_name ?? t`Unassigned machine`)
          : (card.assigned_to_name ?? t`Unassigned`);
      const list = byKey.get(key) ?? [];
      list.push(card);
      byKey.set(key, list);
    }

    let y = HEADER_H;
    const result: GroupLayout[] = [];
    for (const [label, cards] of [...byKey.entries()].sort()) {
      result.push({ label, y, cards });
      y += GROUP_H + cards.length * ROW_H;
    }
    return result;
  }, [scheduledCards, groupBy]);

  const { geometry, totalHeight } = useMemo(() => {
    const geo = new Map<number, CardGeometry>();
    let maxY = HEADER_H;

    for (const group of groups) {
      let y = group.y + GROUP_H;
      for (const card of group.cards) {
        const start = dayjs(card.scheduled_start);
        const end = card.scheduled_end
          ? dayjs(card.scheduled_end)
          : start.add(2, 'hour');
        const leftDays = start.diff(dayjs(rangeStart), 'day', true);
        const durDays = Math.max(end.diff(start, 'day', true), 0.08);
        const barLeft = LABEL_W + leftDays * px;
        const barRight = barLeft + durDays * px;
        geo.set(card.id, { card, rowY: y, barLeft, barRight });
        y += ROW_H;
      }
      maxY = Math.max(maxY, y);
    }
    return { geometry: geo, totalHeight: maxY };
  }, [groups, rangeStart, px]);

  const totalWidth = LABEL_W + days * px;
  const todayX = LABEL_W + dayjs().diff(dayjs(rangeStart), 'day', true) * px;
  const todayVisible = todayX >= LABEL_W && todayX <= totalWidth;

  // Axis ticks: every day (day zoom), Mondays (week), month starts (month).
  const ticks = useMemo(() => {
    const out: { x: number; label: string; major: boolean }[] = [];
    for (let i = 0; i <= days; i++) {
      const d = dayjs(rangeStart).add(i, 'day');
      const x = LABEL_W + i * px;
      if (zoom === 'day') {
        out.push({ x, label: d.format('DD ddd'), major: d.day() === 1 });
      } else if (zoom === 'week' && d.day() === 1) {
        out.push({ x, label: d.format('DD MMM'), major: d.date() <= 7 });
      } else if (zoom === 'month' && d.date() === 1) {
        out.push({ x, label: d.format('MMM YY'), major: true });
      }
    }
    return out;
  }, [rangeStart, days, px, zoom]);

  // ── Drag to reschedule ──────────────────────────────────────
  const dragRef = useRef<{ card: KanbanCard; startX: number } | null>(null);
  const [dragCardId, setDragCardId] = useState<number | null>(null);
  const [dragDx, setDragDx] = useState(0);

  useEffect(() => {
    if (dragCardId == null) return;

    const onMove = (e: PointerEvent) => {
      if (dragRef.current) setDragDx(e.clientX - dragRef.current.startX);
    };
    const onUp = async (e: PointerEvent) => {
      const drag = dragRef.current;
      dragRef.current = null;
      setDragCardId(null);
      setDragDx(0);
      if (!drag) return;

      const totalDx = e.clientX - drag.startX;
      const dayDelta = Math.round(totalDx / px);
      const card = drag.card;

      // A negligible move is a click: open the work order.
      if (Math.abs(totalDx) < 4) {
        navigate(`/tasks/work-orders/${card.id}/`);
        return;
      }
      if (dayDelta === 0 || !card.scheduled_start) return;

      const newStart = dayjs(card.scheduled_start).add(dayDelta, 'day');
      const newEnd = card.scheduled_end
        ? dayjs(card.scheduled_end).add(dayDelta, 'day')
        : null;
      try {
        await moveWorkOrder(
          card,
          newStart.toDate(),
          newEnd ? newEnd.toDate() : null
        );
        notifySaved();
      } catch (error) {
        notifyError(error);
      }
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [dragCardId, px, moveWorkOrder, navigate, notifySaved, notifyError]);

  const onBarPointerDown = (e: React.PointerEvent, card: KanbanCard) => {
    dragRef.current = { card, startX: e.clientX };
    setDragCardId(card.id);
    setDragDx(0);
  };

  const shiftRange = (direction: number) =>
    setRangeStart(
      dayjs(rangeStart)
        .add(direction * Math.round(days / 2), 'day')
        .toDate()
    );

  const rangeLabel = `${dayjs(rangeStart).format('DD MMM')} – ${dayjs(rangeEnd).format('DD MMM YYYY')}`;

  return (
    <Stack gap='sm'>
      <Group justify='space-between' wrap='wrap' gap='sm'>
        <Group gap='xs'>
          <ActionIcon
            variant='default'
            aria-label='gantt-prev'
            onClick={() => shiftRange(-1)}
          >
            <IconChevronLeft size={16} />
          </ActionIcon>
          <ActionIcon
            variant='default'
            aria-label='gantt-today'
            onClick={() =>
              setRangeStart(dayjs().subtract(3, 'day').startOf('day').toDate())
            }
          >
            <Text size='xs' fw={600}>
              {t`Today`}
            </Text>
          </ActionIcon>
          <ActionIcon
            variant='default'
            aria-label='gantt-next'
            onClick={() => shiftRange(1)}
          >
            <IconChevronRight size={16} />
          </ActionIcon>
          <Text size='sm' fw={500}>
            {rangeLabel}
          </Text>
        </Group>
        <Group gap='sm'>
          <SegmentedControl
            size='xs'
            value={groupBy}
            onChange={(value) => setGroupBy(value as GroupBy)}
            data={[
              { value: 'machine', label: t`By machine` },
              { value: 'assignee', label: t`By assignee` }
            ]}
          />
          <SegmentedControl
            size='xs'
            value={zoom}
            onChange={(value) => setZoom(value as Zoom)}
            data={[
              { value: 'day', label: t`Day` },
              { value: 'week', label: t`Week` },
              { value: 'month', label: t`Month` }
            ]}
          />
        </Group>
      </Group>

      {(data?.warnings.length ?? 0) > 0 && (
        <Alert
          color='orange'
          variant='light'
          icon={<IconAlertTriangle size={16} />}
          title={t`Scheduling conflicts`}
          p='xs'
        >
          <Text size='xs'>
            {t`${data?.warnings.length ?? 0} overlap warning(s) in this window.`}
          </Text>
        </Alert>
      )}

      {query.isLoading ? (
        <Group justify='center' p='xl'>
          <Loader />
        </Group>
      ) : scheduledCards.length === 0 ? (
        <Paper withBorder p='xl'>
          <Text c='dimmed' ta='center'>
            {t`No scheduled work orders in this window.`}
          </Text>
        </Paper>
      ) : (
        <Paper withBorder style={{ overflowX: 'auto' }}>
          <Box
            pos='relative'
            style={{
              width: totalWidth,
              height: totalHeight,
              minWidth: '100%',
              userSelect: dragCardId != null ? 'none' : undefined
            }}
          >
            {/* Axis grid + labels */}
            {ticks.map((tick) => (
              <Box
                key={tick.x}
                pos='absolute'
                style={{
                  left: tick.x,
                  top: 0,
                  bottom: 0,
                  width: 1,
                  background: tick.major
                    ? 'var(--mantine-color-gray-4)'
                    : 'var(--mantine-color-gray-2)'
                }}
              >
                <Text
                  size='9px'
                  c='dimmed'
                  style={{
                    position: 'absolute',
                    left: 3,
                    top: 4,
                    whiteSpace: 'nowrap'
                  }}
                >
                  {tick.label}
                </Text>
              </Box>
            ))}

            {/* Today marker */}
            {todayVisible && (
              <Box
                pos='absolute'
                aria-label='gantt-today-marker'
                style={{
                  left: todayX,
                  top: HEADER_H,
                  bottom: 0,
                  width: 2,
                  background: 'var(--mantine-color-blue-5)',
                  zIndex: 1
                }}
              />
            )}

            {/* Group headers + row labels */}
            {groups.map((group) => (
              <Box key={group.label}>
                <Box
                  pos='absolute'
                  style={{
                    left: 0,
                    top: group.y,
                    width: totalWidth,
                    height: GROUP_H,
                    background: 'var(--mantine-color-gray-1)',
                    borderTop: '1px solid var(--mantine-color-gray-3)'
                  }}
                >
                  <Text size='xs' fw={700} style={{ padding: '5px 8px' }}>
                    {group.label}
                  </Text>
                </Box>
              </Box>
            ))}

            {/* Dependency links */}
            <svg
              width={totalWidth}
              height={totalHeight}
              style={{
                position: 'absolute',
                left: 0,
                top: 0,
                pointerEvents: 'none'
              }}
              role='img'
            >
              <title>{t`Work order dependency links`}</title>
              <defs>
                <marker
                  id='gantt-arrow'
                  markerWidth='6'
                  markerHeight='6'
                  refX='5'
                  refY='3'
                  orient='auto'
                >
                  <path
                    d='M0,0 L6,3 L0,6 Z'
                    fill='var(--mantine-color-gray-6)'
                  />
                </marker>
              </defs>
              {(data?.dependencies ?? []).map((dep) => {
                const from = geometry.get(dep.from_card);
                const to = geometry.get(dep.to_card);
                if (!from || !to) return null;
                const x1 = from.barRight;
                const y1 = from.rowY + ROW_H / 2;
                const x2 = to.barLeft;
                const y2 = to.rowY + ROW_H / 2;
                return (
                  <path
                    key={dep.id}
                    d={`M ${x1} ${y1} C ${x1 + 20} ${y1}, ${x2 - 20} ${y2}, ${x2} ${y2}`}
                    stroke='var(--mantine-color-gray-6)'
                    strokeWidth={1.2}
                    fill='none'
                    markerEnd='url(#gantt-arrow)'
                  />
                );
              })}
            </svg>

            {/* Bars */}
            {[...geometry.values()].map(({ card, rowY, barLeft, barRight }) => {
              const isDragging = dragCardId === card.id;
              const conflicted = conflictIds.has(card.id);
              const width = Math.max(barRight - barLeft, 6);
              return (
                <Tooltip
                  key={card.id}
                  label={
                    <Text size='xs'>
                      {card.title}
                      {card.assigned_to_name
                        ? ` — ${card.assigned_to_name}`
                        : ''}
                    </Text>
                  }
                  openDelay={400}
                  position='top'
                >
                  <Box
                    pos='absolute'
                    onPointerDown={(e) =>
                      canEdit
                        ? onBarPointerDown(e, card)
                        : navigate(`/tasks/work-orders/${card.id}/`)
                    }
                    style={{
                      left: barLeft + (isDragging ? dragDx : 0),
                      top: rowY + (ROW_H - BAR_H) / 2,
                      width,
                      height: BAR_H,
                      background:
                        priorityColors[card.priority] ?? priorityColors.medium,
                      border: conflicted
                        ? '2px solid var(--mantine-color-red-7)'
                        : '1px solid rgba(0,0,0,0.15)',
                      borderRadius: 4,
                      cursor: canEdit ? 'grab' : 'pointer',
                      zIndex: isDragging ? 5 : 2,
                      opacity: isDragging ? 0.8 : 1,
                      overflow: 'hidden',
                      display: 'flex',
                      alignItems: 'center'
                    }}
                  >
                    <Text
                      size='9px'
                      c='white'
                      fw={600}
                      style={{
                        padding: '0 4px',
                        whiteSpace: 'nowrap',
                        pointerEvents: 'none'
                      }}
                    >
                      {card.title}
                    </Text>
                  </Box>
                </Tooltip>
              );
            })}
          </Box>
        </Paper>
      )}
    </Stack>
  );
}
