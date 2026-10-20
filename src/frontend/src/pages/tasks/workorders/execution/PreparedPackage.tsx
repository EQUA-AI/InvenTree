import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  List,
  SimpleGrid,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { getDetailUrl } from '@lib/functions/Navigation';
import type {
  PreparedPackage as Package,
  PackageApplication,
  PackageScope,
  PackageStep
} from '@lib/types/WorkExecution';
import { useApi } from '../../../../contexts/ApiContext';
import { extractErrorMessage } from '../../../../functions/api';
import { useUserState } from '../../../../states/UserState';
import { useActionFocus } from '../useActionFocus';
import { workPanelParams } from '../workPanelState';
import {
  ExecutionFact,
  ExecutionSection,
  executionBadgeStyles,
  recordedTime
} from './ExecutionFacts';
import { ScopeRevisionReview } from './ScopeRevisionReview';
import { deviationFacts } from './packagePresentation';
import { effectiveScope } from './scopeRevision';

/** A criterion is a saved instruction, never a threshold supplied by the UI. */
export function StepCriteria({ step }: Readonly<{ step: PackageStep }>) {
  return (
    <Stack gap={2}>
      <Text size='sm'>{step.instruction || t`No instruction recorded`}</Text>
      <Text size='xs' c='dimmed'>
        {t`Unit`}: {step.unit || t`Not recorded`} · {t`Minimum`}:{' '}
        {step.min_value ?? t`Not recorded`} · {t`Maximum`}:{' '}
        {step.max_value ?? t`Not recorded`}
      </Text>
      {step.allowed_values.length > 0 && (
        <Text size='sm'>
          {t`Allowed values`}: {step.allowed_values.join(', ')}
        </Text>
      )}
      {step.required_permission && (
        <Text size='xs'>
          {t`Required permission`}: {step.required_permission}
        </Text>
      )}
    </Stack>
  );
}

function Scope({ scope }: Readonly<{ scope: PackageScope }>) {
  return (
    <Stack gap='xs'>
      <Text size='sm'>{scope.verified_cause || t`Cause not recorded`}</Text>
      <List type='ordered' size='sm'>
        {scope.lines.map((line) => (
          <List.Item key={line.sequence}>{line.action}</List.Item>
        ))}
      </List>
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <ExecutionFact label={t`Scope approved by`}>
          {scope.approved_by}
        </ExecutionFact>
        <ExecutionFact label={t`Approved at`}>
          {recordedTime(scope.approved_at)}
        </ExecutionFact>
        <ExecutionFact
          label={t`Planned crew / elapsed`}
        >{`${scope.crew_size ?? t`Not recorded`} / ${scope.planned_elapsed_minutes ?? t`Not recorded`} ${t`min`}`}</ExecutionFact>
      </SimpleGrid>
      {scope.note && <Text size='sm'>{scope.note}</Text>}
    </Stack>
  );
}

