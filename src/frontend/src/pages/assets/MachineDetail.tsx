import { t } from '@lingui/core/macro';
import { Stack, Text } from '@mantine/core';
import { IconInfoCircle, IconListCheck, IconTool } from '@tabler/icons-react';
import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import AttachmentPanel from '../../components/panels/AttachmentPanel';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import type { PanelType } from '@lib/types/Panel';
import {
  type DetailsField,
  DetailsTable
} from '../../components/details/Details';
import { ItemDetailsGrid } from '../../components/details/ItemDetails';
import InstanceDetail from '../../components/nav/InstanceDetail';
import { PageDetail } from '../../components/nav/PageDetail';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { useInstance } from '../../hooks/UseInstance';
import { MachinePartTable } from '../../tables/assets/MachinePartTable';
import { MaintenanceRecordTable } from '../../tables/assets/MaintenanceRecordTable';

export default function MachineDetail() {
  const { id } = useParams();

  const { instance: machine, instanceQuery } = useInstance({
    endpoint: ApiEndpoints.asset_machine_list,
    pk: id,
    params: {}
  });

  // Left-hand details fields
  const detailsLeft: DetailsField[] = useMemo(
    () => [
      {
        name: 'name',
        type: 'text',
        label: t`Name`
      },
      {
        name: 'active',
        type: 'boolean',
        label: t`Active`
      },
      {
        name: 'location',
        type: 'text',
        label: t`Location`
      }
    ],
    []
  );

  // Right-hand details fields
  const detailsRight: DetailsField[] = useMemo(
    () => [
      {
        name: 'manufacturer',
        type: 'text',
        label: t`Manufacturer`
      },
      {
        name: 'model',
        type: 'text',
        label: t`Model`
      },
      {
        name: 'serial',
        type: 'text',
        label: t`Serial Number`
      },
      {
        name: 'customer_name',
        type: 'text',
        label: t`Customer`
      }
    ],
    []
  );

  // Description is a free-text field: render it across the full panel width,
  // wrapping on word boundaries rather than mid-word.
  const detailsDescription: DetailsField[] = useMemo(
    () => [
      {
        name: 'description',
        type: 'text',
        label: t`Description`,
        value_formatter: () => (
          <Text
            size='sm'
            style={{
              whiteSpace: 'pre-wrap',
              lineBreak: 'auto',
              wordBreak: 'break-word'
            }}
          >
            {machine?.description}
          </Text>
        )
      }
    ],
    [machine?.description]
  );

  const machinePanels: PanelType[] = useMemo(() => {
    return [
      {
        name: 'details',
        label: t`Details`,
        icon: <IconInfoCircle />,
        content: machine?.pk ? (
          <ItemDetailsGrid>
            <DetailsTable fields={detailsLeft} item={machine} />
            <DetailsTable fields={detailsRight} item={machine} />
            {!!machine.description && (
              <div style={{ gridColumn: '1 / -1' }}>
                <DetailsTable fields={detailsDescription} item={machine} />
              </div>
            )}
          </ItemDetailsGrid>
        ) : null
      },
      {
        name: 'parts',
        label: t`Installed Parts`,
        icon: <IconListCheck />,
        content: machine?.pk ? (
          <MachinePartTable machineId={machine.pk} />
        ) : null
      },
      {
        name: 'maintenance',
        label: t`Maintenance`,
        icon: <IconTool />,
        content: machine?.pk ? (
          <MaintenanceRecordTable machineId={machine.pk} />
        ) : null
      },
      AttachmentPanel({
        model_type: ModelType.assetmachine,
        model_id: machine?.pk
      })
    ];
  }, [machine, detailsLeft, detailsRight, detailsDescription]);

  return (
    <InstanceDetail query={instanceQuery}>
      <Stack>
        <PageDetail
          title={machine?.name ?? t`Machine Detail`}
          breadcrumbs={[{ name: t`Machines`, url: '/machines/index/' }]}
          actions={[]}
        />
        <PanelGroup
          pageKey='asset-machine-detail'
          panels={machinePanels}
          instance={machine}
          model={ModelType.assetmachine}
          id={machine?.pk ?? null}
        />
      </Stack>
    </InstanceDetail>
  );
}
