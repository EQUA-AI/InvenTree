import { t } from '@lingui/core/macro';
import { Button, Code, Group, Stack, Table, Text } from '@mantine/core';
import {
  IconBan,
  IconBox,
  IconChecklist,
  IconFlagCheck,
  IconHistory,
  IconInfoCircle,
  IconShieldLock,
  IconStethoscope
} from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import type { PanelType } from '@lib/types/Panel';
import type { RepairPacket } from '@lib/types/Repair';
import {
  type DetailsField,
  DetailsTable
} from '../../components/details/Details';
import { ItemDetailsGrid } from '../../components/details/ItemDetails';
import InstanceDetail from '../../components/nav/InstanceDetail';
import { PageDetail } from '../../components/nav/PageDetail';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { useApi } from '../../contexts/ApiContext';
import { useInstance } from '../../hooks/UseInstance';
import { PacketCloseoutModal } from './components/PacketCloseoutModal';
import { SafetyPanel } from './components/safety/SafetyPanel';

/** Renders a structured JSON blob (diagnosis / closeout). */
function JsonPanel({ data }: Readonly<{ data: unknown }>) {
  const empty = !data || Object.keys(data as object).length === 0;
  if (empty) {
    return <Text c='dimmed'>{t`No data yet.`}</Text>;
  }
  return <Code block>{JSON.stringify(data, null, 2)}</Code>;
}

