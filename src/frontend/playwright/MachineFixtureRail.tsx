import { Text } from '@mantine/core';
import {
  IconActivityHeartbeat,
  IconChartLine,
  IconCube,
  IconHistory,
  IconInfoCircle,
  IconListCheck,
  IconPaperclip,
  IconTool
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { PanelGroup } from '../src/components/panels/PanelGroup';

/** Native machine PanelGroup, using the pump's actual tab order and labels. */
export function MachineFixtureRail({
  equipment,
  faults,
  performance
}: {
  equipment?: ReactNode;
  faults?: ReactNode;
  performance?: ReactNode;
}) {
  return (
    <PanelGroup
      pageKey='asset-machine-detail'
      panels={[
        { name: 'details', label: 'Details', icon: <IconInfoCircle /> },
        {
          name: 'equipment',
          label: 'Equipment view',
          icon: <IconCube />,
          content: equipment
        },
        { name: 'health', label: 'Health', icon: <IconActivityHeartbeat /> },
        {
          name: 'performance',
          label: 'Performance',
          icon: <IconChartLine />,
          content: performance
        },
        { name: 'parts', label: 'Parts', icon: <IconListCheck /> },
        { name: 'maintenance', label: 'Maintenance', icon: <IconTool /> },
        {
          name: 'fault-history',
          label: 'Fault History',
          icon: <IconHistory />,
          content: faults
        },
        { name: 'attachments', label: 'Attachments', icon: <IconPaperclip /> }
      ].map((panel) => ({
        ...panel,
        content: panel.content ?? <Text>Fixture destination</Text>
      }))}
    />
  );
}
