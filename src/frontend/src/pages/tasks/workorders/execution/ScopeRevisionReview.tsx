import type {
  PackageScope,
  PreparedPackage,
  ScopeRevisionReview as RevisionReview
} from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Group,
  List,
  Paper,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { workPanelParams } from '../workPanelState';
import {
  ExecutionFact,
  executionBadgeStyles,
  recordedTime
} from './ExecutionFacts';
import {
  investigationKindLabel,
  investigationStatusLabel
} from './InvestigationRequests';
import { compareScopeText, effectiveScope } from './scopeRevision';

function impactLabel(kind: string) {
  return (
    (
      {
        tasks: t`Execution tasks`,
        materials: t`Materials, fit and reservations`,
        resources: t`Resources and competence`,
        permissions: t`Permissions and restrictions`,
        criteria: t`Verification criteria`
      } as Record<string, string>
    )[kind] ?? kind
  );
}

/** A notice follows the saved revision into each affected Work surface. */
export function ScopeRevisionNotice({
  workOrder,
  full = false
}: Readonly<{ workOrder: WorkOrderOverview; full?: boolean }>) {
  const [params] = useSearchParams();
  const packageValue = workOrder.prepared_package;
  const review = packageValue?.revision_review;
  if (
    (!review && (packageValue?.scopes.length ?? 0) < 2) ||
    (review?.rule === 'scope_revision.v1' &&
      ['no_scope', 'original'].includes(review.state) &&
      review.request_revisions.length === 0)
  )
    return null;
  const current = effectiveScope(
    packageValue?.scopes ?? [],
    review?.current_scope_id ?? null
  );
  if (!full)
    return (
      <Paper withBorder p='xs' role='note' data-testid='scope-revision-notice'>
        <Group gap='xs'>
          <Text size='sm' fw={600}>
            {current
              ? t`Scope version ${current.version}`
              : t`Scope needs review`}
          </Text>
          <Anchor
            component={Link}
            replace
            size='sm'
            to={`?${workPanelParams(params, 'plan')}#scope-revisions`}
          >{t`Review in Plan & Safety`}</Anchor>
        </Group>
      </Paper>
    );
  return (
    <Alert
      role='note'
      color='orange'
      title={
        current
          ? review?.selective_reassessment?.items.some(
              (row) => row.scope_id === review.current_scope_id
            )
            ? t`Scope version ${current.version} · review bound dependencies`
            : t`Scope version ${current.version} · dependency impact not recorded`
          : review
            ? t`Effective scope needs review`
            : t`Scope revision impact not supplied`
      }
      data-testid='scope-revision-notice'
    >
      <Stack gap={6}>
        {full && (
          <Text size='sm'>
            {review?.selective_reassessment?.items.some(
              (row) => row.scope_id === review.current_scope_id
            )
              ? t`Recorded component and procedure bindings identify mapped dependencies. Evidence stays with its original binding; reassessment and unavailable evidence are shown below. Obsolete scope is not executable. Changed physical work needs its own applicable authority.`
              : t`Saved scope records and request revisions remain available for review. The record does not identify which tasks, materials, resources, permissions or tests are affected. Changed physical work needs its own applicable authority; an earlier approval cannot cover it automatically.`}
          </Text>
        )}
        <Anchor
          component={Link}
          replace
          size='sm'
          to={`?${workPanelParams(params, 'plan')}#scope-revisions`}
        >{t`Review scope changes and retained evidence`}</Anchor>
      </Stack>
    </Alert>
  );
}

