import { t } from '@lingui/core/macro';
import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconRefresh, IconShieldCog } from '@tabler/icons-react';
import { useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { RepairPacket } from '@lib/types/Repair';

import { useApi } from '../../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../../functions/notifications';
import { SafetyBlockerAlert } from './SafetyBlockerAlert';
import { SafetyGateCard } from './SafetyGateCard';

/**
 * Field-usable Safety panel for the Repair Packet detail page. Renders the
 * blocker summary, an ordered gate checklist, and per-gate actions. The backend
 * remains the source of truth for enforcement; this panel refreshes the packet
 * after every action.
 */
export function SafetyPanel({
  packet,
  onRefresh
}: Readonly<{
  packet: RepairPacket | undefined;
  onRefresh: () => void;
}>) {
  const api = useApi();
  const [resolving, setResolving] = useState(false);

  const gates = useMemo(
    () =>
      [...(packet?.gates ?? [])].sort(
        (a, b) => a.sequence - b.sequence || a.pk - b.pk
      ),
    [packet?.gates]
  );

  const blockers = packet?.unsatisfied_safety_gates ?? [];
  const confirmedCount = gates.filter((g) => g.status === 'confirmed').length;
  const pendingCount = gates.filter((g) => g.status === 'pending').length;
  const waivedCount = gates.filter((g) => g.status === 'waived').length;

  const resolveGates = async () => {
    if (!packet?.pk) {
      return;
    }
    setResolving(true);
    try {
      const response = await api.post(
        apiUrl(ApiEndpoints.repair_packet_resolve_gates, packet.pk)
      );
      const created = response.data?.created ?? 0;
      notifications.show({
        color: 'green',
        message:
          created > 0
            ? t`Added ${created} safety gate(s)`
            : t`Safety gates are up to date`
      });
      onRefresh();
    } catch (error) {
      showApiErrorMessage({ error, title: t`Failed to resolve safety gates` });
    } finally {
      setResolving(false);
    }
  };

  if (!packet?.pk) {
    return null;
  }

  return (
    <Stack gap='sm'>
      <SafetyBlockerAlert blockers={blockers} />

      <Group justify='space-between'>
        <Group gap='xs'>
          <Badge variant='light'>
            {t`Total`}: {gates.length}
          </Badge>
          <Badge color='red' variant='light'>
            {t`Blocked`}: {blockers.length}
          </Badge>
          <Badge color='green' variant='light'>
            {t`Confirmed`}: {confirmedCount}
          </Badge>
          <Badge color='yellow' variant='light'>
            {t`Pending`}: {pendingCount}
          </Badge>
          <Badge color='orange' variant='light'>
            {t`Waived`}: {waivedCount}
          </Badge>
        </Group>
        <Group gap='xs'>
          <Button
            variant='light'
            leftSection={<IconShieldCog size={16} />}
            loading={resolving}
            onClick={resolveGates}
          >
            {t`Resolve Gates`}
          </Button>
          <Button
            variant='default'
            leftSection={<IconRefresh size={16} />}
            onClick={onRefresh}
          >
            {t`Refresh`}
          </Button>
        </Group>
      </Group>

      {gates.length === 0 ? (
        <Text c='dimmed'>
          {t`No safety gates yet. Use "Resolve Gates" to apply the applicable safety templates for this packet.`}
        </Text>
      ) : (
        gates.map((gate) => (
          <SafetyGateCard
            key={gate.pk}
            packet={packet}
            gate={gate}
            onRefresh={onRefresh}
          />
        ))
      )}
    </Stack>
  );
}
