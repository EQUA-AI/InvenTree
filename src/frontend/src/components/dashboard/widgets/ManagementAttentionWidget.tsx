import type { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useDocumentVisibility } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useAIChatState } from '../../../states/AIChatState';
import { useUserState } from '../../../states/UserState';
import { type ManagementMetric, managementMetrics } from './managementMetrics';

type MetricResult = {
  id: string;
  version: number;
  state: string;
  count?: number;
  missing_dates?: number;
  complete?: boolean;
  next_offset?: number | null;
  records?: { model: ModelType; pk: number; label: string; due_date: string }[];
};
type MetricsResponse = {
  metrics: MetricResult[];
  observed_at: string;
  timezone: string;
  reporting_date: string;
};

export default function ManagementAttentionWidget() {
  const api = useApi();
  const user = useUserState();
  const generation = useAIChatState((s) => s.sessionGeneration);
  const visibility = useDocumentVisibility();
  const [selected, setSelected] = useState<ManagementMetric | null>(null);
  const [offset, setOffset] = useState(0);
  const definitions = managementMetrics().filter((metric) =>
    user.hasViewPermission(metric.model)
  );
  const query = useQuery({
    queryKey: [
      'management-metrics',
      user.user?.pk,
      generation,
      selected?.id,
      offset
    ],
    enabled: definitions.length > 0 && visibility === 'visible',
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    retry: false,
    queryFn: async ({ signal }): Promise<MetricsResponse> => {
      const { data } = await api.get('/api/aichat/ui/metrics/', {
        params: { metric: selected?.id, offset },
        signal
      });
      if (
        !Array.isArray(data?.metrics) ||
        !data.metrics.every(
          (m: MetricResult) =>
            m.state !== 'ready' ||
            (m.version === 1 &&
              m.complete === true &&
              Number.isSafeInteger(m.count) &&
              m.count! >= 0)
        )
      )
        throw new Error('Incomplete metrics');
      return data;
    }
  });
  const selectedResult = query.data?.metrics.find((m) => m.id === selected?.id);
  return (
    <Stack
      gap='sm'
      style={{ height: '100%', overflowY: 'auto' }}
      data-testid='management-attention'
    >
      <Title order={3} size='h4'>{t`Needs attention`}</Title>
      <Text
        size='xs'
        c='dimmed'
      >{t`Your authorized records · Live counts`}</Text>
      {query.isError && (
        <Alert color='yellow'>
          {t`Metrics unavailable. Retry to refresh the source.`}
          <Button
            variant='subtle'
            onClick={() => void query.refetch()}
          >{t`Retry`}</Button>
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='xs'>
        {definitions.map((definition) => {
          const result = query.data?.metrics.find(
            (metric) => metric.id === definition.id
          );
          const valid = result?.state === 'ready' && !query.isError;
          return (
            <Paper
              key={definition.id}
              p='sm'
              withBorder
              data-testid={`metric-${definition.id}`}
            >
              <Stack gap={6}>
                <Text fw={600} size='sm'>
                  {definition.title}
                </Text>
                {definition.readiness === 'supported' ? (
                  query.isPending ? (
                    <Loader size='xs' />
                  ) : valid ? (
                    <>
                      <Text fw={700} size='xl'>
                        {result.count}{' '}
                        <Text span size='sm' fw={400}>
                          {definition.unit}
                        </Text>
                      </Text>
                      {!!result.missing_dates && (
                        <Text size='xs' c='orange'>
                          {t`Missing dates`}: {result.missing_dates}
                        </Text>
                      )}
                      <Button
                        variant='light'
                        size='compact-sm'
                        onClick={() => {
                          setOffset(0);
                          setSelected(definition);
                        }}
                      >{t`View contributing records`}</Button>
                    </>
                  ) : (
                    <Text size='sm' c='dimmed'>{t`Unavailable`}</Text>
                  )
                ) : (
                  <Badge
                    variant='light'
                    color='gray'
                  >{t`Not configured`}</Badge>
                )}
                <Text size='xs' c='dimmed'>
                  {definition.description}
                </Text>
              </Stack>
            </Paper>
          );
        })}
      </SimpleGrid>
      {query.data && (
        <Text size='xs' c='dimmed'>
          {t`As of`}: {new Date(query.data.observed_at).toLocaleString()} ·{' '}
          {query.data.timezone} · {t`Definition version 1`}
        </Text>
      )}
      <Modal
        opened={!!selected}
        onClose={() => setSelected(null)}
        title={selected?.title}
        size='lg'
      >
        <Stack>
          <Text size='sm'>{t`Live records may have changed since the count was loaded.`}</Text>
          {query.isPending ? (
            <Loader />
          ) : query.isError ? (
            <Alert color='yellow'>{t`Records unavailable`}</Alert>
          ) : (
            selectedResult?.records?.map((record) => {
              const valid =
                record.model === selected?.model &&
                Number.isSafeInteger(record.pk) &&
                record.pk > 0;
              return (
                <Group key={record.pk} justify='space-between'>
                  {valid ? (
                    <Anchor
                      component={Link}
                      to={getDetailUrl(record.model, record.pk)}
                    >
                      {record.label}
                    </Anchor>
                  ) : (
                    <Text>{record.label}</Text>
                  )}
                  <Text size='sm'>{record.due_date}</Text>
                </Group>
              );
            })
          )}
          {selectedResult?.count === 0 && <Text>{t`No overdue records`}</Text>}
          <Group justify='space-between'>
            <Button
              variant='default'
              disabled={offset === 0 || query.isFetching}
              onClick={() => setOffset(Math.max(0, offset - 25))}
            >{t`Previous`}</Button>
            <Button
              variant='default'
              disabled={selectedResult?.next_offset == null || query.isFetching}
              onClick={() => setOffset(selectedResult!.next_offset!)}
            >{t`Next`}</Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
