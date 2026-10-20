import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Box,
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
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import monitoring from '../monitoring.module.css';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  MachineAnomaly,
  MachineHealthSummary,
  MachineSignal,
  PreliminaryResults
} from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../functions/notifications';
import { WorkOrderCreateModal } from '../../maintenance/components/WorkOrderCreateModal';
import { displayClockLabel, displayInstant } from '../displayClock';
import { AlarmHistory } from './AlarmHistory';
import { AnomalyList } from './AnomalyList';
import { HealthSourceStatusTable } from './HealthSourceStatus';
import { HealthSummaryPanel } from './HealthSummary';
import { MonitoringContinuityNotice } from './MonitoringContinuity';
import { RetainedHealthEvidence } from './RetainedHealthEvidence';
import { SignalEvidence } from './SignalEvidence';
import { SignalTable } from './SignalTable';
import { SignalTrendChart } from './SignalTrendChart';
import { AlarmNoteModal } from './mimic/AlarmNoteModal';

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
  machineId,
  mimicHref = null
}: Readonly<{
  machineId: number;
  /**
   * Where this machine's mimic is: a station's own tab, or a pump's process
   * schematic inside Equipment view. Null when it has none.
   */
  mimicHref?: string | null;
}>) {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const linkedBinding = params.get('binding');
  const queryClient = useQueryClient();
  const [selectedBinding, setSelectedBinding] = useState<string | null>(
    linkedBinding
  );
  useEffect(() => {
    if (linkedBinding !== null) setSelectedBinding(linkedBinding);
  }, [linkedBinding]);
  const selectBinding = (value: string | null) => {
    setSelectedBinding(value);
    if (linkedBinding !== null)
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value) next.set('binding', value);
          else next.delete('binding');
          return next;
        },
        { replace: true }
      );
  };
  const [evidenceToken, setEvidenceToken] = useState(0);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceHeading = useRef<HTMLHeadingElement>(null);
  const evidenceTrigger = useRef<HTMLElement | null>(null);
  const inspectSignal = (binding: number, trigger: HTMLElement) => {
    selectBinding(String(binding));
    evidenceTrigger.current = trigger;
    setEvidenceOpen(true);
    setEvidenceToken((token) => token + 1);
  };
  const [dismissing, setDismissing] = useState<MachineAnomaly | null>(null);

  const [acknowledging, setAcknowledging] = useState<number | null>(null);
  const [repairAnomaly, setRepairAnomaly] = useState<MachineAnomaly | null>(
    null
  );
  const [analyzing, setAnalyzing] = useState<number | null>(null);
  // Preliminary results are generated on request, so they live in view state
  // rather than being cached and re-shown as if they were current.
  const [results, setResults] = useState<Record<number, PreliminaryResults>>(
    {}
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

  const handleAnalyze = useCallback(
    async (anomaly: MachineAnomaly) => {
      setAnalyzing(anomaly.pk);
      try {
        const response = await api.post(
          apiUrl(
            ApiEndpoints.machine_health_anomaly_analysis,
            machineId
          ).replace(':anomalyId', String(anomaly.pk)),
          {}
        );
        setResults((current) => ({
          ...current,
          [anomaly.pk]: response.data.preliminary_results
        }));
      } catch (error) {
        showApiErrorMessage({
          error,
          title: t`Could not analyze the anomaly`
        });
      } finally {
        setAnalyzing(null);
      }
    },
    [api, machineId]
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
  const anomalyAssociationKnown =
    !anomaliesQuery.isError &&
    Number.isInteger(summary?.active_anomaly_count) &&
    anomalies.length >=
      (summary?.active_anomaly_count ?? Number.POSITIVE_INFINITY);

  if (!summary) {
    return null;
  }

  const bindingId =
    linkedBinding ??
    selectedBinding ??
    (signals[0] ? String(signals[0].binding_id) : null);
  const signal = signals.find((row) => String(row.binding_id) === bindingId);

  return (
    <Stack
      component='section'
      aria-labelledby='pump-health-title'
      gap='md'
      className={monitoring.scope}
      data-testid='pump-health'
    >
      <Group className={monitoring.band} justify='space-between' align='center'>
        <Title id='pump-health-title' order={4}>{t`Current condition`}</Title>
        <Button
          size='xs'
          variant='default'
          leftSection={<IconRefresh size={14} />}
          onClick={refreshAll}
        >
          {t`Refresh`}
        </Button>
        {summary.active_anomaly_count > 0 && (
          <Anchor
            className={monitoring.link}
            href='#health-active-anomalies'
            size='sm'
          >{t`Review active anomalies (${summary.active_anomaly_count})`}</Anchor>
        )}
      </Group>

      <Text
        size='sm'
        className={monitoring.meta}
        component='output'
        aria-live='polite'
      >{t`Condition read ${displayInstant(summaryQuery.dataUpdatedAt) ?? t`Time unavailable`} · Times: your clock (${displayClockLabel()}) for unshifted times · shifted anomaly times are labelled separately · plant timezone not supplied`}</Text>
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
        <HealthSummaryPanel
          summary={summary}
          signals={signalsQuery.isError ? null : signals}
        />
      )}

      <Stack gap='sm'>
        <Title order={4}>{t`Signals`}</Title>
        {signalsQuery.isError ? (
          <Alert
            color='yellow'
            title={t`Signal readings unavailable`}
          >{t`The signal read failed; mapping and coverage are unavailable.`}</Alert>
        ) : (
          <SignalTable
            signals={signals}
            machineId={machineId}
            selectedBinding={bindingId}
            onInspect={inspectSignal}
            anomalies={anomalyAssociationKnown ? anomalies : null}
          />
        )}
      </Stack>

      {!signalsQuery.isError && (
        <Stack gap='sm'>
          <Title order={4}>{t`Trend`}</Title>
          <SignalTrendChart
            machineId={machineId}
            signals={signals}
            bindingId={bindingId}
            onBindingChange={selectBinding}
            onInspect={(trigger) =>
              bindingId && inspectSignal(Number(bindingId), trigger)
            }
            evidence={
              evidenceOpen && bindingId
                ? (prepared, available, provenance) => (
                    <SignalEvidence
                      machineId={machineId}
                      bindingId={Number(bindingId)}
                      signal={signal}
                      source={summary.sources?.find(
                        (source) => source.source_id === signal?.source_id
                      )}
                      anomalies={anomalyAssociationKnown ? anomalies : null}
                      prepared={prepared}
                      historyContext={provenance}
                      trendAvailable={available}
                      headingRef={evidenceHeading}
                      focusToken={evidenceToken}
                      onClose={() => {
                        setEvidenceOpen(false);
                        requestAnimationFrame(() =>
                          (
                            document.querySelector<HTMLButtonElement>(
                              '[role="dialog"] #inspect-selected-health-signal'
                            ) ??
                            (evidenceTrigger.current?.isConnected
                              ? evidenceTrigger.current
                              : document.getElementById(
                                  'inspect-selected-health-signal'
                                ))
                          )?.focus()
                        );
                      }}
                    />
                  )
                : undefined
            }
          />
        </Stack>
      )}

      <Stack gap='sm'>
        <Group justify='space-between' align='center'>
          <Title
            id='health-active-anomalies'
            tabIndex={-1}
            order={4}
          >{t`Active anomalies`}</Title>
          {/* Where the reading behind an alarm is drawn, on the part it
              belongs to. */}
          {mimicHref && (
            <Anchor
              className={monitoring.link}
              component={Link}
              to={mimicHref}
              size='sm'
            >
              {t`See the readings on the mimic`}
            </Anchor>
          )}
        </Group>
        {!anomaliesQuery.isError && (
          <Text
            size='sm'
            className={monitoring.meta}
          >{t`Showing ${anomalies.length} active anomaly records on this machine (maximum 200); summary counts cover the machine's active set.`}</Text>
        )}
        {!anomalyAssociationKnown && !anomaliesQuery.isError && (
          <Alert color='yellow'>{t`The active anomaly list is incomplete. Row association cannot establish an absence of anomalies.`}</Alert>
        )}
        {anomaliesQuery.isError ? (
          <Alert
            color='yellow'
            title={t`Active anomalies unavailable`}
          >{t`The anomaly read failed. An empty list cannot be inferred.`}</Alert>
        ) : anomalies.length > 0 || anomalyAssociationKnown ? (
          <AnomalyList
            anomalies={anomalies}
            acknowledging={acknowledging}
            analyzing={analyzing}
            results={results}
            onAcknowledge={handleAcknowledge}
            onCreateRepair={setRepairAnomaly}
            onAnalyze={handleAnalyze}
            onDismiss={setDismissing}
          />
        ) : null}
      </Stack>

      <Stack gap='sm'>
        <Title order={4}>{t`Past alarms`}</Title>
        <AlarmHistory machineId={machineId} />
      </Stack>

      <RetainedHealthEvidence machineId={machineId} />
      <Box maw='70ch'>
        <MonitoringContinuityNotice />
      </Box>
      <Stack gap='sm'>
        <Title order={4}>{t`Source connections`}</Title>
        <HealthSourceStatusTable sources={summary.sources ?? []} />
      </Stack>

      <AlarmNoteModal
        action='dismiss'
        target={
          dismissing
            ? {
                machine: machineId,
                anomaly: dismissing.pk,
                summary: dismissing.evidence_summary || dismissing.title
              }
            : null
        }
        onClose={() => setDismissing(null)}
        onDone={() => {
          setDismissing(null);
          refreshAll();
        }}
      />

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
