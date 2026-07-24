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

import { PanelGroup } from '../../components/panels/PanelGroup';
import TaskCalendar from '../../components/tasks/TaskCalendar';
import TaskGantt from '../../components/tasks/TaskGantt';
import Kanban from './Kanban';

/**
 * Tasks index page.
 *
 * Hosts the work-order board and the (forthcoming) scheduling views in one
 * PanelGroup, mirroring BuildIndex. The Board panel is the existing Kanban
 * board unchanged; Calendar and Timeline are placeholders until S7/S8.
 */
export default function TasksIndex() {
  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'board',
        label: t`Board`,
        icon: <IconLayoutKanban />,
        content: <Kanban />
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
      <PanelGroup
        pageKey='tasks-index'
        panels={panels}
        model={ModelType.kanbancard}
        id={null}
      />
    </Stack>
  );
}
