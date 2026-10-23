import type { MachineHealthSummary } from '@lib/types/MachineHealth';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { parseMaintenanceResult } from '../../../components/dashboard/widgets/maintenanceMetricsRead';
import { useApi } from '../../../contexts/ApiContext';
import {
  InvalidReadResponse,
  readFailure,
  readPollInterval,
  readQueryPolicy
} from '../../../functions/readQueryPolicy';
import { useAIChatState } from '../../../states/AIChatState';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import type { LocatedMachine } from './locationTypes';
import {
  type MachineWorkMetrics,
  aggregateWork,
  mapMachines,
  parseHealthSummary,
  readLocationMachines
} from './machineMetrics';

export function useMetricsIdentity() {
  const host = useLocalState((s) => s.getHost());
  const generation = useUserState((s) => s.authGeneration);
  const user = useUserState((s) => s.user?.pk);
  const authorization = useAIChatState((s) => s.sessionGeneration);
  return [host, user, generation, authorization] as const;
}

/** A denial blocks reads until explicit recovery or a new authorization identity. */
export function useMetricsAccess() {
  const client = useQueryClient();
  const identity = useMetricsIdentity();
  const key = ['machine-metrics-access', ...identity];
  const access = useQuery<unknown>({
    queryKey: key,
    queryFn: () => null,
    enabled: false,
    initialData: null
  });
  return {
    denied: access.data != null,
    error: access.data,
    retry: async () => {
      await client.resetQueries({ queryKey: ['machine-metrics', ...identity] });
      client.setQueryData(key, null);
    }
  };
}

/** Cancel outstanding reads before deleting payloads; disabled observers cannot refetch in a loop. */
export function useClearDeniedMetrics(error?: unknown) {
  const client = useQueryClient();
  const identity = useMetricsIdentity();
  const identityKey = JSON.stringify(identity);
  useEffect(() => {
    if (['authentication', 'forbidden'].includes(readFailure(error) ?? '')) {
      client.setQueryData(['machine-metrics-access', ...identity], error);
      void client
        .cancelQueries({ queryKey: ['machine-metrics', ...identity] })
        .then(() => {
          client.removeQueries({
            queryKey: ['machine-metrics', ...identity],
            predicate: (q) => q.state.data !== undefined
          });
        });
    }
  }, [client, error, identityKey]);
}

export function useLocationMachines(
  location?: number,
  descendants = true,
  enabled = true
) {
  const api = useApi();
  const identity = useMetricsIdentity();
  const access = useMetricsAccess();
  const query = useQuery({
    ...readQueryPolicy,
    queryKey: [
      'machine-metrics',
      ...identity,
      'population',
      location,
      descendants
    ],
    queryFn: ({ signal }) =>
      readLocationMachines(api, location, descendants, signal),
    staleTime: 30_000,
    refetchInterval: (q) => readPollInterval(q, 60_000),
    enabled: enabled && !access.denied
  });
  useClearDeniedMetrics(query.error);
  return { ...query, access };
}

export function useMachineMetrics(location?: number, descendants = true) {
  const api = useApi();
  const client = useQueryClient();
  const identity = useMetricsIdentity();
  const population = useLocationMachines(location, descendants);
  const access = useMetricsAccess();
  const machines = population.isError ? undefined : population.data;
  const ids = machines?.map((m) => m.pk);
  const health = useQuery<
    { machine: LocatedMachine; summary: MachineHealthSummary }[]
  >({
    ...readQueryPolicy,
    queryKey: [
      'machine-metrics',
      ...identity,
      'health',
      location,
      descendants,
      ids
    ],
    enabled: !!machines && !access.denied,
    staleTime: 30_000,
    refetchInterval: (q) => readPollInterval(q, 60_000),
    queryFn: ({ signal }) =>
      mapMachines(machines!, signal, async (machine) => ({
        machine,
        summary: await client.fetchQuery({
          queryKey: [
            'machine-metrics',
            ...identity,
            'machine-health',
            machine.pk
          ],
          staleTime: 30_000,
          retry: false,
          queryFn: async ({ signal: childSignal }) =>
            parseHealthSummary(
              (
                await api.get(
                  `/api/machine-health/machines/${machine.pk}/health/`,
                  { signal: AbortSignal.any([signal, childSignal]) }
                )
              ).data
            )
        })
      }))
  });
  const work = useQuery<{
    rows: MachineWorkMetrics[];
    totals: ReturnType<typeof aggregateWork>;
  }>({
    ...readQueryPolicy,
    queryKey: [
      'machine-metrics',
      ...identity,
      'work',
      location,
      descendants,
      ids,
      'current'
    ],
    enabled: !!machines && !access.denied,
    staleTime: 30_000,
    refetchInterval: (q) =>
      q.state.data?.totals === null ? false : readPollInterval(q, 60_000),
    queryFn: async ({ signal }) => {
      const rows = await mapMachines(machines!, signal, async (machine) => {
        const results = [];
        for (const metric of ['open', 'overdue']) {
          signal.throwIfAborted();
          results.push(
            await client.fetchQuery({
              queryKey: [
                'machine-metrics',
                ...identity,
                'maintenance',
                machine.pk,
                metric,
                0,
                'current'
              ],
              staleTime: 30_000,
              retry: false,
              queryFn: async ({ signal: childSignal }) => {
                const { data } = await api.get(
                  '/api/aichat/ui/maintenance-metrics/',
                  {
                    signal: AbortSignal.any([signal, childSignal]),
                    params: { metric, machine: machine.pk, offset: 0 }
                  }
                );
                try {
                  return parseMaintenanceResult(data, metric);
                } catch {
                  throw new InvalidReadResponse('Invalid maintenance result');
                }
              }
            })
          );
        }
        return {
          machine: machine.pk,
          open: results[0],
          overdue: results[1]
        } satisfies MachineWorkMetrics;
      });
      return { rows, totals: aggregateWork(rows) };
    }
  });
  useClearDeniedMetrics(health.error ?? work.error);
  return { population, health, work, identity };
}
