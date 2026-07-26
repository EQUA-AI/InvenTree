import { t } from '@lingui/core/macro';
import { Stack } from '@mantine/core';
import {
  IconCalendar,
  IconLayoutKanban,
  IconTimeline
} from '@tabler/icons-react';
import { useMemo } from 'react';

import { ModelType } from '@lib/enums/ModelType';
import type { PanelType } from '@lib/types/Panel';

import PageTitle from '../../components/nav/PageTitle';
import { PanelGroup } from '../../components/panels/PanelGroup';
import TaskCalendar from '../../components/tasks/TaskCalendar';
import TaskGantt from '../../components/tasks/TaskGantt';
import MaintenanceBoard from './MaintenanceBoard';

/**
 * Maintenance workspace.
 *
 * Hosts the work-order Board, Calendar and Timeline in one PanelGroup, mirroring
 * BuildIndex. The panel is taken from the URL (`/maintenance/board/`,
 * `/maintenance/calendar/`, `/maintenance/timeline/`) so those views are
 * deep-linkable; `/maintenance/` alone falls back to the user's last panel.
 *
 * The workspace is named Maintenance, but the API namespace and Django models
 * behind it remain `tasks`/`KanbanCard`: renaming those would be a high-risk
 * data migration with no user-visible value.
 */
export default function MaintenanceIndex() {
  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'board',
        label: t`Board`,
        icon: <IconLayoutKanban />,
        content: <MaintenanceBoard />
      },
      {
        name: 'calendar',
        label: t`Calendar`,
        icon: <IconCalendar />,
        content: <TaskCalendar />
      },
      {
        name: 'timeline',
        label: t`Timeline`,
        icon: <IconTimeline />,
        content: <TaskGantt />
      }
    ],
    []
  );

  return (
    <Stack>
      <PageTitle title={t`Maintenance`} />
      <PanelGroup
        // Unchanged from the Tasks workspace so an existing panel preference
        // survives the rename.
        pageKey='tasks-index'
        panels={panels}
        model={ModelType.kanbancard}
        id={null}
      />
    </Stack>
  );
}
