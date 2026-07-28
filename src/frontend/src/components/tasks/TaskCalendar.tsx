import type {
  EventClickArg,
  EventContentArg,
  EventDropArg,
  EventInput
} from '@fullcalendar/core';
import type { EventResizeDoneArg } from '@fullcalendar/interaction';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import type { KanbanPriority, WorkOrder } from '@lib/types/Tasks';
import { Badge, Group, Text } from '@mantine/core';
import { useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import useCalendar from '../../hooks/UseCalendar';
import { useTaskSchedule } from '../../hooks/UseTaskSchedule';
import { useUserState } from '../../states/UserState';
import Calendar from '../calendar/Calendar';

const priorityColors: Record<KanbanPriority, string> = {
  low: 'var(--mantine-color-teal-6)',
  medium: 'var(--mantine-color-yellow-6)',
  high: 'var(--mantine-color-red-6)'
};

/**
 * Calendar view of scheduled work orders (S7).
 *
 * Reuses the shared Calendar shell and useCalendar window/filter conventions;
 * the card list endpoint honours the ``min_date``/``max_date`` the hook sends.
 * Events span the scheduled window, falling back to the due date. Drag moves the
 * work order (preserving duration); resizing an edge changes its duration. Both
 * go through the governed command path via useTaskSchedule, and revert on
 * rejection. Editing is gated on the ``work_order`` change role.
 */
export default function TaskCalendar() {
  const navigate = useNavigate();
  const user = useUserState();
  const { moveWorkOrder, resizeWorkOrder, notifySaved, notifyError } =
    useTaskSchedule();

  const canEdit = user.hasChangeRole(UserRoles.work_order);

  const calendarState = useCalendar({
    name: 'work-orders',
    endpoint: ApiEndpoints.kanban_card_list
  });

  const events: EventInput[] = useMemo(() => {
    const cards = (calendarState.data ?? []) as WorkOrder[];

    return cards
      .filter((card) => card.scheduled_start || card.due_date)
      .map((card) => {
        const scheduled = Boolean(card.scheduled_start);
        const start = card.scheduled_start ?? card.due_date ?? undefined;
        const end =
          card.scheduled_end ??
          card.scheduled_start ??
          card.due_date ??
          undefined;
        const color = priorityColors[card.priority] ?? priorityColors.medium;

        return {
          id: String(card.id),
          title: card.title,
          start: start ?? undefined,
          end: end ?? undefined,
          // Due-date-only cards are shown but not yet placed on a timeline; a
          // scheduled card is draggable/resizable when the user may edit.
          allDay: !scheduled,
          startEditable: canEdit && scheduled,
          durationEditable: canEdit && scheduled,
          backgroundColor: color,
          borderColor: color,
          extendedProps: { card }
        };
      });
  }, [calendarState.data, canEdit]);

  const onEventDrop = useCallback(
    async (info: EventDropArg) => {
      const card = info.event.extendedProps.card as WorkOrder;
      if (!info.event.start) {
        info.revert();
        return;
      }
      try {
        await moveWorkOrder(card, info.event.start, info.event.end);
        notifySaved();
      } catch (error) {
        info.revert();
        notifyError(error);
      }
    },
    [moveWorkOrder, notifySaved, notifyError]
  );

  const onEventResize = useCallback(
    async (info: EventResizeDoneArg) => {
      const card = info.event.extendedProps.card as WorkOrder;
      if (!info.event.end) {
        info.revert();
        return;
      }
      try {
        await resizeWorkOrder(card, info.event.end);
        notifySaved();
      } catch (error) {
        info.revert();
        notifyError(error);
      }
    },
    [resizeWorkOrder, notifySaved, notifyError]
  );

  const onEventClick = useCallback(
    (info: EventClickArg) => {
      navigate(`/maintenance/work-orders/${info.event.id}/`);
    },
    [navigate]
  );

  const renderEvent = useCallback((arg: EventContentArg) => {
    const card = arg.event.extendedProps.card as WorkOrder;
    return (
      <Group
        gap={4}
        wrap='nowrap'
        style={{ paddingLeft: 4, overflow: 'hidden' }}
      >
        <Text size='xs' fw={600} truncate>
          {card.title}
        </Text>
        {card.machine_name && (
          <Badge size='xs' variant='transparent' color='gray'>
            {card.machine_name}
          </Badge>
        )}
      </Group>
    );
  }, []);

  const tooltip = useCallback((arg: EventContentArg) => {
    const card = arg.event.extendedProps.card as WorkOrder;
    return (
      <Text size='sm'>
        {card.title}
        {card.assigned_to_name ? ` — ${card.assigned_to_name}` : ''}
      </Text>
    );
  }, []);

  return (
    <Calendar
      enableSearch
      enableRefresh
      state={calendarState}
      events={events}
      editable={canEdit}
      eventContent={renderEvent}
      eventTooltipContent={tooltip}
      eventClick={onEventClick}
      eventDrop={onEventDrop}
      eventResize={onEventResize}
    />
  );
}