function Application({
  application
}: Readonly<{ application: PackageApplication }>) {
  const user = useUserState();
  const linkParts = user.hasViewRole(UserRoles.part);
  const drift =
    application.drift_basis === 'catalogue_at_read'
      ? application.drift_status
      : 'not_evaluated';
  const driftLabel =
    drift === 'current'
      ? t`Applied revision matches the current published catalogue revision.`
      : drift === 'different_revision'
        ? t`A different revision is current in the catalogue.`
        : drift === 'revision_not_published'
          ? t`Applied revision is not currently published.`
          : drift === 'procedure_inactive'
            ? t`The linked procedure is inactive in the catalogue.`
            : t`Drift not evaluated`;
  return (
    <Stack gap='sm'>
      <Group gap='xs'>
        <Text
          fw={600}
        >{t`Applied procedure · revision ${application.revision ?? t`Not recorded`}`}</Text>
        <Badge styles={executionBadgeStyles} color='gray'>
          {application.primary
            ? t`Primary application`
            : t`Additional application`}
        </Badge>
      </Group>
      <Text size='sm'>
        {t`Current catalogue name`}:{' '}
        {application.current_catalogue_code || t`Code not recorded`} ·{' '}
        {application.current_catalogue_name || t`Name not recorded`}
      </Text>
      <Text size='sm'>
        {t`Applied by`} {application.applied_by ?? t`Not recorded`} ·{' '}
        {recordedTime(application.applied_at)}
      </Text>
      <ExecutionFact label={t`Catalogue comparison at this read`}>
        {driftLabel}
      </ExecutionFact>
      {drift !== 'current' && (
        <Alert color='orange'>{t`Review the catalogue difference or missing comparison through the native flow. The original applied instructions are retained; this comparison does not establish readiness or authority.`}</Alert>
      )}
      {application.change_summary && (
        <Text size='sm'>{application.change_summary}</Text>
      )}
      <Accordion variant='separated' multiple>
        <Accordion.Item value='steps'>
          <Accordion.Control>{t`Instructions and sourced criteria (${application.steps.length})`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='md'>
              {application.steps.map((step) => (
                <Stack key={step.key} gap={4}>
                  <Text fw={600} size='sm'>
                    {step.sequence}. {step.title} ·{' '}
                    {step.required ? t`Required` : t`Optional`}
                  </Text>
                  <StepCriteria step={step} />
                </Stack>
              ))}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
        <Accordion.Item value='resources'>
          <Accordion.Control>{t`Resource requirements (${application.resources.length})`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              {application.resources.map((resource) => (
                <Stack key={resource.key} gap={2}>
                  <Text size='sm' fw={600}>
                    {resource.kind} · {resource.quantity}
                  </Text>
                  {resource.part_name && linkParts ? (
                    <Anchor
                      component={Link}
                      size='sm'
                      to={getDetailUrl(ModelType.part, resource.part_id)}
                    >
                      {resource.part_name}
                    </Anchor>
                  ) : (
                    <Text size='sm'>
                      {resource.part_name ?? t`Part name unavailable`}
                    </Text>
                  )}
                  <Text size='xs' c='dimmed'>
                    {t`Current catalogue name; requirement retained from the applied snapshot`}
                  </Text>
                  <Text size='xs' c='dimmed'>
                    {t`Quantity unit not recorded in this snapshot`} ·{' '}
                    {resource.fulfillment_mode === 'checkout_return'
                      ? t`Checkout and return`
                      : resource.fulfillment_mode === 'reserve_consume'
                        ? t`Reserve and consume`
                        : resource.fulfillment_mode === 'verify_only'
                          ? t`Verify only`
                          : t`Fulfillment not recorded`}
                  </Text>
                  {resource.notes && <Text size='sm'>{resource.notes}</Text>}
                </Stack>
              ))}
            </Stack>
            <Text
              size='sm'
              mt='sm'
            >{t`Requirements do not establish availability, issue, competence or readiness.`}</Text>
          </Accordion.Panel>
        </Accordion.Item>
        <Accordion.Item value='source'>
          <Accordion.Control>{t`Applied source record`}</Accordion.Control>
          <Accordion.Panel>
            <ExecutionFact
              label={t`Application / revision record`}
            >{`${application.id} / ${application.revision_id ?? t`Not recorded`}`}</ExecutionFact>
            <ExecutionFact label={t`Immutable snapshot hash`}>
              {application.snapshot_hash}
            </ExecutionFact>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </Stack>
  );
}

function DeviationValues({
  label,
  value
}: Readonly<{ label: string; value: unknown }>) {
  const facts = deviationFacts(value);
  return (
    <Stack gap={2}>
      <Text size='xs' c='dimmed'>
        {label}
      </Text>
      {facts.length === 0 ? (
        <Text size='sm'>{t`Not recorded`}</Text>
      ) : (
        facts.map((fact) => (
          <Text key={fact.field} size='sm' style={{ overflowWrap: 'anywhere' }}>
            {fact.field && (
              <Text span fw={500}>
                {fact.field}:{' '}
              </Text>
            )}
            {fact.value === null
              ? t`Not recorded`
              : fact.value === true
                ? t`Yes`
                : fact.value === false
                  ? t`No`
                  : String(fact.value)}
          </Text>
        ))
      )}
    </Stack>
  );
}

function DeviationEntry({ workOrderId }: Readonly<{ workOrderId: number }>) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<
    'saved' | 'uncertain' | 'refused' | null
  >(null);
  const [refusal, setRefusal] = useState('');
  const resultFocus = useActionFocus(busy);
  async function save() {
    if (busy || !reason.trim()) return;
    setBusy(true);
    setOutcome(null);
    setRefusal('');
    try {
      const response = await api.post(
        `/api/tasks/work-orders/${workOrderId}/deviations/`,
        { category: 'changed_condition', reason: reason.trim() }
      );
      // A lost/malformed response is not proof of failure or permission to
      // blindly retry this native non-idempotent endpoint.
      if (response.status !== 201 || !Number.isSafeInteger(response.data?.id))
        throw new Error('Unconfirmed save');
      setOutcome('saved');
      setReason('');
      await queryClient.invalidateQueries({
        queryKey: ['work-order', workOrderId]
      });
    } catch (error) {
      const status = (error as { response?: { status?: number } } | null)
        ?.response?.status;
      if (
        status &&
        status >= 400 &&
        status < 500 &&
        ![408, 429].includes(status)
      ) {
        const message: unknown = extractErrorMessage({
          error,
          field: 'reason'
        });
        setRefusal(
          typeof message === 'string'
            ? message
            : Array.isArray(message) &&
                message.every((part) => typeof part === 'string')
              ? message.join('; ')
              : t`The native service refused this attempt.`
        );
        setOutcome('refused');
      } else {
        setOutcome('uncertain');
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <Accordion variant='contained'>
      <Accordion.Item value='deviation'>
        <Accordion.Control>{t`Record a changed condition`}</Accordion.Control>
        <Accordion.Panel>
          <Stack
            {...resultFocus.props}
            aria-label={t`Changed condition record`}
            gap='sm'
          >
            <Text size='sm'>{t`Native manual recovery records a deviation. It does not approve a revised scope or release a restriction.`}</Text>
            <Textarea
              label={t`Changed condition and reason`}
              value={reason}
              onChange={(event) => {
                setReason(event.currentTarget.value);
              }}
              readOnly={busy || outcome === 'uncertain'}
              autosize
              minRows={3}
            />
            {outcome === 'saved' && (
              <Alert color='blue'>{t`Deviation saved to this work order.`}</Alert>
            )}
            {outcome === 'uncertain' && (
              <Alert color='orange'>{t`Save not confirmed. Your text is retained. Check the latest native deviations before submitting again; this request may have saved.`}</Alert>
            )}
            {outcome === 'refused' && (
              <Alert color='red'>
                <Text size='sm'>{t`Save refused. No deviation was saved by this attempt. Your text is retained.`}</Text>
                <Text size='sm'>{refusal}</Text>
              </Alert>
            )}
            <Button
              style={{ alignSelf: 'flex-start' }}
              onClick={() => resultFocus.run(save)}
              loading={busy}
              disabled={!reason.trim() || outcome === 'uncertain'}
            >{t`Save deviation`}</Button>
          </Stack>
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  );
}

export function PreparedPackage({
  value,
  workOrderId
}: Readonly<{ value: Package | undefined; workOrderId: number }>) {
  const [params] = useSearchParams();
  if (value?.rule !== 'prepared_package.v1')
    return (
      <Alert
        color='gray'
        title={t`Prepared package not supplied`}
      >{t`This server does not supply the applied procedure, resources or revision history here. Use the linked native records. Missing information does not establish readiness.`}</Alert>
    );
  const current = value.scopes.filter((scope) => scope.current);
  const effective = effectiveScope(
    value.scopes,
    value.revision_review
      ? value.revision_review.current_scope_id
      : current.length === 1
        ? current[0].id
        : null
  );
  return (
    <Stack gap='md' data-testid='prepared-package'>
      <ExecutionSection title={t`Prepared work package`}>
        {effective ? (
          <>
            <Badge
              styles={executionBadgeStyles}
              style={{ alignSelf: 'flex-start' }}
            >{t`Current scope version ${effective.version}`}</Badge>
            <Scope scope={effective} />
          </>
        ) : (
          <Text size='sm'>
            {current.length > 0 ||
            value.revision_review?.state === 'conflicting_scope'
              ? t`Current scope markers do not identify a single latest version. Effective scope is unresolved.`
              : t`No current approved repair scope recorded.`}
          </Text>
        )}
        <Text
          size='sm'
          c='dimmed'
        >{t`A saved scope or applied procedure is a plan record, not proof of physical application or permission to work.`}</Text>
        {value.procedures_state === 'not_enabled' ? (
          <Text size='sm'>{t`Governed procedure records are not enabled.`}</Text>
        ) : value.applications.length ? (
          value.applications.map((application) => (
            <Application key={application.id} application={application} />
          ))
        ) : (
          <Text size='sm'>{t`No applied procedure recorded. Instructions and acceptance criteria are not established here.`}</Text>
        )}
        <Anchor
          component={Link}
          to={`?${workPanelParams(params, 'parts')}`}
          size='sm'
        >{t`Open Parts for this job`}</Anchor>
      </ExecutionSection>
      <ExecutionSection title={t`Recorded physical authority`}>
        <Text size='sm'>{t`Not available — no physical-authority provider is connected. Applied isolation, valid job-specific permits, access, operations window and independent restrictions are not established by the package or its safety-gate status.`}</Text>
        <Text
          size='sm'
          c='dimmed'
        >{t`Authority source, effective time, responsible owner and resume conditions: Not recorded here.`}</Text>
      </ExecutionSection>
      <ExecutionSection title={t`Scope revisions and affected dependencies`}>
        <ScopeRevisionReview value={value} />
      </ExecutionSection>
      <ExecutionSection title={t`Native deviations`}>
        {value.procedures_state === 'not_enabled' ? (
          <Text size='sm'>{t`Deviation records are not enabled.`}</Text>
        ) : value.deviations.length === 0 ? (
          <Text size='sm'>{t`No native deviation records returned. This does not establish that conditions are unchanged.`}</Text>
        ) : (
          value.deviations.map((item) => (
            <Stack gap={4} key={item.id}>
              <Text fw={600} size='sm'>
                {item.reason}
              </Text>
              <Text size='xs' c='dimmed'>
                {item.recorded_by ?? t`Recorder not recorded`} ·{' '}
                {recordedTime(item.created_at)} · {item.category}
              </Text>
              <Text size='sm'>
                {item.resolved_at
                  ? `${item.resolution || t`Resolution not recorded`} · ${recordedTime(item.resolved_at)}`
                  : t`Unresolved — no resolution recorded`}
              </Text>
              {(item.expected !== null || item.actual !== null) && (
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <DeviationValues label={t`Expected`} value={item.expected} />
                  <DeviationValues label={t`Actual`} value={item.actual} />
                </SimpleGrid>
              )}
            </Stack>
          ))
        )}
        {value.can_record_deviation && (
          <DeviationEntry workOrderId={workOrderId} />
        )}
      </ExecutionSection>
    </Stack>
  );
}