function ScopeFacts({
  scope,
  label
}: Readonly<{ scope: PackageScope; label: string }>) {
  return (
    <Paper withBorder p='sm'>
      <Stack gap='xs'>
        <Group gap='xs'>
          <Text size='sm' fw={600}>
            {label} · {t`Scope version ${scope.version}`}
          </Text>
          <Badge color='gray' styles={executionBadgeStyles}>
            {scope.current ? t`Current record` : t`Superseded · reference only`}
          </Badge>
        </Group>
        <Text size='sm'>{scope.verified_cause || t`Cause not recorded`}</Text>
        {scope.failure_codes && scope.failure_codes.length > 0 && (
          <ExecutionFact label={t`Recorded failure codes`}>
            {scope.failure_codes.join(', ')}
          </ExecutionFact>
        )}
        {scope.lines.length ? (
          <List type='ordered' size='sm'>
            {scope.lines.map((line) => (
              <List.Item key={line.sequence}>{line.action}</List.Item>
            ))}
          </List>
        ) : (
          <Text size='sm'>{t`Scope lines not recorded`}</Text>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='xs'>
          <ExecutionFact label={t`Approved by`}>
            {scope.approved_by}
          </ExecutionFact>
          <ExecutionFact label={t`Approved at`}>
            {recordedTime(scope.approved_at)}
          </ExecutionFact>
          <ExecutionFact label={t`Planned crew`}>
            {scope.crew_size}
          </ExecutionFact>
          <ExecutionFact label={t`Planned elapsed (min)`}>
            {scope.planned_elapsed_minutes}
          </ExecutionFact>
        </SimpleGrid>
        <ExecutionFact label={t`Saved approval note`}>
          {scope.note}
        </ExecutionFact>
        {!scope.current && (
          <Text fw={600} size='sm'>{t`Obsolete scope · not executable`}</Text>
        )}
        {!scope.current && (
          <ExecutionFact label={t`Superseded at`}>
            {recordedTime(scope.superseded_at)}
          </ExecutionFact>
        )}
      </Stack>
    </Paper>
  );
}

function Comparison({
  before,
  after
}: Readonly<{ before: PackageScope; after: PackageScope }>) {
  const changes = compareScopeText(before.lines, after.lines);
  const states = {
    added: t`Added text`,
    removed: t`Removed text`,
    changed: t`Revised text`,
    same: t`Same text`
  };
  return (
    <Stack gap='sm'>
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing='sm'>
        <ScopeFacts
          scope={before}
          label={t`Before · previous retained version`}
        />
        <ScopeFacts scope={after} label={t`After · next retained version`} />
      </SimpleGrid>
      <Text size='sm'>{t`Compared in native version order. Approval notes are retained; a linked revision reason and supporting evidence are not recorded. Text at the same line position does not identify an execution task or establish unchanged readiness.`}</Text>
      {changes.map((change) => (
        <Stack key={change.sequence} gap={3}>
          <Badge
            color={change.state === 'same' ? 'gray' : 'orange'}
            styles={executionBadgeStyles}
            style={{ alignSelf: 'flex-start' }}
          >
            {states[change.state]}
          </Badge>
          <Text size='sm'>
            {t`Line ${change.sequence}`}:{' '}
            {change.before ?? t`No line in this version`} →{' '}
            {change.after ?? t`No line in this version`}
          </Text>
        </Stack>
      ))}
    </Stack>
  );
}

function RequestSnapshot({
  value,
  label
}: Readonly<{
  value: RevisionReview['request_revisions'][number]['before'];
  label: string;
}>) {
  return (
    <Paper withBorder p='sm'>
      <Stack gap='xs'>
        <Text size='sm' fw={600}>
          {label} · v{value.version}
        </Text>
        <ExecutionFact label={t`Decision purpose`}>
          {value.purpose}
        </ExecutionFact>
        <ExecutionFact label={t`Next check`}>{value.next_check}</ExecutionFact>
        <ExecutionFact label={t`Saved request status`}>
          {investigationStatusLabel(value.status)}
        </ExecutionFact>
        <ExecutionFact label={t`Recorded owner`}>
          {value.owner_name}
        </ExecutionFact>
        <ExecutionFact label={t`Next checkpoint`}>
          {recordedTime(value.next_checkpoint)}
        </ExecutionFact>
        <ExecutionFact label={t`Recorded constraint`}>
          {value.blocked_reason}
        </ExecutionFact>
        {value.source_finding != null && (
          <ExecutionFact label={t`Linked source finding`}>
            {value.source_finding}
          </ExecutionFact>
        )}
        {value.source_proposal && (
          <ExecutionFact label={t`Saved source proposal`}>
            {value.source_proposal.check} ·{' '}
            {recordedTime(value.source_proposal.generated_at)}
          </ExecutionFact>
        )}
        {value.resolution && (
          <ExecutionFact label={t`Recorded resolution`}>
            {value.resolution}
          </ExecutionFact>
        )}
        {value.priority != null && (
          <ExecutionFact label={t`Recorded priority`}>
            {value.priority}
          </ExecutionFact>
        )}
        <Text size='sm' fw={500}>{t`Required artifacts`}</Text>
        {value.required_artifacts?.length ? (
          <List size='sm'>
            {value.required_artifacts.map((item, index) => (
              <List.Item key={index}>
                {investigationKindLabel(item.kind)} · {item.description} ·{' '}
                {t`Unit`}: {item.unit || t`Not recorded`} · {t`Conditions`}:{' '}
                {item.conditions || t`Not recorded`}
              </List.Item>
            ))}
          </List>
        ) : (
          <Text size='sm'>{t`Not recorded`}</Text>
        )}
      </Stack>
    </Paper>
  );
}

/** Existing immutable versions and request audit snapshots; no new approval step. */
export function ScopeRevisionReview({
  value
}: Readonly<{ value: PreparedPackage }>) {
  const [params] = useSearchParams();
  const review = value.revision_review;
  const supported = review?.rule === 'scope_revision.v1';
  const current = supported
    ? effectiveScope(value.scopes, review.current_scope_id)
    : null;
  const comparisons = supported
    ? review.comparisons.flatMap((pair) => {
        const before = value.scopes.find(
          (scope) => scope.id === pair.before_scope_id
        );
        const after = value.scopes.find(
          (scope) => scope.id === pair.after_scope_id
        );
        return before && after ? [{ before, after }] : [];
      })
    : [];
  const requestLink = (id: number) => {
    const next = workPanelParams(params, 'diagnose');
    next.set('investigation_request', String(id));
    return `?${next}#investigation-request-${id}`;
  };
  return (
    <Stack gap='sm' id='scope-revisions' data-testid='scope-revision-review'>
      <Alert
        role='note'
        color='orange'
        title={t`Affected readiness is not established`}
      >
        {review?.selective_reassessment?.items.some(
          (row) => row.scope_id === review.current_scope_id
        )
          ? t`Review the recorded component and procedure bindings below. This projection does not grant physical authority or replace execution permission checks.`
          : t`No record links a scope revision to affected dependencies or evidence validity. Review changed work through its owning process; prior approvals keep their original boundary.`}
      </Alert>
      {review?.selective_reassessment?.unmapped_changes && (
        <Alert
          role='note'
          color='orange'
          data-testid='unmapped-scope-change'
        >{t`An unbound scope line changed. Its applicability to tasks, materials, tests and permissions is unknown; recorded bindings do not identify the full impact.`}</Alert>
      )}
      {review?.selective_reassessment && (
        <Text size='sm'>
          {t`Changed components`}:{' '}
          {review.selective_reassessment.changed_component_refs.join(', ') ||
            t`Not recorded`}{' '}
          · {t`Changed procedure revisions`}:{' '}
          {review.selective_reassessment.changed_procedure_revision_ids.join(
            ', '
          ) || t`Not recorded`}
        </Text>
      )}
      <Text size='sm' fw={600}>
        {current
          ? t`Latest current scope: version ${current.version}`
          : supported && review.state === 'no_scope'
            ? t`No approved scope recorded`
            : t`Effective scope not established by this projection`}
      </Text>
      {review?.selective_reassessment &&
        review.selective_reassessment.items.length > 0 && (
          <Stack gap='xs' data-testid='selective-reassessment'>
            <Text
              fw={600}
              size='sm'
            >{t`Component and procedure reassessment`}</Text>
            <Text size='sm'>{t`Only dependencies bound to a revised component or procedure need reassessment. Other bound dependencies retain only their recorded native evidence. Missing or unvalidated evidence stays unknown. Superseded scope is not executable; these mappings do not grant physical permission.`}</Text>
            <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }} spacing='xs'>
              {review.selective_reassessment.items.map((row, index) => (
                <Paper
                  withBorder
                  p='xs'
                  key={`${row.scope_id}:${row.line_sequence ?? index}:${row.binding_index ?? index}:${row.kind}:${row.id}`}
                >
                  <Stack gap={4}>
                    <Text size='sm' fw={600}>
                      {impactLabel(row.kind)} · {row.id} · {t`Scope`}:{' '}
                      {row.scope_id}
                    </Text>
                    <Text size='sm'>
                      {row.state === 'reassess'
                        ? t`Reassessment required · not executable`
                        : row.state === 'retained'
                          ? t`Evidence retained for this binding`
                          : row.state === 'effective_unknown'
                            ? t`Effective scope unknown · not executable`
                            : row.state === 'evidence_unknown'
                              ? t`Evidence not established · not executable`
                              : row.state === 'obsolete'
                                ? t`Obsolete scope · not executable`
                                : t`Applicability not mapped · not executable`}
                    </Text>
                    <Text size='sm'>
                      {t`Retained evidence IDs`}:{' '}
                      {row.retained_evidence_ids.length
                        ? row.retained_evidence_ids.join(', ')
                        : t`Not recorded`}
                    </Text>
                  </Stack>
                </Paper>
              ))}
            </SimpleGrid>
          </Stack>
        )}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing='xs'>
        {['tasks', 'materials', 'resources', 'permissions', 'criteria'].map(
          (kind) => (
            <Paper key={kind} withBorder p='xs'>
              <ExecutionFact label={impactLabel(kind)}>
                {review?.selective_reassessment?.items.some(
                  (row) =>
                    row.kind === kind &&
                    row.scope_id === review.current_scope_id
                )
                  ? t`Recorded bindings shown above; prior evidence stays with its binding.`
                  : t`Affected records not recorded`}
              </ExecutionFact>
            </Paper>
          )
        )}
      </SimpleGrid>
      {comparisons.length > 0 && (
        <Accordion
          variant='separated'
          defaultValue={String(comparisons[0].after.id)}
          keepMounted={false}
        >
          {comparisons.map(({ before, after }) => (
            <Accordion.Item value={String(after.id)} key={after.id}>
              <Accordion.Control>{t`Scope ${before.version} → ${after.version}`}</Accordion.Control>
              <Accordion.Panel>
                <Comparison before={before} after={after} />
              </Accordion.Panel>
            </Accordion.Item>
          ))}
        </Accordion>
      )}
      {!comparisons.length && (
        <Text size='sm'>
          {supported
            ? t`No before/after scope pair is retained.`
            : t`Revision comparisons are not supplied by this server. Saved versions remain below.`}
        </Text>
      )}
      <Accordion variant='contained' keepMounted={false}>
        <Accordion.Item value='versions'>
          <Accordion.Control>{t`All retained scope versions (${value.scopes.length})`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              {value.scopes.map((scope) => (
                <ScopeFacts
                  key={scope.id}
                  scope={scope}
                  label={t`Saved scope`}
                />
              ))}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
        <Accordion.Item value='requests'>
          <Accordion.Control>{t`Recorded investigation request revisions (${supported ? review.request_revisions.length : t`Not supplied`})`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='md'>
              {supported && review.request_revisions.length === 0 && (
                <Text size='sm'>{t`No saved request-revision events returned. This does not establish that the diagnosis or scope is unchanged.`}</Text>
              )}
              {supported &&
                review.request_revisions.map((revision) => (
                  <Stack key={revision.event_id} gap='xs'>
                    <Text size='sm' fw={600}>
                      {t`Request ${revision.request_id}`} ·{' '}
                      {revision.recorded_by ?? t`Not recorded`} ·{' '}
                      {recordedTime(revision.recorded_at)}
                    </Text>
                    <SimpleGrid cols={{ base: 1, md: 2 }}>
                      <RequestSnapshot
                        value={revision.before}
                        label={t`Before`}
                      />
                      <RequestSnapshot
                        value={revision.after}
                        label={t`After`}
                      />
                    </SimpleGrid>
                    <Text size='sm'>
                      {t`Retained prior evidence IDs`}:{' '}
                      {revision.retained_evidence_ids.length
                        ? revision.retained_evidence_ids.join(', ')
                        : t`No evidence IDs in this saved snapshot`}
                    </Text>
                    <Text size='sm'>{t`Scope relationship and authored revision reason: Not recorded. The saved request change is not proof of a new approved scope or permission.`}</Text>
                    <Anchor
                      component={Link}
                      replace
                      to={requestLink(revision.request_id)}
                      size='sm'
                    >{t`Open saved request ${revision.request_id} and evidence`}</Anchor>
                  </Stack>
                ))}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
      <Text size='sm'>{t`Original observations, corrections, material movements and verification attempts remain in their native records. Their validity for changed scope is not inferred.`}</Text>
      <Group gap='md'>
        <Anchor
          component={Link}
          replace
          size='sm'
          to={`?${workPanelParams(params, 'diagnose')}`}
        >{t`Open investigation and retained evidence`}</Anchor>
        <Anchor
          component={Link}
          replace
          size='sm'
          to={`?${workPanelParams(params, 'parts')}`}
        >{t`Review job materials`}</Anchor>
        <Anchor
          component={Link}
          replace
          size='sm'
          to={`?${workPanelParams(params, 'verify')}`}
        >{t`Review saved verification criteria and attempts`}</Anchor>
      </Group>
    </Stack>
  );
}
