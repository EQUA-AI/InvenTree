import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../App';
import { showApiErrorMessage } from '../../functions/notifications';

type Source = { pk: number; name: string };
export type StationLiveStatus = {
  activated: boolean;
  enabled: boolean;
  source: Source | null;
  sources: Source[];
  approved: number;
  bound: number;
  unbound: number;
  last_poll_at: string | null;
  last_error_code: string;
  preview?: { source: number; source_hash: string; approved: number };
};

const endpoint = (stationId: string) =>
  `${apiUrl(ApiEndpoints.equipment_registry)}${stationId}/activate/`;

export function useStationLiveStatus(stationId: string) {
  return useQuery({
    queryKey: ['registry', 'live', stationId],
    enabled: !!stationId,
    refetchInterval: 30_000,
    queryFn: async () =>
      (await api.get<StationLiveStatus>(endpoint(stationId))).data
  });
}

export function StationLiveSource({
  stationId,
  canAdd,
  canChange,
  busy
}: {
  stationId: string;
  canAdd: boolean;
  canChange: boolean;
  busy: boolean;
}) {
  const client = useQueryClient();
  const live = useStationLiveStatus(stationId);
  const [selected, setSelected] = useState<string | null>(null);
  const status = live.data;
  const sourceId = status?.source ? String(status.source.pk) : selected;
  const preview = useQuery({
    queryKey: ['registry', 'live-preview', stationId, sourceId],
    enabled: !!sourceId,
    queryFn: async () =>
      (
        await api.get<StationLiveStatus>(endpoint(stationId), {
          params: { source: sourceId }
        })
      ).data.preview
  });
  const change = useMutation({
    mutationFn: async (deactivate: boolean) => {
      const plan = preview.data;
      if (!plan || String(plan.source) !== sourceId) {
        throw new Error(t`Refresh the source preview before continuing.`);
      }
      const data = { source: plan.source, source_hash: plan.source_hash };
      return deactivate
        ? api.delete(endpoint(stationId), { data })
        : api.post(endpoint(stationId), data);
    },
    onSuccess: (_, deactivate) => {
      notifications.show({
        color: 'green',
        message: deactivate
          ? t`Station deactivated`
          : t`Station mappings activated`
      });
    },
    onError: (error) =>
      showApiErrorMessage({ error, title: t`Live source update failed` }),
    onSettled: () => client.invalidateQueries({ queryKey: ['registry'] })
  });
  const disabled =
    busy || change.isPending || live.isFetching || preview.isFetching;
  const sources = status?.sources ?? [];
  const options =
    status?.source && !sources.some((s) => s.pk === status.source?.pk)
      ? [status.source, ...sources]
      : sources;

  return (
    <Paper withBorder p='md'>
      <Stack gap='sm'>
        <Group justify='space-between'>
          <Title order={4}>{t`Live source`}</Title>
          <Button
            variant='subtle'
            loading={live.isFetching || preview.isFetching}
            disabled={change.isPending}
            onClick={() => client.invalidateQueries({ queryKey: ['registry'] })}
          >{t`Refresh`}</Button>
        </Group>
        {live.isLoading && <Loader size='sm' />}
        {live.isError ? (
          <Alert color='red'>{t`Live source status could not be loaded. Refresh to retry.`}</Alert>
        ) : (
          status && (
            <>
              <Group>
                <Badge color={status.activated ? 'green' : 'gray'}>
                  {status.activated ? t`Activated` : t`Not activated`}
                </Badge>
                <Badge color={status.enabled ? 'blue' : 'yellow'}>
                  {status.enabled ? t`Polling enabled` : t`Polling paused`}
                </Badge>
              </Group>
              <Select
                label={t`Source`}
                value={sourceId}
                onChange={setSelected}
                data={options.map((s) => ({
                  value: String(s.pk),
                  label: s.name
                }))}
                disabled={disabled || !!status.source || !canChange}
                placeholder={t`Select a configured source`}
              />
              {!options.length && (
                <Text size='sm'>{t`No source is configured for this station. Ask your administrator to assign one.`}</Text>
              )}
              <Group gap='lg'>
                <Text size='sm'>
                  {t`Approved`}: {status.approved}
                </Text>
                <Text size='sm'>
                  {t`Bound`}: {status.bound}
                </Text>
                <Text size='sm'>
                  {t`Unbound`}: {status.unbound}
                </Text>
              </Group>
              <Text size='sm'>
                {t`Last poll`}:{' '}
                {status.last_poll_at
                  ? new Date(status.last_poll_at).toLocaleString()
                  : t`Not polled yet`}
              </Text>
              {status.last_error_code && (
                <Alert color='red'>
                  {t`Last poll error`}: {status.last_error_code}
                </Alert>
              )}
              {preview.isError && (
                <Alert color='red'>{t`Source preview could not be loaded. Refresh before trying again.`}</Alert>
              )}
              <Text size='sm' c='dimmed'>
                {t`Activation binds approved mappings. Polling must also be enabled by your administrator. Deactivation stops this station and clears its managed live values.`}
              </Text>
              <Group>
                <Button
                  disabled={
                    disabled ||
                    !canAdd ||
                    !canChange ||
                    !preview.data?.approved ||
                    preview.isError
                  }
                  loading={change.isPending && !change.variables}
                  onClick={() => change.mutate(false)}
                >
                  {status.activated
                    ? t`Refresh approved bindings`
                    : t`Activate station`}
                </Button>
                <Button
                  variant='default'
                  disabled={
                    disabled ||
                    !canChange ||
                    !status.source ||
                    !preview.data ||
                    preview.isError
                  }
                  loading={change.isPending && change.variables}
                  onClick={() => change.mutate(true)}
                >{t`Deactivate station`}</Button>
              </Group>
            </>
          )
        )}
      </Stack>
    </Paper>
  );
}
