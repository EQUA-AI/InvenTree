import type { RepairScopeDraftResponse } from '@lib/types/RepairScopeDraft';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  List,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useApi } from '../../../../contexts/ApiContext';
import {
  ExecutionFact,
  ExecutionSection,
  recordedTime
} from './ExecutionFacts';

function DraftList({
  title,
  items,
  ordered = false
}: Readonly<{ title: string; items: string[]; ordered?: boolean }>) {
  return (
    <Stack gap='xs'>
      <Title order={3} size='h5'>
        {title}
      </Title>
      <List size='sm' spacing='xs' type={ordered ? 'ordered' : 'unordered'}>
        {items.map((item, index) => (
          <List.Item key={`${index}:${item}`}>{item}</List.Item>
        ))}
      </List>
    </Stack>
  );
}

export function RepairScopeDraftPanel({
  workOrder,
  active
}: Readonly<{ workOrder: WorkOrderOverview; active: boolean }>) {
  const api = useApi();
  const enabled = active && workOrder.repair_packet !== null;
  const query = useQuery({
    queryKey: ['repair-scope-draft', workOrder.id],
    enabled,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: 'always',
    refetchInterval: (current) =>
      enabled && !current.state.error ? 5000 : false,
    queryFn: async () => {
      const response = await api.get<RepairScopeDraftResponse>(
        `/api/tasks/work-orders/${workOrder.id}/repair-scope-draft/`
      );
      const result = response.data;
      const draft = result.draft;
      if (
        result.work_order_id !== workOrder.id ||
        result.machine_id !== workOrder.machine ||
        result.repair_packet_id !== workOrder.repair_packet?.id ||
        (draft &&
          (draft.work_order_id !== result.work_order_id ||
            draft.machine_id !== result.machine_id ||
            draft.repair_packet_id !== result.repair_packet_id ||
            draft.status !== 'proposed' ||
            draft.gates?.supervisor_review !== 'pending' ||
            draft.gates?.site_safety_authorization !== 'pending' ||
            draft.gates?.physical_execution !== 'not_started' ||
            draft.gates?.post_repair_verification !== 'not_started' ||
            (workOrder.source_alert !== null &&
              draft.source_event_id !== workOrder.source_alert.id) ||
            typeof draft.scope?.suspected_cause !== 'string' ||
            typeof draft.scope?.parts_decision !== 'string' ||
            ![
              draft.scope?.steps,
              draft.scope?.tools_and_supplies,
              draft.scope?.required_ppe,
              draft.scope?.isolation_checks,
              draft.scope?.post_work_checks,
              draft.scope?.open_questions
            ].every(
              (items) =>
                Array.isArray(items) &&
                items.every((item) => typeof item === 'string')
            ) ||
            !Array.isArray(draft.scope?.replacement_parts) ||
            draft.scope.replacement_parts.length !== 0 ||
            draft.sources?.e03?.label !== 'P08-TEMP-E03' ||
            draft.sources?.e03?.prepared !== true ||
            !Array.isArray(draft.sources?.manuals) ||
            draft.sources.manuals.some(
              (source) =>
                source.applicability !== 'related_reference_unverified'
            )))
      )
        throw new Error('The proposed scope does not match the current work.');
      return result;
    }
  });

  if (!enabled) return null;
  if (query.isPending)
    return (
      <Group component='output'>
        <Loader size='xs' />
        <Text size='sm'>{t`Reading proposed repair scope…`}</Text>
      </Group>
    );
  if (query.isError || !query.data)
    return (
      <Alert color='orange' title={t`Proposed repair scope unavailable`}>
        <Stack gap='xs'>
          <Text size='sm'>{t`The current draft and its source links could not be verified. Review the current record before planning work.`}</Text>
          <Button
            variant='default'
            size='xs'
            onClick={() => void query.refetch()}
          >{t`Reload proposed scope`}</Button>
        </Stack>
      </Alert>
    );

  const result = query.data;
  const draft = result.draft;
  if (!draft) return null;
  const scope = draft.scope;

  return (
    <ExecutionSection
      title={t`Proposed repair scope`}
      testId='repair-scope-draft'
    >
      <Group justify='space-between' gap='sm'>
        <Text fw={600} data-testid='repair-scope-draft-identity'>
          {t`Draft`} #{draft.id} · {t`Revision`} {draft.revision}
        </Text>
        <Badge
          color='orange'
          variant='light'
        >{t`Proposed · Review pending`}</Badge>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <ExecutionFact label={t`Saved by`}>
          {draft.actor_id === null ? null : t`User ${draft.actor_id}`}
        </ExecutionFact>
        <ExecutionFact label={t`Saved at`}>
          {recordedTime(draft.saved_at)}
        </ExecutionFact>
        <ExecutionFact label={t`Saved work revision`}>
          {draft.lifecycle_version}
        </ExecutionFact>
      </SimpleGrid>
      {draft.lifecycle_version !== result.lifecycle_version && (
        <Alert color='orange' title={t`Work order changed after this draft`}>
          {t`Review the current work order and evidence before relying on this saved proposal.`}
        </Alert>
      )}
      <Stack gap='xs'>
        <Title order={3} size='h5'>{t`Suspected cause`}</Title>
        <Text size='sm'>{scope.suspected_cause}</Text>
      </Stack>
      <DraftList title={t`Scope`} items={scope.steps} ordered />
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing='lg'>
        <DraftList
          title={t`Tools and supplies`}
          items={scope.tools_and_supplies}
        />
        <DraftList title={t`Required PPE`} items={scope.required_ppe} />
        <DraftList title={t`Isolation checks`} items={scope.isolation_checks} />
        <DraftList title={t`Post-work checks`} items={scope.post_work_checks} />
      </SimpleGrid>
      <Stack gap='xs' data-testid='repair-scope-sources'>
        <Title order={3} size='h5'>{t`Supporting sources`}</Title>
        <Group gap='md'>
          <Anchor
            component={Link}
            size='sm'
            to={`/machines/machine/${draft.machine_id}/health/?anomaly=${draft.source_event_id}`}
          >
            {t`Source event`} #{draft.source_event_id}
          </Anchor>
          <Anchor
            component={Link}
            size='sm'
            to={`/repair/packets/${draft.repair_packet_id}/`}
          >
            {t`Repair packet`} #{draft.repair_packet_id}
          </Anchor>
          <Text size='sm'>{draft.component_code}</Text>
        </Group>
        <Text size='sm'>
          {draft.sources.e03.label} · {t`Prepared inspection evidence`} ·{' '}
          {t`Attachment`} #{draft.sources.e03.id}
        </Text>
        <Text size='xs' c='dimmed'>
          {draft.sources.e03.sha256}
        </Text>
        <Text size='sm'>{t`Related document references. Applicability to the fitted equipment remains unverified.`}</Text>
        {draft.sources.manuals.map((source) => (
          <Stack key={source.id} gap={2}>
            <Text size='sm' fw={600}>
              {source.title}
            </Text>
            <Text size='sm'>
              {source.filename} · {t`Attachment`} #{source.id}
            </Text>
            <Text size='xs' c='dimmed'>
              {source.sha256}
            </Text>
          </Stack>
        ))}
      </Stack>
      <Stack gap='xs' data-testid='repair-scope-parts'>
        <Group gap='xs'>
          <Title order={3} size='h5'>{t`Conditional parts decision`}</Title>
          <Badge color='gray' variant='outline'>
            0 {t`replacement parts`}
          </Badge>
        </Group>
        <Text size='sm'>{scope.parts_decision}</Text>
      </Stack>
      <DraftList title={t`Open questions`} items={scope.open_questions} />
      <Alert
        color='orange'
        title={t`Draft review status when saved`}
        data-testid='repair-scope-pending-gates'
      >
        <Stack gap='xs'>
          <Text size='sm'>{t`This saved proposal is not the current approval or execution record.`}</Text>
          <Text size='sm'>{t`Supervisor review: pending`}</Text>
          <Text size='sm'>{t`Site safety authorization: pending`}</Text>
          <Text size='sm'>{t`Physical execution: not started`}</Text>
          <Text size='sm'>{t`Post-repair verification: not started`}</Text>
        </Stack>
      </Alert>
    </ExecutionSection>
  );
}
