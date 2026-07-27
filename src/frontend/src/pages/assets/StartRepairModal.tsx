import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Group,
  List,
  Loader,
  Modal,
  Stack,
  Text
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconExternalLink } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import { useApi } from '../../contexts/ApiContext';
import { showApiErrorMessage } from '../../functions/notifications';

export interface StartBlocker {
  code: string;
  message: string;
  source: string;
}

export interface OpenRepair {
  packet_id: number;
  packet_reference: string;
  packet_status: string;
  work_order_id: number | null;
  work_order_reference: string | null;
  work_order_title?: string;
  fault_summary?: string;
  criticality?: string;
  lifecycle_status: string | null;
  lifecycle_version: number | null;
  ready: boolean;
  blockers: StartBlocker[];
}

/**
 * Start repair from the machine page.
 *
 * Three rules the plan is explicit about, all visible here:
 *
 * - starting is a readiness-gated transition, so the modal asks the server which
 *   repairs are startable rather than deciding locally;
 * - a blocked repair offers "Review blockers", not a disabled button, so the
 *   technician sees the unresolved LOTO point or missing part;
 * - when more than one repair is open the operator chooses. The UI never guesses
 *   which one a click meant.
 */
export function StartRepairModal({
  opened,
  onClose,
  machineId,
  onStarted
}: Readonly<{
  opened: boolean;
  onClose: () => void;
  machineId: number;
  onStarted?: (repair: OpenRepair) => void;
}>) {
  const api = useApi();
  const isSmallScreen = useMediaQuery('(max-width: 48em)');
  const [starting, setStarting] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const repairsQuery = useQuery<OpenRepair[]>({
    queryKey: ['machine-open-repairs', machineId],
    enabled: opened,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_open_repairs, machineId)
      );
      return response.data?.results ?? [];
    }
  });

  const handleStart = async (repair: OpenRepair) => {
    setStarting(repair.packet_id);
    try {
      await api.post(
        apiUrl(ApiEndpoints.repair_packet_start, repair.packet_id),
        { expected_version: repair.lifecycle_version }
      );
      onStarted?.(repair);
      onClose();
    } catch (error: any) {
      // A 409 means readiness changed since the list was fetched. Refetch so the
      // operator sees the current blockers rather than a stale "ready".
      if (error?.response?.status === 409) {
        repairsQuery.refetch();
      }
      showApiErrorMessage({ error, title: t`Could not start the repair` });
    } finally {
      setStarting(null);
    }
  };

  const repairs = repairsQuery.data ?? [];

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t`Start repair`}
      size='lg'
      fullScreen={isSmallScreen}
    >
      {repairsQuery.isLoading ? (
        <Center p='xl'>
          <Loader />
        </Center>
      ) : repairs.length === 0 ? (
        <Alert color='blue' variant='light'>
          {t`This machine has no open repair to start. Create one first.`}
        </Alert>
      ) : (
        <Stack gap='sm'>
          {repairs.length > 1 && (
            <Text size='sm' c='dimmed'>
              {t`More than one repair is open on this machine. Choose the one to start.`}
            </Text>
          )}

          {repairs.map((repair) => (
            <Card key={repair.packet_id} withBorder radius='md' p='md'>
              <Stack gap='sm'>
                <Group justify='space-between' align='flex-start' wrap='nowrap'>
                  <Stack gap={2}>
                    <Group gap='xs'>
                      <Text fw={600}>
                        {repair.work_order_reference || repair.packet_reference}
                      </Text>
                      <Badge variant='outline' color='gray'>
                        {repair.packet_status}
                      </Badge>
                      {repair.ready ? (
                        <Badge color='green' variant='light'>
                          {t`Ready to start`}
                        </Badge>
                      ) : (
                        <Badge
                          color='orange'
                          variant='light'
                          leftSection={<IconAlertTriangle size={14} />}
                        >
                          {t`${repair.blockers.length} blocker(s)`}
                        </Badge>
                      )}
                    </Group>
                    <Text size='sm'>{repair.work_order_title}</Text>
                    {repair.fault_summary && (
                      <Text size='xs' c='dimmed'>
                        {repair.fault_summary}
                      </Text>
                    )}
                  </Stack>

                  <Group gap='xs' wrap='nowrap'>
                    {repair.work_order_id && (
                      <Button
                        size='xs'
                        variant='subtle'
                        component={Link}
                        to={`/maintenance/work-orders/${repair.work_order_id}/`}
                        leftSection={<IconExternalLink size={14} />}
                      >
                        {t`Open`}
                      </Button>
                    )}
                    {repair.ready ? (
                      <Button
                        size='xs'
                        loading={starting === repair.packet_id}
                        onClick={() => handleStart(repair)}
                      >
                        {t`Start repair`}
                      </Button>
                    ) : (
                      <Button
                        size='xs'
                        variant='light'
                        color='orange'
                        onClick={() =>
                          setExpanded(
                            expanded === repair.packet_id
                              ? null
                              : repair.packet_id
                          )
                        }
                      >
                        {t`Review blockers`}
                      </Button>
                    )}
                  </Group>
                </Group>

                {expanded === repair.packet_id && (
                  <Alert color='orange' variant='light'>
                    <List size='sm' spacing={4}>
                      {repair.blockers.map((blocker) => (
                        <List.Item key={`${blocker.code}-${blocker.message}`}>
                          <Text size='sm'>{blocker.message}</Text>
                          <Text size='xs' c='dimmed'>
                            {blocker.source} · {blocker.code}
                          </Text>
                        </List.Item>
                      ))}
                    </List>
                  </Alert>
                )}
              </Stack>
            </Card>
          ))}
        </Stack>
      )}
    </Modal>
  );
}

export default StartRepairModal;
