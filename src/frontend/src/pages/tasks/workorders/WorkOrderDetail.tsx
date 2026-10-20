import { t } from '@lingui/core/macro';
import {
  Accordion,
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  Timeline,
  Title
} from '@mantine/core';
import { useElementSize, useMediaQuery } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconCalendarClock,
  IconGitBranch,
  IconHistory,
  IconLayoutKanban,
  IconMessageChatbot,
  IconPackage,
  IconShieldCheck
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  type CSSProperties,
  type ReactNode,
  useEffect,
  useRef,
  useState
} from 'react';
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams
} from 'react-router-dom';

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
import { openGlobalAIChatAboutWork } from '../../../states/AIChatState';
import { MonitoringContinuity } from '../../assets/health/MonitoringContinuity';
import { InvestigationSection } from '../../maintenance/workorders/components/InvestigationSection';
import { WorkOrderAlertContext } from '../../maintenance/workorders/components/WorkOrderAlertContext';
import { WorkOrderProblemPanel } from '../../maintenance/workorders/components/WorkOrderProblemPanel';
import {
  WorkOrderSafetyReadiness,
  gateStatusColor,
  useGateAuthorityClock
} from '../../maintenance/workorders/components/WorkOrderSafetyReadiness';
import { CloseoutLink } from './CloseoutLink';
import { CloseoutPanel, WorkCloseoutReadings } from './CloseoutPanel';
import { WorkAttachmentResource } from './WorkAttachmentResource';
import { WorkMaterialsPanel } from './WorkMaterialsPanel';
import { WorkPanelNavigation } from './WorkPanelNavigation';
import { DiagnoseProjection } from './execution/DiagnoseProjection';
import { PreparedPackage } from './execution/PreparedPackage';
import { RoutineWork } from './execution/RoutineWork';
import { ScopeRevisionNotice } from './execution/ScopeRevisionReview';
import { ServiceRestoration } from './execution/ServiceRestoration';
import { TimeAssignment } from './execution/TimeAssignment';
import { VerificationReturn } from './execution/VerificationReturn';
import { WorkBrief } from './execution/WorkBrief';
import {
  WorkContinuity,
  WorkContinuityConstraints,
  WorkContinuityHistory
} from './execution/WorkContinuity';
import classes from './workCompanion.module.css';
import {
  type WorkPanel,
  workPanelParams,
  workPanelState
} from './workPanelState';
import { projectCloseoutWorkOrder } from './workProjection';

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
  const api = useApi();
  const workOrderId = id && /^\d+$/.test(id) ? Number(id) : Number.NaN;

  const workOrderQuery = useQuery({
    queryKey: ['work-order', workOrderId],
    enabled: Number.isSafeInteger(workOrderId) && workOrderId > 0,
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
  const denied = [401, 403, 404].includes(
    (workOrderQuery.error as any)?.response?.status
  );
  if (denied || !workOrderQuery.data) {
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

  return (
    <>
      {workOrderQuery.isError && (
        <Alert color='orange' title={t`Latest Work unavailable`} m='md'>
          <Text size='sm'>{t`Last-known record is shown. Drafts are retained. Refresh before relying on its current state.`}</Text>
          <Text size='xs'>
            {t`Last-known Work version`}:{' '}
            {workOrderQuery.data.lifecycle_version}
            {' · '}
            {t`Last received in this browser`}:{' '}
            {workOrderQuery.dataUpdatedAt > 0
              ? formatDateTime(
                  new Date(workOrderQuery.dataUpdatedAt).toISOString()
                )
              : t`Time not recorded`}
          </Text>
          <Button
            variant='light'
            onClick={() => void workOrderQuery.refetch()}
          >{t`Retry Work`}</Button>
        </Alert>
      )}
      <WorkOrderWorkspace
        key={workOrderQuery.data.id}
        workOrder={workOrderQuery.data}
      />
    </>
  );
}

function WorkOrderWorkspace({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const activePanel = workPanelState(params);
  const isPhone = useMediaQuery('(max-width: 47.99em)');
  const { ref: navigationRef, height: navigationHeight } =
    useElementSize<HTMLDivElement>();
  const workflowRef = useRef<HTMLDivElement>(null);
  const previousPanel = useRef(activePanel);
  useEffect(() => {
    if (
      previousPanel.current !== activePanel &&
      window.matchMedia('(max-width: 47.99em)').matches
    )
      workflowRef.current?.scrollIntoView({ block: 'start' });
    previousPanel.current = activePanel;
  }, [activePanel]);
  // P02/P33: the one existing assistant, asked about this exact Work at the
  // lifecycle version shown here. The drawer acknowledges it with the server
  // before sending; this page grants nothing.
  const canAskAboutWork =
    Number.isSafeInteger(workOrder.lifecycle_version) &&
    workOrder.lifecycle_version > 0;
  const askAboutWork = () =>
    openGlobalAIChatAboutWork({
      workOrderId: workOrder.id,
      lifecycleVersion: workOrder.lifecycle_version,
      label: `${workOrder.reference} · ${workOrder.title}`,
      returnPath: `${location.pathname}${location.search}`
    });
  const [visited, setVisited] = useState<Set<WorkPanel>>(
    () => new Set(activePanel ? [activePanel] : [])
  );
  useEffect(() => {
    if (activePanel)
      setVisited((previous) =>
        previous.has(activePanel)
          ? previous
          : new Set([...previous, activePanel])
      );
  }, [activePanel]);

  const panels: { name: WorkPanel; label: string; content: ReactNode }[] = [
    {
      name: 'work',
      label: t`Work`,
      content: (
        <Stack gap='md'>
          <WorkBrief workOrder={workOrder} />
          <ScopeRevisionNotice workOrder={workOrder} />
          <CloseoutLink />
          <RoutineWork workOrder={workOrder} />
          {workOrder.repair_packet && (
            <WorkOrderProblemPanel packet={workOrder.repair_packet} />
          )}
          <ServiceRestoration workOrder={workOrder} />
          <WorkContinuity workOrder={workOrder} />
          <TimeAssignment workOrder={workOrder} />
          <MonitoringContinuity workOrder={workOrder} />
          <SectionCard
            title={t`Board cards`}
            icon={<IconLayoutKanban size={18} />}
          >
            <Text
              size='sm'
              c='dimmed'
              mb='sm'
            >{t`Operational tasks retain their own recorded status and links.`}</Text>
            <Table.ScrollContainer minWidth={640}>
              <CardsTable cards={workOrder.cards ?? []} />
            </Table.ScrollContainer>
          </SectionCard>
        </Stack>
      )
    },
    {
      name: 'diagnose',
      label: t`Diagnose`,
      content: (
        <Stack gap='md'>
          <ScopeRevisionNotice workOrder={workOrder} />
          <DiagnoseProjection
            workOrder={workOrder}
            nativeFindings={
              workOrder.repair_packet?.findings.length ? (
                <Table.ScrollContainer minWidth={900}>
                  <InvestigationSection
                    findings={workOrder.repair_packet.findings}
                    approvedScope={null}
                  />
                </Table.ScrollContainer>
              ) : (
                <Alert
                  color='gray'
                  title={t`No findings in this overview`}
                >{t`Recorded findings are not supplied here. This does not establish a cause or mean that all evidence sources have been checked.`}</Alert>
              )
            }
          />
        </Stack>
      )
    },
    {
      name: 'plan',
      label: t`Plan & Safety`,
      content: (
        <Stack gap='md'>
          <ScopeRevisionNotice workOrder={workOrder} />
          <WorkContinuityConstraints workOrder={workOrder} />
          <PreparedPackage
            value={workOrder.prepared_package}
            authority={workOrder.operational_authority}
            workOrderId={workOrder.id}
          />
          {workOrder.repair_packet ? (
            <>
              {!workOrder.prepared_package &&
                workOrder.repair_packet.approved_scope && (
                  <InvestigationSection
                    findings={[]}
                    approvedScope={workOrder.repair_packet.approved_scope}
                  />
                )}
              <WorkOrderSafetyReadiness
                gates={workOrder.repair_packet.gates}
                physical={workOrder.operational_authority?.physical}
              />
              <RepairPacketCard
                packet={workOrder.repair_packet}
                physical={workOrder.operational_authority?.physical}
              />
            </>
          ) : (
            <Alert
              color='gray'
              title={t`No linked repair packet`}
            >{t`Safety and approved-scope records are not included in this overview. This is not permission to begin physical work.`}</Alert>
          )}
          <SectionCard
            title={t`Dependencies`}
            icon={<IconGitBranch size={18} />}
          >
            <DependenciesTable dependencies={workOrder.dependencies} />
          </SectionCard>
        </Stack>
      )
    },
    {
      name: 'parts',
      label: t`Parts`,
      content: (
        <SectionCard
          title={t`Parts for this job`}
          icon={<IconPackage size={18} />}
        >
          <ScopeRevisionNotice workOrder={workOrder} />
          {/* R6 P22: requirement → reservation → issue → use, each recorded. */}
          <WorkMaterialsPanel
            workOrderId={workOrder.id}
            label={workOrder.reference || t`Work order ${workOrder.id}`}
            fallback={<PartsTable parts={workOrder.parts} />}
          />
        </SectionCard>
      )
    },
    {
      name: 'verify',
      label: t`Verify & Return`,
      // R6 P23 projection, then the shared native closeout readings (P24).
      content: (
        <Stack gap='md'>
          <ScopeRevisionNotice workOrder={workOrder} />
          <VerificationReturn workOrder={workOrder} />
          {workOrder.canonical_commands_enabled &&
            workOrder.closeout_wizard_enabled !== false && (
              <WorkCloseoutReadings
                workOrderId={workOrder.id}
                readOnly={workOrder.lifecycle_status === 'completed'}
              />
            )}
        </Stack>
      )
    },
    {
      name: 'closeout',
      label: t`Closeout`,
      content: (
        <Stack gap='md'>
          {(workOrder.maintenance_record || workOrder.structured_closeout) && (
            <CompletionCard
              maintenance={workOrder.maintenance_record}
              closeout={workOrder.structured_closeout}
            />
          )}
          <MonitoringContinuity workOrder={workOrder} />
          {workOrder.canonical_commands_enabled || workOrder.repair_packet ? (
            <CloseoutPanel
              workOrder={projectCloseoutWorkOrder(workOrder)}
              accepted={workOrder.structured_closeout}
              execution={workOrder}
              packetId={workOrder.repair_packet?.id}
              captureEnabled={workOrder.closeout_wizard_enabled !== false}
            />
          ) : (
            <Text c='dimmed'>{t`Standalone completion is not enabled for this work order.`}</Text>
          )}
        </Stack>
      )
    },
    {
      name: 'history',
      label: t`History`,
      content: (
        <Stack gap='md'>
          <MonitoringContinuity workOrder={workOrder} />
          <WorkContinuityHistory workOrder={workOrder} />
          <SectionCard title={t`Activity`} icon={<IconHistory size={18} />}>
            <EventTimeline events={workOrder.events} />
          </SectionCard>
        </Stack>
      )
    }
  ];

  // Keep secondary metadata after the phone workflow in both DOM and visual
  // order. The source alert stays above it at every width; panel forms retain
  // their native mounted state when the breakpoint changes.
  const recordDetails = (
    <Accordion variant='contained'>
      <Accordion.Item value='record-details'>
        <Accordion.Control>{t`Record details`}</Accordion.Control>
        <Accordion.Panel>
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
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  );

  return (
    <Stack
      gap='md'
      className={classes.workspace}
      style={
        { '--work-navigation-height': `${navigationHeight}px` } as CSSProperties
      }
    >
      <PageTitle title={`${workOrder.reference} - ${workOrder.title}`} />
      <Group justify='space-between' align='flex-start'>
        <Group
          gap='sm'
          wrap='nowrap'
          align='flex-start'
          style={{ flex: '1 1 320px', minWidth: 0 }}
        >
          <ActionIcon
            variant='default'
            aria-label='work-order-back'
            style={{ flexShrink: 0 }}
            onClick={() => navigate(-1)}
          >
            <IconArrowLeft size={16} />
          </ActionIcon>
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Title order={3} style={{ overflowWrap: 'anywhere' }}>
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
          <Button
            variant='light'
            size='xs'
            leftSection={<IconMessageChatbot size={16} />}
            onClick={askAboutWork}
            disabled={!canAskAboutWork}
          >
            {t`Ask about this work`}
          </Button>
          <StateBadge
            value={workOrder.status}
            color='indigo'
            label={t`Board stage`}
          />
          <StateBadge
            value={workOrder.lifecycle_status}
            label={t`Lifecycle`}
            color={LIFECYCLE_COLORS[workOrder.lifecycle_status] ?? 'gray'}
          />
          <StateBadge
            value={workOrder.priority}
            label={t`Priority`}
            color={priorityColor(workOrder.priority)}
          />
        </Group>
      </Group>

      <WorkAttachmentResource workOrderId={workOrder.id} />

      {workOrder.source_alert && (
        <WorkOrderAlertContext alert={workOrder.source_alert} />
      )}

      {!isPhone && recordDetails}

      <Tabs
        ref={workflowRef}
        className={classes.workflow}
        value={activePanel}
        keepMounted
        keepMountedMode='display-none'
        onChange={(value) => {
          const panel = panels.find((entry) => entry.name === value);
          if (panel)
            setParams((previous) => workPanelParams(previous, panel.name));
        }}
      >
        <WorkPanelNavigation
          panels={panels}
          reference={workOrder.reference || t`Work order ${workOrder.id}`}
          navigationRef={navigationRef}
        />
        {!activePanel && (
          <Alert mt='md' color='red' title={t`Work panel unavailable`}>
            <Stack gap='xs'>
              <Text>{t`This link does not name a supported workflow panel.`}</Text>
              <Anchor
                component={Link}
                to={`?${workPanelParams(params, 'work')}`}
              >{t`Return to Work`}</Anchor>
            </Stack>
          </Alert>
        )}
        {panels.map((panel) => (
          <Tabs.Panel key={panel.name} value={panel.name} pt='md'>
            {visited.has(panel.name) ? panel.content : null}
          </Tabs.Panel>
        ))}
      </Tabs>
      {isPhone && recordDetails}
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
      <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
        {value || '-'}
      </Text>
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
  color,
  label
}: Readonly<{ value: string; color: string; label?: string }>) {
  return (
    <Badge variant='light' color={color}>
      {label ? `${label}: ${humanize(value)}` : humanize(value)}
    </Badge>
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
  packet,
  physical
}: Readonly<{
  packet: RepairPacketOverview;
  physical?: import('@lib/types/OperationalAuthority').AuthorityRecord[];
}>) {
  const { expired } = useGateAuthorityClock(packet.gates, physical);
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
        <InfoItem
          label={
            packet.diagnosis_is_preliminary
              ? t`Preliminary results`
              : t`Verified diagnosis`
          }
          value={likelyCause}
        />
      </SimpleGrid>
      <Divider />
      <Text size='sm' fw={600}>{t`Safety gates`}</Text>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {packet.gates.map((gate) => (
          <Group key={gate.id} justify='space-between' wrap='wrap'>
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
              color={gateStatusColor(gate, expired(gate))}
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
            {closeout.amended && (
              <Badge color='yellow' variant='light' w='fit-content'>
                {t`Amended`}
              </Badge>
            )}
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
      {[...events]
        .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id)
        .map((event) => (
          <Timeline.Item
            key={event.id}
            title={
              event.event_type.toUpperCase() === 'COMPLETED'
                ? t`Maintenance record updated`
                : humanize(event.event_type)
            }
          >
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
              {formatDateTime(event.created_at)} ·{' '}
              {event.actor != null
                ? t`Recorded by user ${event.actor}`
                : t`Actor not recorded`}
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
