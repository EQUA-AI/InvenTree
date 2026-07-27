import { t } from '@lingui/core/macro';
import { Button, Stack, Text } from '@mantine/core';
import {
  IconActivityHeartbeat,
  IconInfoCircle,
  IconListCheck,
  IconPlayerPlay,
  IconSparkles,
  IconTool
} from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ScopedChatPanel } from '../../components/aichat/ScopedChatPanel';
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
import { WorkOrderCreateModal } from '../maintenance/components/WorkOrderCreateModal';
import { StartRepairModal } from './StartRepairModal';
import { MachineHealthPanel } from './health/MachineHealthPanel';

export default function MachineDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [createRepairOpen, setCreateRepairOpen] = useState(false);
  const [startRepairOpen, setStartRepairOpen] = useState(false);

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
      },
      {
        // Internal assets have no sales customer; the client is what makes them
        // scope-resolvable, so it is shown alongside rather than instead.
        name: 'client_name',
        type: 'text',
        label: t`Client`
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
        // Health sits immediately after Details: an operator opening a machine
        // asks how it is doing before asking what it is made of.
        name: 'health',
        label: t`Health`,
        icon: <IconActivityHeartbeat />,
        content: machine?.pk ? (
          <MachineHealthPanel machineId={machine.pk} />
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
      }),
      {
        // Last, because it answers questions about the tabs before it. The
        // panel resolves its own pinned context server-side and renders an
        // unavailable state when the deployment has not enabled the machine
        // context, so mounting it unconditionally is safe.
        name: 'ask-aimms',
        label: t`Ask AIMMS`,
        icon: <IconSparkles />,
        content: machine?.pk ? (
          <ScopedChatPanel contextType='machine' objectId={machine.pk} />
        ) : null
      }
    ];
  }, [machine, detailsLeft, detailsRight, detailsDescription]);

  return (
    <InstanceDetail query={instanceQuery}>
      <Stack>
        <PageDetail
          title={machine?.name ?? t`Machine Detail`}
          breadcrumbs={[{ name: t`Machines`, url: '/machines/index/' }]}
          actions={[
            // Create repair plans work for this asset. Starting it is a
            // separate, readiness-gated transition and is not offered here.
            <Button
              key='create-repair'
              leftSection={<IconTool size={16} />}
              onClick={() => setCreateRepairOpen(true)}
              disabled={!machine?.pk}
            >
              {t`Create repair`}
            </Button>,
            // Starting is a separate, readiness-gated transition. The modal
            // asks the server what is startable rather than deciding here.
            <Button
              key='start-repair'
              variant='light'
              leftSection={<IconPlayerPlay size={16} />}
              onClick={() => setStartRepairOpen(true)}
              disabled={!machine?.pk}
            >
              {t`Start repair`}
            </Button>
          ]}
        />
        <PanelGroup
          pageKey='asset-machine-detail'
          panels={machinePanels}
          instance={machine}
          model={ModelType.assetmachine}
          id={machine?.pk ?? null}
        />
        {machine?.pk && (
          <WorkOrderCreateModal
            opened={createRepairOpen}
            onClose={() => setCreateRepairOpen(false)}
            machineId={machine.pk}
            origin='manual'
            onCreated={(result) =>
              navigate(`/maintenance/work-orders/${result.work_order_id}/`)
            }
          />
        )}
        {machine?.pk && (
          <StartRepairModal
            opened={startRepairOpen}
            onClose={() => setStartRepairOpen(false)}
            machineId={machine.pk}
            onStarted={(repair) =>
              repair.work_order_id &&
              navigate(`/maintenance/work-orders/${repair.work_order_id}/`)
            }
          />
        )}
      </Stack>
    </InstanceDetail>
  );
}
