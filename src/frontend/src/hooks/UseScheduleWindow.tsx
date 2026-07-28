import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { WorkOrder } from '@lib/types/Tasks';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';

import { useApi } from '../contexts/ApiContext';

export interface ScheduleDependency {
  id: number;
  from_card: number;
  to_card: number;
  dependency_type: string;
  lag_minutes: number;
}

export interface ScheduleWarning {
  code: string;
  card_ids: number[];
  message: string;
  machine_id?: number;
  assigned_to_id?: number;
}

export interface ScheduleWindow {
  cards: WorkOrder[];
  dependencies: ScheduleDependency[];
  warnings: ScheduleWarning[];
}

/**
 * Load the schedule window (cards + dependencies + conflict warnings) for a date
 * range. This is the richer read the Timeline needs — the calendar uses the flat
 * card list, but the Gantt draws dependency links and conflict badges too.
 */
export function useScheduleWindow(rangeStart: Date, rangeEnd: Date) {
  const api = useApi();

  const minDate = dayjs(rangeStart).format('YYYY-MM-DD');
  const maxDate = dayjs(rangeEnd).format('YYYY-MM-DD');

  return useQuery<ScheduleWindow>({
    queryKey: ['kanban-schedule', minDate, maxDate],
    queryFn: async () => {
      const response = await api.get(apiUrl(ApiEndpoints.kanban_schedule), {
        params: { min_date: minDate, max_date: maxDate }
      });
      return response.data ?? { cards: [], dependencies: [], warnings: [] };
    }
  });
}
