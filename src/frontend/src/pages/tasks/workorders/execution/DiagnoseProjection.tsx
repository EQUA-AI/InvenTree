import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  List,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconListCheck,
  IconPencil,
  IconScale,
  IconStethoscope,
  IconTool
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import type {
  EvidenceRelation,
  InvestigationEvidence,
  InvestigationExplanation
} from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';

import { ContinueInvestigation } from './ContinueInvestigation';
import { unattributedVerificationLabel } from './ExecutionFacts';
import { InvestigationRequests } from './InvestigationRequests';
import {
  isSnapshotId,
  measured,
  readInvestigation
} from './investigationProjection';

/** Badge text that wraps on a phone instead of truncating its meaning. */
const WRAP_BADGE = {
  root: { height: 'auto', paddingBlock: 2, maxWidth: '100%' },
  label: { whiteSpace: 'normal' as const, overflow: 'visible' }
};

function when(value: string | null | undefined): string | null {
  return value ? dayjs(value).format('DD MMM YYYY, HH:mm') : null;
}

function analysisLabel(status: string | null): string | null {
  switch (status) {
    case 'available':
      return t`Evidence available`;
    case 'stale':
      return t`Data is stale`;
    case 'insufficient':
      return t`Data is insufficient`;
    case 'unavailable':
      return t`No data available`;
    default:
      return null;
  }
}

function Fact({
  label,
  value
}: Readonly<{ label: string; value: string | null }>) {
  return (
    <Stack gap={0}>
      <Text size='xs'>{label}</Text>
      <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
        {value ?? t`Not recorded`}
      </Text>
    </Stack>
  );
}

function Section({
  title,
  icon,
  children,
  testId
}: Readonly<{
  title: string;
  icon: ReactNode;
  children: ReactNode;
  testId?: string;
}>) {
  return (
    <Card withBorder padding='md' data-testid={testId}>
      <Stack gap='sm'>
        <Group gap='xs' wrap='nowrap' align='center'>
          {icon}
          <Title order={2} size='h4'>
            {title}
          </Title>
        </Group>
        {children}
      </Stack>
    </Card>
  );
}

function ExplanationBasis({
  explanation
}: Readonly<{ explanation: InvestigationExplanation }>) {
  const window = explanation.data_window;
  const from = when(window.start);
  const to = when(window.end);
  const span = from && to ? t`${from} to ${to}` : null;
  const snapshots =
    typeof window.snapshot_count === 'number'
      ? t`${window.snapshot_count} snapshot(s)`
      : null;
  return (
    <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing='sm'>
      <Fact
        label={t`Analysis`}
        value={analysisLabel(explanation.analysis_status)}
      />
      <Fact
        label={t`Evidence window`}
        value={[span, snapshots].filter(Boolean).join(' · ') || null}
      />
      <Fact
        label={t`Stale signals`}
        value={
          typeof explanation.stale_signal_count === 'number'
            ? String(explanation.stale_signal_count)
            : null
        }
      />
      <Fact label={t`Signal quality`} value={explanation.quality_summary} />
      <Fact
        label={t`Out of limits decided by`}
        value={
          explanation.authority === 'source_declared'
            ? t`The source system's own alarm`
            : explanation.authority === 'derived'
              ? t`A threshold configured in AIMMS`
              : null
        }
      />
      <Fact
        label={t`Prepared by`}
        value={
          [
            explanation.provider,
            explanation.model_or_rule_version,
            when(explanation.generated_at)
          ]
            .filter(Boolean)
            .join(' · ') || null
        }
      />
    </SimpleGrid>
  );
}

