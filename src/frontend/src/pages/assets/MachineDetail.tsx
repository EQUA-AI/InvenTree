import { t } from '@lingui/core/macro';
import { Button, Stack, Text } from '@mantine/core';
import {
  IconActivityHeartbeat,
  IconChartLine,
  IconCube,
  IconHistory,
  IconInfoCircle,
  IconListCheck,
  IconMessageChatbot,
  IconPlayerPlay,
  IconTool
} from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import AttachmentPanel from '../../components/panels/AttachmentPanel';

import { UserRoles } from '@lib/enums/Roles';
import { BarcodeActionDropdown } from '../../components/items/ActionDropdown';
import { useUserState } from '../../states/UserState';

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
import { openGlobalAIChat } from '../../states/AIChatState';
import { WorkOrderCreateModal } from '../maintenance/components/WorkOrderCreateModal';
import FaultHistoryPanel from './FaultHistoryPanel';
import { InstalledPartsPanel } from './InstalledPartsPanel';
import { StartRepairModal } from './StartRepairModal';
import { EquipmentViewPanel } from './equipment/EquipmentViewPanel';
import { MachineHealthPanel } from './health/MachineHealthPanel';
import PumpMimic from './health/PumpMimic';
import PumphouseMimic from './health/PumphouseMimic';
import { machineTrail } from './machineTrail';
import { MachineMaintenancePanel } from './maintenance/MachineMaintenancePanel';
import { PerformancePanel } from './performance/PerformancePanel';

import {
  MachineLocationCard,
  MachinePlacementPath
} from './locations/MachineLocationCard';
import { machinesReturnHref } from './locations/locationTree';

