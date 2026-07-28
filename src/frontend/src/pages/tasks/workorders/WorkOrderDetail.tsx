import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Card,
  Divider,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Timeline,
  Title
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconCalendarClock,
  IconGitBranch,
  IconHistory,
  IconLayoutKanban,
  IconPackage,
  IconShieldCheck,
  IconTools
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import type { ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { BoardCard, WorkOrderPart } from '@lib/types/Tasks';

import type {
  MaintenanceRecordOverview,
  RepairPacketOverview,
  StructuredCloseoutOverview,
  WorkOrderDependency,
  WorkOrderEvent,
  WorkOrderOverview,
  WorkOrderSummary
} from '@lib/types/WorkOrderOverview';
import PageTitle from '../../../components/nav/PageTitle';

import { useApi } from '../../../contexts/ApiContext';
import { InvestigationSection } from '../../maintenance/workorders/components/InvestigationSection';
import { WorkOrderAlertContext } from '../../maintenance/workorders/components/WorkOrderAlertContext';
import { WorkOrderProblemPanel } from '../../maintenance/workorders/components/WorkOrderProblemPanel';
import { WorkOrderSafetyReadiness } from '../../maintenance/workorders/components/WorkOrderSafetyReadiness';
import { CloseoutPanel, type CloseoutWorkOrder } from './CloseoutPanel';

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
 * Complete Work Order detail page.
 *
 * Uses the always-enabled Kanban overview surface for operational context. The
 * feature-gated canonical API is used only for closeout commands when available.
 */
export default function WorkOrderDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const api = useApi();
  const workOrderId = Number(id);

  const workOrderQuery = useQuery({
    queryKey: ['work-order', workOrderId],
    enabled: Number.isFinite(workOrderId),
    retry: false,
    queryFn: async () => {
      const response = await api.get<WorkOrderOverview>(
        apiUrl(ApiEndpoints.kanban_card_overview, workOrderId)
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
        <Stack gap='xs'>
          <Text size='sm'>
            {t`This work order does not exist or is outside your access scope.`}
          </Text>
          <Anchor component={Link} to='/maintenance/board' size='sm'>
            {t`Return to the work-order board`}
          </Anchor>
        </Stack>
      </Alert>
    );
  }

  const workOrder = workOrderQuery.data;

  return (
    <Stack gap='md' p='md'>
      <PageTitle title={`${workOrder.reference} - ${workOrder.title}`} />
      <Group justify='space-between'>
        <Group gap='sm' wrap='nowrap'>
          <ActionIcon
            variant='default'
            aria-label='work-order-back'
            onClick={() => navigate(-1)}
          >
            <IconArrowLeft size={16} />
          </ActionIcon>
          <Stack gap={2}>
            <Title order={3}>
              {workOrder.reference}: {workOrder.title}
            </Title>
            <Text size='sm' c='dimmed'>
              {workOrder.machine_name ?? t`No machine assigned`}
              {workOrder.machine_location
                ? ` · ${workOrder.machine_location}`
                : ''}
            </Text>
          </Stack>
        </Group>
        <Group gap='xs'>
          <StateBadge value={workOrder.status} color='indigo' />
          <StateBadge
            value={workOrder.lifecycle_status}
            color={LIFECYCLE_COLORS[workOrder.lifecycle_status] ?? 'gray'}
          />
          <StateBadge
            value={workOrder.priority}
            color={priorityColor(workOrder.priority)}
          />
        </Group>
      </Group>

      {workOrder.source_alert && (
        <WorkOrderAlertContext alert={workOrder.source_alert} />
      )}

      {workOrder.parent_detail && (
        <Alert color='blue' icon={<IconGitBranch size={16} />}>
          {t`This task belongs to`}{' '}
          <Anchor
            component={Link}
            to={`/maintenance/work-orders/${workOrder.parent_detail.id}/`}
          >
            {displayReference(workOrder.parent_detail)}
          </Anchor>
        </Alert>
      )}

      <Card withBorder padding='md'>
        <Stack gap='md'>
          {workOrder.description && (
            <Text size='sm'>{workOrder.description}</Text>
          )}
          <Divider />
          <SimpleGrid cols={{ base: 2, md: 4 }}>
            <InfoItem
              label={t`Board stage`}
              value={humanize(workOrder.status)}
            />
            <InfoItem
              label={t`Type`}
              value={humanize(workOrder.work_order_type)}
            />
            <InfoItem label={t`Assignee`} value={assigneeLabel(workOrder)} />
            <InfoItem
              label={t`Estimated effort`}
              value={formatMinutes(workOrder.estimated_minutes)}
            />
            <InfoItem
              label={t`Scheduled start`}
              value={formatDateTime(workOrder.scheduled_start)}
            />
            <InfoItem
              label={t`Scheduled end`}
              value={formatDateTime(workOrder.scheduled_end)}
            />
            <InfoItem
              label={t`Due date`}
              value={formatDate(workOrder.due_date)}
            />
            <InfoItem
              label={t`Actual start`}
              value={formatDateTime(workOrder.actual_started_at)}
            />
            <InfoItem
              label={t`Actual completion`}
              value={formatDateTime(workOrder.actual_completed_at)}
            />
            <InfoItem label={t`Reference`} value={workOrder.reference} />
            <InfoItem
              label={t`Created`}
              value={formatDateTime(workOrder.created_at)}
            />
            <InfoItem
              label={t`Last updated`}
              value={formatDateTime(workOrder.updated_at)}
            />
          </SimpleGrid>
        </Stack>
      </Card>

      {workOrder.repair_packet && (
        <WorkOrderProblemPanel packet={workOrder.repair_packet} />
      )}

      {workOrder.repair_packet && (
        <InvestigationSection
          findings={workOrder.repair_packet.findings}
          approvedScope={workOrder.repair_packet.approved_scope}
        />
      )}

      {workOrder.repair_packet && (
        <WorkOrderSafetyReadiness gates={workOrder.repair_packet.gates} />
      )}

      <SectionCard title={t`Board cards`} icon={<IconLayoutKanban size={18} />}>
        <CardsTable cards={workOrder.cards ?? []} />
      </SectionCard>

      <SectionCard title={t`Jobs and tasks`} icon={<IconTools size={18} />}>
        <WorkOrderTable
          rows={workOrder.children}
          empty={t`No child jobs or tasks.`}
        />
      </SectionCard>

      <SectionCard title={t`Required parts`} icon={<IconPackage size={18} />}>
        <PartsTable parts={workOrder.parts} />
      </SectionCard>

      <SectionCard title={t`Dependencies`} icon={<IconGitBranch size={18} />}>
        <DependenciesTable dependencies={workOrder.dependencies} />
      </SectionCard>

      {workOrder.repair_packet && (
        <RepairPacketCard packet={workOrder.repair_packet} />
      )}

      {(workOrder.maintenance_record || workOrder.structured_closeout) && (
        <CompletionCard
          maintenance={workOrder.maintenance_record}
          closeout={workOrder.structured_closeout}
        />
      )}

      <SectionCard title={t`Activity`} icon={<IconHistory size={18} />}>
        <EventTimeline events={workOrder.events} />
      </SectionCard>

      {workOrder.canonical_commands_enabled && !workOrder.repair_packet && (
        <CloseoutPanel workOrder={closeoutWorkOrder(workOrder)} />
      )}
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

function SectionCard({
  title,
  icon,
  children
}: Readonly<{ title: string; icon: ReactNode; children: ReactNode }>) {
  return (
    <Card withBorder padding='md'>
      <Stack gap='sm'>
        <Group gap='xs'>
          {icon}
          <Title order={4}>{title}</Title>
        </Group>
        {children}
      </Stack>
    </Card>
  );
}

function StateBadge({
  value,
  color
}: Readonly<{ value: string; color: string }>) {
  return (
    <Badge variant='light' color={color}>
      {humanize(value)}
    </Badge>
  );
}

function WorkOrderTable({
  rows,
  empty
}: Readonly<{ rows: WorkOrderSummary[]; empty: string }>) {
  if (rows.length === 0) return <Text c='dimmed'>{empty}</Text>;

  return (
    <Table.ScrollContainer minWidth={760}>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Task`}</Table.Th>
            <Table.Th>{t`Board stage`}</Table.Th>
            <Table.Th>{t`Lifecycle`}</Table.Th>
            <Table.Th>{t`Type`}</Table.Th>
            <Table.Th>{t`Assignee`}</Table.Th>
            <Table.Th>{t`Schedule`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.id}>
              <Table.Td>
                <Anchor
                  component={Link}
                  to={`/maintenance/work-orders/${row.id}/`}
                >
                  {displayReference(row)}
                </Anchor>
                <Text size='xs' c='dimmed' lineClamp={1}>
                  {row.description}
                </Text>
              </Table.Td>
              <Table.Td>
                <StateBadge
                  value={row.status}
                  color={statusColor(row.status)}
                />
              </Table.Td>
              <Table.Td>{humanize(row.lifecycle_status)}</Table.Td>
              <Table.Td>{humanize(row.card_kind)}</Table.Td>
              <Table.Td>{row.assigned_to_name ?? '-'}</Table.Td>
              <Table.Td>
                <Text size='xs'>{formatDateTime(row.scheduled_start)}</Text>
                <Text size='xs' c='dimmed'>
                  {formatMinutes(row.estimated_minutes)}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/**
 * The pieces this job is being worked through.
 *
 * A job with one card is the ordinary case and says so plainly; a job broken
 * down shows each piece and the column it currently sits in, which is the whole
 * reason cards and work orders are separate.
 */
function CardsTable({ cards }: Readonly<{ cards: BoardCard[] }>) {
  if (cards.length === 0) {
    return (
      <Text size='sm' c='dimmed'>
        {t`No cards on the board for this work order.`}
      </Text>
    );
  }

  return (
    <Table striped highlightOnHover>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t`Card`}</Table.Th>
          <Table.Th>{t`Kind`}</Table.Th>
          <Table.Th>{t`Column`}</Table.Th>
          <Table.Th>{t`Scheduled`}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {cards.map((card) => (
          <Table.Tr key={card.id}>
            <Table.Td>
              <Text size='sm'>{card.title}</Text>
            </Table.Td>
            <Table.Td>
              <Badge variant='light' color='gray'>
                {card.card_kind}
              </Badge>
            </Table.Td>
            <Table.Td>
              <Badge variant='light'>{card.status}</Badge>
            </Table.Td>
            <Table.Td>
              <Text size='sm' c={card.effective_start ? undefined : 'dimmed'}>
                {card.effective_start
                  ? dayjs(card.effective_start).format('MMM D, HH:mm')
                  : t`Unscheduled`}
              </Text>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function PartsTable({ parts }: Readonly<{ parts: WorkOrderPart[] }>) {
  if (parts.length === 0)
    return <Text c='dimmed'>{t`No required parts.`}</Text>;

  return (
    <Table.ScrollContainer minWidth={680}>
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Part`}</Table.Th>
            <Table.Th>{t`Required`}</Table.Th>
            <Table.Th>{t`Allocated`}</Table.Th>
            <Table.Th>{t`Status`}</Table.Th>
            <Table.Th>{t`Notes`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {parts.map((part) => (
            <Table.Tr key={part.id}>
              <Table.Td>
                <Text size='sm' fw={600}>
                  {part.part_name}
                </Text>
                <Text size='xs' c='dimmed'>
                  {part.part_ipn || '-'}
                </Text>
              </Table.Td>
              <Table.Td>{part.quantity}</Table.Td>
              <Table.Td>{part.allocated_quantity}</Table.Td>
              <Table.Td>
                <StateBadge
                  value={part.allocation_status}
                  color={part.allocation_status === 'full' ? 'green' : 'orange'}
                />
              </Table.Td>
              <Table.Td>{part.allocation_note || '-'}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function DependenciesTable({
  dependencies
}: Readonly<{ dependencies: WorkOrderDependency[] }>) {
  if (dependencies.length === 0) {
    return <Text c='dimmed'>{t`No work-order dependencies.`}</Text>;
  }

  return (
    <Table.ScrollContainer minWidth={620}>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Direction`}</Table.Th>
            <Table.Th>{t`Work order`}</Table.Th>
            <Table.Th>{t`Constraint`}</Table.Th>
            <Table.Th>{t`Status`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {dependencies.map((dependency) => (
            <Table.Tr key={dependency.id}>
              <Table.Td>{humanize(dependency.direction)}</Table.Td>
              <Table.Td>
                <Anchor
                  component={Link}
                  to={`/maintenance/work-orders/${dependency.card.id}/`}
                >
                  {displayReference(dependency.card)}
                </Anchor>
              </Table.Td>
              <Table.Td>
                {dependency.dependency_type}
                {dependency.lag_minutes
                  ? ` · ${formatMinutes(dependency.lag_minutes)} lag`
                  : ''}
              </Table.Td>
              <Table.Td>
                <StateBadge
                  value={dependency.card.status}
                  color={statusColor(dependency.card.status)}
                />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function RepairPacketCard({
  packet
}: Readonly<{ packet: RepairPacketOverview }>) {
  const likelyCause =
    typeof packet.diagnosis.likely_cause === 'string'
      ? packet.diagnosis.likely_cause
      : '';

  return (
    <SectionCard title={t`Repair packet`} icon={<IconShieldCheck size={18} />}>
      <Group gap='xs'>
        <Anchor component={Link} to={`/repair/packets/${packet.id}/`} fw={600}>
          {packet.reference}
        </Anchor>
        <StateBadge value={packet.status} color='blue' />
        <StateBadge
          value={packet.criticality}
          color={priorityColor(packet.criticality)}
        />
        <StateBadge value={packet.generation_status} color='gray' />
      </Group>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <InfoItem label={t`Fault`} value={packet.fault_summary} />
        <InfoItem label={t`Symptom`} value={packet.symptom} />
        <InfoItem
          label={t`Production impact`}
          value={packet.production_impact}
        />
        <InfoItem label={t`Likely cause`} value={likelyCause} />
      </SimpleGrid>
      <Divider />
      <Text size='sm' fw={600}>{t`Safety gates`}</Text>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {packet.gates.map((gate) => (
          <Group key={gate.id} justify='space-between' wrap='nowrap'>
            <Stack gap={0}>
              <Text size='sm'>{gate.name}</Text>
              <Text size='xs' c='dimmed'>
                {humanize(gate.gate_type)}
                {gate.requires_second_person ? ` · ${t`Second person`}` : ''}
                {gate.requires_photo ? ` · ${t`Photo required`}` : ''}
              </Text>
            </Stack>
            <StateBadge
              value={gate.status}
              color={gate.status === 'confirmed' ? 'green' : 'orange'}
            />
          </Group>
        ))}
      </SimpleGrid>
    </SectionCard>
  );
}

function CompletionCard({
  maintenance,
  closeout
}: Readonly<{
  maintenance: MaintenanceRecordOverview | null;
  closeout: StructuredCloseoutOverview | null;
}>) {
  return (
    <SectionCard title={t`Completion`} icon={<IconCalendarClock size={18} />}>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {maintenance && (
          <Stack gap='xs'>
            <Text fw={600}>{maintenance.summary}</Text>
            <Text size='sm'>{maintenance.details}</Text>
            <Text size='xs' c='dimmed'>
              {formatDate(maintenance.date)} · {maintenance.performed_by}
            </Text>
          </Stack>
        )}
        {closeout && (
          <Stack gap='xs'>
            <InfoItem label={t`Action`} value={closeout.action} />
            <InfoItem label={t`Result`} value={closeout.result} />
            <InfoItem
              label={t`Verification`}
              value={closeout.verification_summary}
            />
            <InfoItem
              label={t`Downtime`}
              value={formatMinutes(closeout.downtime_minutes)}
            />
            {closeout.follow_up_required && (
              <InfoItem label={t`Follow-up`} value={closeout.follow_up} />
            )}
          </Stack>
        )}
      </SimpleGrid>
    </SectionCard>
  );
}

function EventTimeline({ events }: Readonly<{ events: WorkOrderEvent[] }>) {
  if (events.length === 0)
    return <Text c='dimmed'>{t`No audit events yet.`}</Text>;

  return (
    <Timeline bulletSize={22} lineWidth={2} active={events.length}>
      {events.map((event) => (
        <Timeline.Item key={event.id} title={humanize(event.event_type)}>
          <Text size='sm'>
            {[event.from_status, event.to_status]
              .filter(Boolean)
              .map(humanize)
              .join(' → ') ||
              event.reason ||
              '-'}
          </Text>
          {event.reason && <Text size='xs'>{event.reason}</Text>}
          <Text size='xs' c='dimmed'>
            {formatDateTime(event.created_at)}
          </Text>
        </Timeline.Item>
      ))}
    </Timeline>
  );
}

function displayReference(
  workOrder: Pick<WorkOrderSummary, 'reference' | 'title'>
) {
  return `${workOrder.reference ?? `WO-${workOrder.title}`} · ${workOrder.title}`;
}

function assigneeLabel(workOrder: WorkOrderOverview): string {
  return workOrder.assigned_to_name || workOrder.assignee || '-';
}

function closeoutWorkOrder(workOrder: WorkOrderOverview): CloseoutWorkOrder {
  return {
    id: workOrder.id,
    reference: workOrder.reference ?? `WO-${workOrder.id}`,
    title: workOrder.title,
    lifecycle_status: workOrder.lifecycle_status,
    lifecycle_version: workOrder.lifecycle_version
  };
}

function humanize(value: string): string {
  return value
    .replaceAll('_', ' ')
    .replaceAll('-', ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatDateTime(value: string | null): string {
  return value ? dayjs(value).format('DD MMM YYYY, HH:mm') : '-';
}

function formatDate(value: string | null): string {
  return value ? dayjs(value).format('DD MMM YYYY') : '-';
}

function formatMinutes(value: number | null): string {
  if (value == null) return '-';
  if (value < 60) return `${value} min`;
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

function priorityColor(value: string): string {
  return value === 'high' || value === 'critical'
    ? 'red'
    : value === 'medium'
      ? 'yellow'
      : 'teal';
}

function statusColor(value: string): string {
  if (value === 'in-progress') return 'indigo';
  if (value === 'review') return 'yellow';
  if (value === 'done') return 'green';
  return 'gray';
}