function EvidenceGroup({
  relation,
  items
}: Readonly<{ relation: EvidenceRelation; items: InvestigationEvidence[] }>) {
  const heading =
    relation === 'supports'
      ? t`Supports the explanation (${items.length})`
      : relation === 'contradicts'
        ? t`Contradicts it (${items.length})`
        : t`Related, relation not established (${items.length})`;
  const color =
    relation === 'supports'
      ? 'teal'
      : relation === 'contradicts'
        ? 'orange'
        : 'gray';
  return (
    <Stack gap={6} data-testid={`evidence-${relation}`}>
      <Group gap='xs'>
        <Badge variant='light' color={color} styles={WRAP_BADGE}>
          {heading}
        </Badge>
      </Group>
      {items.length === 0 ? (
        <Text size='sm'>
          {relation === 'contradicts'
            ? t`No contradicting evidence is cited.`
            : t`None cited.`}
        </Text>
      ) : (
        items.map((item, index) => {
          const value = measured(item.value, item.unit);
          return (
            <Stack
              key={`${relation}-${index}`}
              gap={0}
              pl='sm'
              style={{
                borderLeft: `3px solid var(--mantine-color-${color}-filled)`
              }}
            >
              <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
                {item.observation || t`No observation text recorded`}
              </Text>
              <Text size='xs' style={{ overflowWrap: 'anywhere' }}>
                {[
                  item.signal_label,
                  value,
                  when(item.observed_at),
                  item.snapshot_id
                    ? isSnapshotId(item.snapshot_id)
                      ? t`Snapshot ${item.snapshot_id}`
                      : t`Cited record ${item.snapshot_id}`
                    : t`No source record cited`,
                  item.stale ? t`stale` : null
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </Text>
            </Stack>
          );
        })
      )}
    </Stack>
  );
}

/**
 * P20 / UI12: the chat-primary Diagnose projection.
 *
 * Diagnose renders the saved investigation and offers one way to act on it:
 * Continue investigation in the existing assistant. There is no answer form,
 * check wizard, confidence score or round counter. The native findings table
 * (`nativeFindings`) stays underneath, unchanged.
 */
export function DiagnoseProjection({
  workOrder,
  nativeFindings
}: Readonly<{ workOrder: WorkOrderOverview; nativeFindings: ReactNode }>) {
  const [params] = useSearchParams();
  const reading = readInvestigation(workOrder.investigation);

  if (reading.kind === 'unsupported') {
    return (
      <Stack gap='md'>
        <ContinueInvestigation workOrder={workOrder} variant='light' />
        {nativeFindings}
      </Stack>
    );
  }

  const packet = workOrder.investigation?.packet;
  const nativePacketLink = packet ? (
    <Anchor component={Link} size='sm' to={`/repair/packets/${packet.id}/`}>
      {t`Open ${packet.reference} to record supported changes`}
    </Anchor>
  ) : null;

  const noRequests = (
    <InvestigationRequests
      requests={workOrder.investigation!.requests}
      workOrder={workOrder}
    />
  );

  const hasRequests = Boolean(
    workOrder.investigation?.requests.available &&
      workOrder.investigation.requests.items?.length
  );

  if (reading.kind === 'no_packet') {
    return (
      <Stack gap='md'>
        <Section
          title={t`Current explanation`}
          icon={<IconStethoscope size={18} />}
          testId='diagnose-explanation'
        >
          <Text size='sm'>
            {hasRequests
              ? t`No repair packet diagnosis is saved for this Work. Its saved investigation requests and evidence are shown below; they do not establish a cause or authorize physical work.`
              : t`This work order has no repair packet, so no diagnosis, cited evidence or findings are saved for it. This does not establish a cause or mean that evidence was checked.`}
          </Text>
          {!hasRequests && <ContinueInvestigation workOrder={workOrder} />}
        </Section>
        {noRequests}
        {nativeFindings}
      </Stack>
    );
  }

  const { investigation, qualification, evidence, disputed } = reading;
  const explanation = investigation.explanation;
  const intervention = investigation.intervention;
  const planParams = new URLSearchParams(params);
  planParams.set('work_panel', 'plan');

  return (
    <Stack gap='md'>
      <Section
        title={t`Current explanation`}
        icon={<IconStethoscope size={18} />}
        testId='diagnose-explanation'
      >
        <Group gap='xs'>
          {qualification === 'verified' ? (
            <Badge variant='light' color='teal' styles={WRAP_BADGE}>
              {t`Verified by ${explanation?.verified_by ?? t`a person`}`}
            </Badge>
          ) : qualification === 'unattributed' ? (
            <Badge variant='light' color='gray' styles={WRAP_BADGE}>
              {unattributedVerificationLabel()}
            </Badge>
          ) : qualification === 'preliminary' ? (
            <Badge variant='light' color='yellow' styles={WRAP_BADGE}>
              {t`Preliminary — not technician verified`}
            </Badge>
          ) : (
            <Badge variant='light' color='gray'>
              {t`No explanation saved`}
            </Badge>
          )}
          {qualification === 'verified' && explanation?.verified_at && (
            <Text size='xs'>{when(explanation.verified_at)}</Text>
          )}
          <Anchor
            component={Link}
            to={`/repair/packets/${investigation.packet!.id}/`}
            size='sm'
          >
            {investigation.packet!.reference}
          </Anchor>
        </Group>
        <Text size='md' fw={600} style={{ overflowWrap: 'anywhere' }}>
          {explanation?.likely_cause ||
            t`No explanation has been saved for this investigation.`}
        </Text>
        {qualification === 'preliminary' && (
          <Text size='sm'>
            {t`The initiating cause is not established. This restates what the cited evidence shows, with the gaps below, and authorizes nothing.`}
          </Text>
        )}
        {(disputed.length > 0 ||
          investigation.requests.items?.some((r) =>
            ['disputed', 'cannot_perform', 'pending_external'].includes(
              r.status
            )
          )) && (
          <Alert
            role='note'
            color='orange'
            title={t`Investigation decision unresolved`}
          >
            {t`The saved explanation retains its recorded verification. Conflicting or unavailable evidence still needs a decision on the affected request; this does not invalidate unrelated findings or authorize work.`}
          </Alert>
        )}
        {explanation && <ExplanationBasis explanation={explanation} />}
        {!hasRequests && <ContinueInvestigation workOrder={workOrder} />}
        {nativePacketLink}
      </Section>

      <Section
        title={t`Next check`}
        icon={<IconListCheck size={18} />}
        testId='diagnose-next-check'
      >
        {noRequests}
        <Text size='sm' fw={600}>
          {t`Checks proposed by the diagnosis`}
        </Text>
        {explanation?.proposed_checks.length ? (
          <>
            <List type='ordered' size='sm' spacing={4}>
              {explanation.proposed_checks.map((check, index) => (
                <List.Item key={`${index}-${check}`}>{check}</List.Item>
              ))}
            </List>
            <Text size='xs'>
              {t`Proposed by the diagnosis, not assigned, scheduled or confirmed. The order is the diagnosis's, not a priority decision.`}
            </Text>
          </>
        ) : (
          <Text size='sm'>{t`The diagnosis proposes no checks.`}</Text>
        )}
      </Section>

      <Section
        title={t`Evidence and contradictions`}
        icon={<IconScale size={18} />}
        testId='diagnose-evidence'
      >
        <SimpleGrid cols={{ base: 1, md: 3 }} spacing='md'>
          <EvidenceGroup relation='supports' items={evidence.supports} />
          <EvidenceGroup relation='contradicts' items={evidence.contradicts} />
          <EvidenceGroup relation='unknown' items={evidence.unknown} />
        </SimpleGrid>
        {disputed.length > 0 && (
          <Alert
            role='note'
            color='orange'
            variant='light'
            icon={<IconAlertTriangle size={16} />}
            title={t`${disputed.length} disputed finding(s), unresolved`}
          >
            <Stack gap={4}>
              {disputed.map((finding) => (
                <Text key={finding.id} size='sm'>
                  <Text span fw={600}>
                    {finding.finding_key}
                  </Text>
                  {` · ${finding.observation}`}
                  {finding.recorded_by
                    ? ` · ${t`recorded by ${finding.recorded_by}`}`
                    : ''}
                </Text>
              ))}
              <Text size='xs'>
                {t`A disputed finding stays visible beside the explanation. Discuss it in the assistant; chat does not save its correction or resolution. Use the native repair packet to record supported changes. It does not block unrelated work.`}
              </Text>
            </Stack>
          </Alert>
        )}
        {explanation?.alternatives.length ? (
          <Stack gap={4}>
            <Text size='sm' fw={600}>
              {t`Alternatives considered`}
            </Text>
            <List size='sm' spacing={2}>
              {explanation.alternatives.map((alternative, index) => (
                <List.Item key={`${index}-${alternative}`}>
                  {alternative}
                </List.Item>
              ))}
            </List>
          </Stack>
        ) : null}
      </Section>

      <Section
        title={t`Corrections`}
        icon={<IconPencil size={18} />}
        testId='diagnose-corrections'
      >
        {explanation?.amendments.length ? (
          <Stack gap={6}>
            {explanation.amendments.map((amendment, index) => (
              <Stack key={`${index}-${amendment.text}`} gap={0}>
                <Text size='sm'>{amendment.text}</Text>
                <Text size='xs'>
                  {[
                    amendment.author
                      ? t`by ${amendment.author}`
                      : t`author not recorded`,
                    when(amendment.recorded_at),
                    amendment.reason
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </Text>
              </Stack>
            ))}
          </Stack>
        ) : (
          <Text size='sm'>
            {t`No technician correction is recorded on the diagnosis.`}
          </Text>
        )}
        {investigation.finding_history === 'preserved_revisions' && (
          <Stack gap='sm' data-testid='finding-revisions'>
            <Text size='sm'>{t`Original observations are retained when corrected. Values overwritten before history was introduced cannot be reconstructed. A correction does not invalidate unrelated findings.`}</Text>
            {(investigation.finding_revisions ?? []).map((revision) => {
              const original = revision.snapshot;
              const current = investigation.findings.find(
                (f) => f.id === revision.finding_id
              );
              return (
                <Stack
                  key={revision.id}
                  gap={4}
                  p='sm'
                  style={{
                    borderLeft: '3px solid var(--mantine-color-orange-filled)',
                    overflowWrap: 'anywhere'
                  }}
                >
                  <Text size='sm' fw={600}>
                    {t`Original finding`} · {original.finding_key} ·{' '}
                    {t`Revision ${revision.id}`}
                  </Text>
                  <Text size='sm'>
                    {original.observation} ·{' '}
                    {measured(original.value, original.unit) ||
                      t`Value not recorded`}{' '}
                    · {original.verification}
                  </Text>
                  <Text size='sm'>
                    {original.recorded_by || t`Recorder not recorded`} ·{' '}
                    {when(original.observed_at) ||
                      t`Observation time not recorded`}{' '}
                    · {original.evidence_source || t`Source not recorded`}
                    {original.snapshot_id
                      ? ` · ${t`Snapshot ${original.snapshot_id}`}`
                      : ''}
                  </Text>
                  <Text size='sm'>
                    {t`Replaced by`}: {revision.replaced_by || t`Not recorded`}{' '}
                    · {when(revision.replaced_at)}
                  </Text>
                  {current && (
                    <>
                      <Text size='sm' fw={600}>
                        {t`Current finding`} · {current.verification}
                      </Text>
                      <Text size='sm'>
                        {current.observation} ·{' '}
                        {measured(current.value, current.unit) ||
                          t`Value not recorded`}
                      </Text>
                      <Text size='sm'>
                        {current.recorded_by || t`Recorder not recorded`} ·{' '}
                        {when(current.updated_at)}
                      </Text>
                    </>
                  )}
                </Stack>
              );
            })}
          </Stack>
        )}
        {investigation.finding_history === 'overwritten_in_place' && (
          <Text size='xs'>
            {t`This server updates a finding in place: when it is corrected, its earlier value and verification are not kept.`}
          </Text>
        )}
      </Section>

      <Section
        title={t`Intervention basis`}
        icon={<IconTool size={18} />}
        testId='diagnose-intervention'
      >
        {intervention ? (
          <>
            <Group gap='xs'>
              <Badge variant='light'>
                {t`Approved scope version ${intervention.version}`}
              </Badge>
              {intervention.superseded_versions > 0 && (
                <Text size='xs'>
                  {t`${intervention.superseded_versions} earlier version(s) retained`}
                </Text>
              )}
            </Group>
            <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
              {intervention.verified_cause ||
                t`No verified cause is recorded on the approved scope.`}
            </Text>
            <Text size='xs'>
              {[
                t`${intervention.scope_line_count} scope step(s)`,
                intervention.failure_codes.join(', ') || null,
                intervention.approved_by
                  ? t`approved by ${intervention.approved_by}`
                  : t`approver not recorded`,
                when(intervention.approved_at)
              ]
                .filter(Boolean)
                .join(' · ')}
            </Text>
            <Anchor component={Link} replace to={`?${planParams}`} size='sm'>
              {t`Open Plan & Safety`}
            </Anchor>
          </>
        ) : (
          <Text size='sm'>
            {t`No approved intervention scope is recorded. Nothing here authorizes physical work.`}
          </Text>
        )}
      </Section>

      {nativeFindings}
    </Stack>
  );
}

export default DiagnoseProjection;
