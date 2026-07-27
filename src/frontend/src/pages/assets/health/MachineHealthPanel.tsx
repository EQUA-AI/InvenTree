import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Center,
  Group,
  Loader,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconInfoCircle, IconRefresh } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  MachineAnomaly,
  MachineHealthSummary,
  MachineSignal
} from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../functions/notifications';
import { WorkOrderCreateModal } from '../../maintenance/components/WorkOrderCreateModal';
import { AnomalyList } from './AnomalyList';
import { HealthSourceStatusTable } from './HealthSourceStatus';
import { HealthSummaryPanel } from './HealthSummary';
import { SignalTable } from './SignalTable';

/**
 * Machine Health blade.
 *
 * Reads normalized state that connectors wrote; it cannot write telemetry back.
 * The two actions it does offer are deliberately different in weight:
 * acknowledging an anomaly records that a human looked, while Create repair
 * opens the governed work-package command with the anomaly cited as its origin.
 * Neither one starts work, and neither satisfies a safety gate.
 *
 * A machine with no mapped source renders an explicit empty state rather than an
 * error - it is a configuration gap, not a failure, and manual work-order
 * creation stays available throughout.
 */
export function MachineHealthPanel({
  machineId
}: Readonly<{ machineId: number }>) {
  const api = useApi();
  const queryClient = useQueryClient();

  const [acknowledging, setAcknowledging] = useState<number | null>(null);
  const [repairAnomaly, setRepairAnomaly] = useState<MachineAnomaly | null>(
    null
  );

  const summaryQuery = useQuery<MachineHealthSummary>({
    queryKey: ['machine-health-summary', machineId],
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_summary, machineId)
      );
      return response.data;
    }
  });

  const signalsQuery = useQuery<MachineSignal[]>({
    queryKey: ['machine-health-signals', machineId],
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_signals, machineId)
      );
      return response.data?.results ?? [];
    }
  });

  const anomaliesQuery = useQuery<MachineAnomaly[]>({
    queryKey: ['machine-health-anomalies', machineId],
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_anomalies, machineId)
      );
      return response.data?.results ?? [];
    }
  });

  const refreshAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['machine-health-summary'] });
    queryClient.invalidateQueries({ queryKey: ['machine-health-signals'] });
    queryClient.invalidateQueries({ queryKey: ['machine-health-anomalies'] });
  }, [queryClient]);

  const handleAcknowledge = useCallback(
    async (anomaly: MachineAnomaly) => {
      setAcknowledging(anomaly.pk);
      try {
        await api.post(
          apiUrl(
            ApiEndpoints.machine_health_anomaly_acknowledge,
            machineId
          ).replace(':anomalyId', String(anomaly.pk)),
          {}
        );
        refreshAll();
      } catch (error) {
        showApiErrorMessage({
          error,
          title: t`Could not acknowledge the anomaly`
        });
      } finally {
        setAcknowledging(null);
      }
    },
    [api, machineId, refreshAll]
  );

  const isLoading =
    summaryQuery.isLoading ||
    signalsQuery.isLoading ||
    anomaliesQuery.isLoading;

  if (isLoading) {
    return (
      <Center p='xl'>
        <Loader />
      </Center>
    );
  }

  if (summaryQuery.isError) {
    return (
      <Alert color='red' variant='light' title={t`Health data unavailable`}>
        {t`The health service could not be reached. Work-order creation is unaffected.`}
      </Alert>
    );
  }

  const summary = summaryQuery.data;
  const signals = signalsQuery.data ?? [];
  const anomalies = anomaliesQuery.data ?? [];

  if (!summary) {
    return null;
  }

  return (
    <Stack gap='lg'>
      <Group justify='space-between' align='center'>
        <Title order={4}>{t`Current condition`}</Title>
        <Button
          size='xs'
          variant='subtle'
          leftSection={<IconRefresh size={14} />}
          onClick={refreshAll}
        >
          {t`Refresh`}
        </Button>
      </Group>

      {!summary.configured ? (
        <Alert
          color='blue'
          variant='light'
          icon={<IconInfoCircle size={16} />}
          title={t`No health source is configured for this machine`}
        >
          <Stack gap='xs'>
            <Text size='sm'>
              {t`Map this machine's tags to a health source to see live signals and anomalies here. Until then, its condition is unknown rather than healthy.`}
            </Text>
            <Text size='sm'>
              {t`Creating work orders for this machine works normally in the meantime.`}
            </Text>
          </Stack>
        </Alert>
      ) : (
        <HealthSummaryPanel summary={summary} />
      )}

      <Stack gap='sm'>
        <Title order={4}>{t`Active anomalies`}</Title>
        <AnomalyList
          anomalies={anomalies}
          acknowledging={acknowledging}
          onAcknowledge={handleAcknowledge}
          onCreateRepair={setRepairAnomaly}
        />
      </Stack>

      <Stack gap='sm'>
        <Title order={4}>{t`Signals`}</Title>
        <SignalTable signals={signals} />
      </Stack>

      <Stack gap='sm'>
        <Title order={4}>{t`Source connections`}</Title>
        <HealthSourceStatusTable sources={summary.sources ?? []} />
      </Stack>

      <WorkOrderCreateModal
        opened={repairAnomaly !== null}
        onClose={() => setRepairAnomaly(null)}
        machineId={machineId}
        origin='anomaly'
        anomalyId={repairAnomaly?.pk}
        initialTitle={repairAnomaly?.title}
        initialFaultSummary={repairAnomaly?.evidence_summary}
        initialCriticality={
          repairAnomaly?.severity === 'critical' ? 'critical' : 'high'
        }
        onCreated={(result) => {
          notifications.show({
            title: t`Repair created`,
            message: t`${result.work_order_reference} was created from this anomaly and is planned, not started.`,
            color: 'green',
            autoClose: 8000
          });
          refreshAll();
        }}
      />
    </Stack>
  );
}

export default MachineHealthPanel;
