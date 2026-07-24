import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { KanbanCard } from '@lib/types/Tasks';
import { t } from '@lingui/core/macro';
import { notifications } from '@mantine/notifications';
import { IconCircleCheck } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import { useApi } from '../contexts/ApiContext';
import { showApiErrorMessage } from '../functions/notifications';

/**
 * Shared scheduling mutations for the Calendar and Timeline views (S7/S8).
 *
 * Every write goes through the governed command endpoints with the card's
 * ``lifecycle_version`` as the optimistic-concurrency token and a fresh
 * idempotency key, then invalidates the board, calendar and schedule caches so
 * all views converge. Mutations throw on failure so the caller (e.g. a calendar
 * drag) can revert its optimistic UI.
 */
export function useTaskSchedule() {
  const api = useApi();
  const queryClient = useQueryClient();

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['kanban-cards'] });
    queryClient.invalidateQueries({ queryKey: ['calendar'] });
    queryClient.invalidateQueries({ queryKey: ['kanban-schedule'] });
  }, [queryClient]);

  const newKey = () =>
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

  /**
   * Move a work order to a new window (drag). Both endpoints are sent, so the
   * duration is preserved exactly as the user positioned it.
   */
  const moveWorkOrder = useCallback(
    async (card: KanbanCard, start: Date, end: Date | null) => {
      await api.post(apiUrl(ApiEndpoints.kanban_command_schedule, card.id), {
        expected_version: card.lifecycle_version,
        scheduled_start: start.toISOString(),
        scheduled_end: end ? end.toISOString() : null,
        idempotency_key: newKey()
      });
      invalidate();
    },
    [api, invalidate]
  );

  /**
   * Resize a work order (drag an edge). Only the end moves; the backend derives
   * the new duration from the working-time span.
   */
  const resizeWorkOrder = useCallback(
    async (card: KanbanCard, end: Date) => {
      await api.post(apiUrl(ApiEndpoints.kanban_command_resize, card.id), {
        expected_version: card.lifecycle_version,
        scheduled_end: end.toISOString(),
        idempotency_key: newKey()
      });
      invalidate();
    },
    [api, invalidate]
  );

  const notifySaved = useCallback(() => {
    notifications.show({
      title: t`Schedule updated`,
      message: t`The work order was rescheduled.`,
      color: 'green',
      icon: <IconCircleCheck size={16} />
    });
  }, []);

  const notifyError = useCallback((error: unknown) => {
    showApiErrorMessage({
      error,
      title: t`Could not reschedule the work order`
    });
  }, []);

  return { moveWorkOrder, resizeWorkOrder, notifySaved, notifyError };
}