export default function RepairPacketDetail() {
  const { id } = useParams();
  const api = useApi();

  const { instance: packet, instanceQuery } = useInstance({
    endpoint: ApiEndpoints.repair_packet_list,
    pk: id,
    params: {}
  });

  // Poll while asynchronous generation is in flight.
  useEffect(() => {
    const gs = (packet as RepairPacket | undefined)?.generation_status;
    if (gs !== 'pending' && gs !== 'running') {
      return;
    }
    const timer = setInterval(() => instanceQuery.refetch?.(), 2000);
    return () => clearInterval(timer);
  }, [packet, instanceQuery]);

  const detailsLeft: DetailsField[] = useMemo(
    () => [
      { name: 'reference', type: 'text', label: t`Reference` },
      { name: 'status_label', type: 'text', label: t`Status` },
      { name: 'machine_name', type: 'text', label: t`Asset` },
      { name: 'criticality', type: 'text', label: t`Criticality` },
      { name: 'generation_status', type: 'text', label: t`Generation` }
    ],
    []
  );

  const detailsRight: DetailsField[] = useMemo(
    () => [
      { name: 'symptom', type: 'text', label: t`Symptom` },
      { name: 'fault_summary', type: 'text', label: t`Fault Summary` },
      { name: 'production_impact', type: 'text', label: t`Production Impact` }
    ],
    []
  );

  const [closeoutOpen, setCloseoutOpen] = useState(false);

  // Closing a packet that owns a work order is a structured closeout, not a bare
  // status change: the server refuses the plain advance path for those packets.
  const ownsWorkOrder = Boolean(
    (packet as RepairPacket | undefined)?.work_order
  );

  // Lifecycle: map current status -> next transition offered in the UI.
  const nextTransition = useMemo(() => {
    switch (packet?.status) {
      case 'diagnosed':
        return { to: 'approved', label: t`Request Approval` };
      case 'approved':
        return { to: 'executing', label: t`Start Work` };
      case 'executing':
        return ownsWorkOrder ? null : { to: 'closed', label: t`Close Packet` };
      default:
        return null;
    }
  }, [packet?.status, ownsWorkOrder]);

  const runAction = (endpoint: ApiEndpoints, body: object = {}) => {
    if (!packet?.pk) {
      return;
    }
    api
      .post(apiUrl(endpoint, packet.pk), body)
      .catch(() => {})
      .finally(() => instanceQuery.refetch?.());
  };

  const actions = useMemo(() => {
    const items = [];
    if (packet?.status === 'draft') {
      items.push(
        <Button
          key='generate'
          onClick={() => runAction(ApiEndpoints.repair_packet_generate)}
        >
          {t`Generate`}
        </Button>
      );
    }
    if (nextTransition) {
      items.push(
        <Button
          key='advance'
          variant='light'
          onClick={() =>
            runAction(ApiEndpoints.repair_packet_advance, {
              to: nextTransition.to
            })
          }
        >
          {nextTransition.label}
        </Button>
      );
    }
    if (packet?.status === 'executing' && ownsWorkOrder) {
      items.push(
        <Button
          key='closeout'
          variant='light'
          leftSection={<IconFlagCheck size={16} />}
          onClick={() => setCloseoutOpen(true)}
        >
          {t`Close Repair`}
        </Button>
      );
    }
    if (
      packet?.pk &&
      packet.status !== 'closed' &&
      packet.status !== 'canceled'
    ) {
      items.push(
        <Button
          key='cancel'
          color='red'
          variant='subtle'
          leftSection={<IconBan size={16} />}
          onClick={() => runAction(ApiEndpoints.repair_packet_cancel)}
        >
          {t`Cancel`}
        </Button>
      );
    }
    return items;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [packet?.status, nextTransition, ownsWorkOrder]);

  const packetData = packet as RepairPacket | undefined;

  const panels: PanelType[] = useMemo(() => {
    return [
      {
        name: 'fault',
        label: t`Fault`,
        icon: <IconInfoCircle />,
        content: packet?.pk ? (
          <ItemDetailsGrid>
            <DetailsTable fields={detailsLeft} item={packet} />
            <DetailsTable fields={detailsRight} item={packet} />
          </ItemDetailsGrid>
        ) : null
      },
      {
        name: 'diagnosis',
        label: t`Diagnosis`,
        icon: <IconStethoscope />,
        content: <JsonPanel data={packetData?.diagnosis} />
      },
      {
        name: 'safety',
        label: t`Safety`,
        icon: <IconShieldLock />,
        content: (
          <SafetyPanel
            packet={packetData}
            onRefresh={() => instanceQuery.refetch?.()}
          />
        )
      },
      {
        name: 'parts',
        label: t`Parts`,
        icon: <IconBox />,
        content: (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`Part`}</Table.Th>
                <Table.Th>{t`Required`}</Table.Th>
                <Table.Th>{t`Allocated`}</Table.Th>
                <Table.Th>{t`Status`}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(packetData?.parts ?? []).map((p) => (
                <Table.Tr key={p.id}>
                  <Table.Td>{p.part_name}</Table.Td>
                  <Table.Td>{p.quantity}</Table.Td>
                  <Table.Td>{p.allocated_quantity}</Table.Td>
                  <Table.Td>{p.allocation_status}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )
      },
      {
        name: 'approvals',
        label: t`Approvals`,
        icon: <IconChecklist />,
        content: (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`Purpose`}</Table.Th>
                <Table.Th>{t`Status`}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(packetData?.approvals ?? []).map((a) => (
                <Table.Tr key={a.pk}>
                  <Table.Td>{a.purpose}</Table.Td>
                  <Table.Td>{a.status}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )
      },
      {
        name: 'closeout',
        label: t`Closeout`,
        icon: <IconFlagCheck />,
        content: <JsonPanel data={packetData?.closeout} />
      },
      {
        name: 'events',
        label: t`History`,
        icon: <IconHistory />,
        content: (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`When`}</Table.Th>
                <Table.Th>{t`Event`}</Table.Th>
                <Table.Th>{t`From`}</Table.Th>
                <Table.Th>{t`To`}</Table.Th>
                <Table.Th>{t`Reason`}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(packetData?.events ?? []).map((e) => (
                <Table.Tr key={e.pk}>
                  <Table.Td>{new Date(e.created_at).toLocaleString()}</Table.Td>
                  <Table.Td>{e.event_type}</Table.Td>
                  <Table.Td>{e.from_status}</Table.Td>
                  <Table.Td>{e.to_status}</Table.Td>
                  <Table.Td>{e.reason}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )
      }
    ];
  }, [packet, packetData, detailsLeft, detailsRight]);

  return (
    <InstanceDetail query={instanceQuery}>
      <Stack>
        <PageDetail
          title={packet?.reference ?? t`Repair Packet`}
          breadcrumbs={[{ name: t`Repair Packets`, url: '/repair/packets/' }]}
          actions={[<Group key='actions'>{actions}</Group>]}
        />
        <PanelGroup
          pageKey='repair-packet-detail'
          panels={panels}
          instance={packet}
          model={ModelType.repairpacket}
          id={packet?.pk ?? null}
        />
        {packet?.pk && (
          <PacketCloseoutModal
            opened={closeoutOpen}
            onClose={() => setCloseoutOpen(false)}
            packetId={packet.pk}
            workOrderVersion={packetData?.work_order_lifecycle_version ?? null}
            workOrderReference={packetData?.work_order_reference ?? null}
            onClosed={() => instanceQuery.refetch?.()}
          />
        )}
      </Stack>
    </InstanceDetail>
  );
}