export default function MachineDetail() {
  // The route is `machine/:id/*`; what follows the id is the open tab.
  const { id, '*': rest } = useParams();
  const panel = rest?.split('/')[0];
  const navigate = useNavigate();
  const user = useUserState();
  const [searchParams] = useSearchParams();
  const [createRepairOpen, setCreateRepairOpen] = useState(false);
  const [startRepairOpen, setStartRepairOpen] = useState(false);

  // The return route is explicit (U2): a `from` parameter names the source
  // list (sites/all machines/unassigned) so its panel and filters are
  // preserved; without one the legacy breadcrumb contract is kept.
  const machinesUrl = useMemo(
    () => machinesReturnHref(searchParams),
    [searchParams]
  );

  const { instance: machine, instanceQuery } = useInstance({
    endpoint: ApiEndpoints.asset_machine_list,
    pk: id,
    params: {}
  });

  // R6 P08: a pump's process schematic is its station's mimic for its bay,
  // and it lives inside Equipment view rather than as a competing tab.
  const schematic =
    machine?.pk &&
    machine.asset_type === 'pump' &&
    machine.parent &&
    machine.source_key
      ? {
          stationId: machine.parent as number,
          unit: machine.source_key as string
        }
      : null;

  // The pump mimic tab became Equipment view's Process schematic; an old
  // link to it lands there, keeping whatever else the link carried.
  // Only once the loaded machine is the one in the URL: while another is
  // still being fetched, the previous machine (say the pump a station link
  // was followed from) is still in hand and must not be redirected to.
  useEffect(() => {
    if (
      panel !== 'mimic' ||
      machine?.asset_type !== 'pump' ||
      !machine.pk ||
      String(machine.pk) !== id
    )
      return;
    const next = new URLSearchParams(searchParams);
    if (schematic) next.set('view', 'schematic');
    navigate(`/machines/machine/${machine.pk}/equipment/?${next}`, {
      replace: true
    });
  }, [
    panel,
    id,
    machine?.pk,
    machine?.asset_type,
    !!schematic,
    searchParams,
    navigate
  ]);

  // The way back up: a pump's trail runs through the station it belongs to.
  const trail = useMemo(() => machineTrail(machine, panel), [machine, panel]);
  const breadcrumbs = useMemo(
    () => [{ name: t`Machines`, url: machinesUrl }, ...trail.breadcrumbs],
    [trail, machinesUrl]
  );

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
        label: t`Active record`
      },
      {
        name: 'location',
        type: 'text',
        label: t`Legacy location label`
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
          <Stack>
            <MachineLocationCard machineId={machine.pk} />
            <ItemDetailsGrid>
              <DetailsTable fields={detailsLeft} item={machine} />
              <DetailsTable fields={detailsRight} item={machine} />
              {!!machine.description && (
                <div style={{ gridColumn: '1 / -1' }}>
                  <DetailsTable fields={detailsDescription} item={machine} />
                </div>
              )}
            </ItemDetailsGrid>
          </Stack>
        ) : null
      },
      // R6 UI03: a pump opens the reference 3D model, with its process
      // schematic as the secondary view when it has one. The scene bundle
      // loads only when this panel is shown.
      ...(machine?.pk && machine.asset_type === 'pump'
        ? [
            {
              name: 'equipment',
              label: t`Equipment view`,
              icon: <IconCube />,
              content: (
                <EquipmentViewPanel
                  key={machine.pk}
                  machine={machine}
                  schematic={
                    schematic ? (
                      <PumpMimic
                        stationId={schematic.stationId}
                        unit={schematic.unit}
                      />
                    ) : undefined
                  }
                />
              )
            }
          ]
        : []),
      ...(machine?.asset_type === 'pumphouse'
        ? [
            {
              name: 'mimic',
              label: t`Pumphouse mimic`,
              icon: <IconActivityHeartbeat />,
              content: (
                <PumphouseMimic key={machine.pk} stationId={machine.pk} />
              )
            }
          ]
        : []),
      {
        // Health sits immediately after Details: an operator opening a machine
        // asks how it is doing before asking what it is made of.
        name: 'health',
        label: t`Health`,
        icon: <IconActivityHeartbeat />,
        content: machine?.pk ? (
          <MachineHealthPanel
            key={machine.pk}
            machineId={machine.pk}
            mimicHref={
              machine.asset_type === 'pumphouse'
                ? `/machines/machine/${machine.pk}/mimic`
                : schematic
                  ? `/machines/machine/${machine.pk}/equipment/?view=schematic`
                  : null
            }
          />
        ) : null
      },
      {
        // Performance follows Health: condition first, then how the machine
        // is running and what its parameters have been doing.
        name: 'performance',
        label: t`Performance`,
        icon: <IconChartLine />,
        content: machine?.pk ? <PerformancePanel machine={machine} /> : null
      },
      {
        name: 'parts',
        // R6 UI07: "Parts"; the native route key stays `parts`.
        label: t`Parts`,
        icon: <IconListCheck />,
        content: machine?.pk ? <InstalledPartsPanel machine={machine} /> : null
      },
      {
        name: 'maintenance',
        label: t`Maintenance`,
        icon: <IconTool />,
        // R6 UI06: Open work and Completed history (P12).
        content: machine?.pk ? (
          <MachineMaintenancePanel machineId={machine.pk} />
        ) : null
      },
      {
        name: 'fault-history',
        label: t`Fault History`,
        icon: <IconHistory />,
        content: machine?.pk ? (
          <FaultHistoryPanel machineId={machine.pk} />
        ) : null
      },
      AttachmentPanel({
        model_type: ModelType.assetmachine,
        model_id: machine?.pk
      })
    ];
  }, [
    machine,
    schematic?.stationId,
    schematic?.unit,
    detailsLeft,
    detailsRight,
    detailsDescription
  ]);

  return (
    <InstanceDetail query={instanceQuery}>
      <Stack>
        <PageDetail
          title={machine?.name ?? t`Machine Detail`}
          breadcrumbs={breadcrumbs}
          lastCrumb={trail.lastCrumb}
          actions={[
            // S32a: view the machine QR, or link/unlink a third-party tag.
            <BarcodeActionDropdown
              key='barcode_actions'
              model={ModelType.assetmachine}
              pk={machine?.pk}
              hash={machine?.barcode_hash}
              perm={user.hasChangeRole(UserRoles.work_order)}
            />,
            // S14 B5: opens the main chat drawer with this machine preloaded
            // as a VISIBLE routing hint — never authority. Every tool call
            // re-authorizes server-side; a denied or out-of-scope machine is
            // indistinguishable from a nonexistent one.
            <Button
              key='ask-ai'
              variant='light'
              leftSection={<IconMessageChatbot size={16} />}
              onClick={() =>
                machine?.pk &&
                openGlobalAIChat({
                  machineId: machine.pk,
                  machineName: machine.name ?? `Machine ${machine.pk}`
                })
              }
              disabled={!machine?.pk}
            >
              {t`Ask about this machine`}
            </Button>,
            // Create repair plans work for this asset. Starting it is a
            // separate, readiness-gated transition and is not offered here.
            <Button
              key='create-repair'
              leftSection={<IconTool size={16} />}
              onClick={() => setCreateRepairOpen(true)}
              disabled={!machine?.pk || !user.hasAddRole(UserRoles.work_order)}
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
              disabled={
                !machine?.pk ||
                !user.hasChangeRole(UserRoles.work_order) ||
                (!user.isSuperuser() &&
                  !user.user?.permissions?.workorder?.includes('execute'))
              }
            >
              {t`Start repair`}
            </Button>
          ]}
        />
        {machine?.pk && <MachinePlacementPath machineId={machine.pk} />}
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
