import { t } from '@lingui/core/macro';
import { Stack } from '@mantine/core';
import { IconClipboardList } from '@tabler/icons-react';
import { useMemo } from 'react';

import { PageDetail } from '../../components/nav/PageDetail';
import type { PanelType } from '../../components/panels/Panel';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { RepairPacketTable } from '../../tables/repair/RepairPacketTable';

/**
 * Index page for listing repair packets.
 */
export default function RepairPacketIndex() {
  const panels: PanelType[] = useMemo(() => {
    return [
      {
        name: 'packets',
        label: t`Repair Packets`,
        content: <RepairPacketTable />,
        icon: <IconClipboardList />
      }
    ];
  }, []);

  return (
    <Stack>
      <PageDetail title={t`Repair Packets`} actions={[]} />
      <PanelGroup
        pageKey='repair-packet-index'
        panels={panels}
        model='repairpacket'
        id={null}
      />
    </Stack>
  );
}
