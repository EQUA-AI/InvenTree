import { t } from '@lingui/core/macro';
import { Stack } from '@mantine/core';
import {
  IconBuildingFactory2,
  IconMapPinOff,
  IconTools
} from '@tabler/icons-react';
import { useMemo } from 'react';

import { ModelType } from '@lib/enums/ModelType';
import type { PanelType } from '@lib/types/Panel';
import { PageDetail } from '../../components/nav/PageDetail';
import { PanelGroup } from '../../components/panels/PanelGroup';
import classes from './MachineIndex.module.css';
import { LocationWorkspace } from './locations/LocationWorkspace';

/**
 * Index page for listing equipment asset machines.
 */
export default function MachineIndex() {
  const panels: PanelType[] = useMemo(() => {
    return [
      {
        name: 'sites',
        showHeadline: false,
        label: t`Sites & Facilities`,
        content: <LocationWorkspace />,
        icon: <IconBuildingFactory2 />
      },
      {
        name: 'machines',
        showHeadline: false,
        label: t`All Machines`,
        content: <LocationWorkspace view='all' />,
        icon: <IconTools />
      },
      {
        name: 'unassigned',
        showHeadline: false,
        label: t`Unassigned`,
        content: <LocationWorkspace view='unassigned' />,
        icon: <IconMapPinOff />
      }
    ];
  }, []);

  return (
    <Stack className={classes.workspace}>
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
