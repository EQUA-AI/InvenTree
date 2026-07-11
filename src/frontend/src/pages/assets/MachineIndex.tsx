import { t } from '@lingui/core/macro';
import { Stack } from '@mantine/core';
import { IconTools } from '@tabler/icons-react';
import { useMemo } from 'react';

import { PageDetail } from '../../components/nav/PageDetail';
import type { PanelType } from '@lib/types/Panel';
import { ModelType } from '@lib/enums/ModelType';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { AssetMachineTable } from '../../tables/assets/AssetMachineTable';

/**
 * Index page for listing equipment asset machines.
 */
export default function MachineIndex() {
  const panels: PanelType[] = useMemo(() => {
    return [
      {
        name: 'machines',
        label: t`Machines`,
        content: <AssetMachineTable />,
        icon: <IconTools />
      }
    ];
  }, []);

  return (
    <Stack>
      <PageDetail title={t`Machines`} actions={[]} />
      <PanelGroup
        pageKey='asset-machine-index'
        panels={panels}
        model={ModelType.assetmachine}
        id={null}
      />
    </Stack>
  );
}
