import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import PageTitle from '../../../components/nav/PageTitle';
import { useApi } from '../../../contexts/ApiContext';
import { CloseoutPanel, type CloseoutWorkOrder } from './CloseoutPanel';

interface WorkOrderResource extends CloseoutWorkOrder {
  description: string;
  work_order_type: string;
  machine: number | null;
  assignee: string;
  scheduled_start: string | null;
  scheduled_end: string | null;
  actual_started_at: string | null;
  actual_completed_at: string | null;
}

const LIFECYCLE_COLORS: Record<string, string> = {
  draft: 'gray',
  planned: 'blue',
  ready: 'cyan',
  in_progress: 'yellow',
  on_hold: 'orange',
  verifying: 'violet',
  completed: 'green',
  canceled: 'red'
};

/**
 * Canonical Work Order detail page (Feature #15).
 *
 * Hosts the closeout wizard; the Kanban board deep-links here. The page is
 * additive — it renders only when the canonical work-order API is enabled.
 */
export default function WorkOrderDetail() {
  const { id } = useParams();
  const api = useApi();
  const workOrderId = Number(id);

  const workOrderQuery = useQuery({
    queryKey: ['work-order', workOrderId],
    enabled: Number.isFinite(workOrderId),
    retry: false,
    queryFn: async () => {
      const response = await api.get<WorkOrderResource>(
        apiUrl(ApiEndpoints.work_order_detail, workOrderId)
      );
      return response.data;
    }
  });

  if (workOrderQuery.isLoading) {
    return <Loader mt='xl' />;
  }
  if (workOrderQuery.isError || !workOrderQuery.data) {
    return (
      <Alert
        color='red'
        icon={<IconAlertTriangle size={16} />}
        title={t`Work order unavailable`}
        m='md'
      >
        {t`This work order does not exist, is out of your scope, or the canonical work-order API is disabled.`}
      </Alert>
    );
  }

  const workOrder = workOrderQuery.data;

  return (
    <Stack gap='md' p='md'>
      <PageTitle title={`${workOrder.reference} - ${workOrder.title}`} />
      <Group justify='space-between'>
        <Stack gap={2}>
          <Title order={3}>
            {workOrder.reference}: {workOrder.title}
          </Title>
          {workOrder.description && (
            <Text size='sm' c='dimmed'>
              {workOrder.description}
            </Text>
          )}
        </Stack>
        <Badge
          size='lg'
          color={LIFECYCLE_COLORS[workOrder.lifecycle_status] ?? 'gray'}
        >
          {workOrder.lifecycle_status}
        </Badge>
      </Group>
      <Card withBorder padding='md'>
        <SimpleGrid cols={{ base: 2, md: 4 }}>
          <InfoItem label={t`Type`} value={workOrder.work_order_type} />
          <InfoItem label={t`Assignee`} value={workOrder.assignee} />
          <InfoItem
            label={t`Started`}
            value={workOrder.actual_started_at ?? '-'}
          />
          <InfoItem
            label={t`Completed`}
            value={workOrder.actual_completed_at ?? '-'}
          />
        </SimpleGrid>
      </Card>
      <CloseoutPanel workOrder={workOrder} />
    </Stack>
  );
}

function InfoItem({
  label,
  value
}: Readonly<{ label: string; value: string | null }>) {
  return (
    <Stack gap={0}>
      <Text size='xs' c='dimmed'>
        {label}
      </Text>
      <Text size='sm'>{value || '-'}</Text>
    </Stack>
  );
}
