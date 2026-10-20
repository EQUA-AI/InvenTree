import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type {
  InvestigationArtifact,
  InvestigationRequest
} from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Collapse,
  Group,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { useApi } from '../../../../contexts/ApiContext';
import { useLocalState } from '../../../../states/LocalState';
import { useUserState } from '../../../../states/UserState';
import { useActionFocus } from '../useActionFocus';
import { recordedTime } from './ExecutionFacts';
import {
  type ObservationIntent,
  observationReceipt
} from './observationCapture';

interface WorkAttachment {
  pk: number;
  filename: string;
  model_type: string;
  model_id: number;
}

/** Secondary native manual recovery; chat remains the diagnosis exchange. */
export function NativeObservationCapture(
  props: Readonly<{
    workOrder: WorkOrderOverview;
    request: InvestigationRequest;
  }>
) {
  const canChange = useUserState((state) =>
    state.hasChangeRole(UserRoles.work_order)
  );
  const identity = useUserState((state) => state.authGeneration);
  const actor = useUserState((state) => state.user?.pk);
  const host = useLocalState((state) => state.hostKey);
  if (!canChange || props.request.work_order !== props.workOrder.id)
    return null;
  return (
    <ObservationDraft
      key={`${host}:${actor}:${identity}:${props.workOrder.id}:${props.request.id}`}
      {...props}
    />
  );
}

function ObservationDraft({
  workOrder: work,
  request
}: Readonly<{ workOrder: WorkOrderOverview; request: InvestigationRequest }>) {
  const api = useApi();
  const client = useQueryClient();
  const actor = useUserState((state) => state.user?.pk);
  const identity = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.hostKey);
  const [opened, setOpened] = useState(false);
  const id = useId();
  const [text, setText] = useState('');
  const [observedAt, setObservedAt] = useState('');
  const [conditions, setConditions] = useState('');
  const [attachment, setAttachment] = useState<string | null>(null);
  const [captureContext] = useState({
    machine: work.machine,
    machineName: work.machine_name,
    component: work.affected_component_ref || work.affected_component || '',
    componentName: [work.affected_component, work.affected_component_ref]
      .filter(Boolean)
      .join(' · ')
  });
  const [base, setBase] = useState({
    work: work.lifecycle_version,
    request: request.version
  });
  const [intent, setIntent] = useState<ObservationIntent | null>(null);
  const [receipt, setReceipt] = useState<InvestigationArtifact | null>(null);
  const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [needsReview, setNeedsReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const resultFocus = useActionFocus(busy);
  const contextChanged =
    captureContext.machine !== work.machine ||
    captureContext.component !==
      (work.affected_component_ref || work.affected_component || '');
  const changed =
    contextChanged ||
    base.work !== work.lifecycle_version ||
    base.request !== request.version;
  const files = useQuery({
    queryKey: [
      'investigation-work-attachments',
      host,
      actor,
      identity,
      work.id
    ],
    enabled: opened,
    retry: false,
    queryFn: async ({ signal }) => {
      const response = await api.get(apiUrl(ApiEndpoints.attachment_list), {
        signal,
        params: { model_type: 'workorder', model_id: work.id, limit: 100 }
      });
      if (!Array.isArray(response.data?.results))
        throw new Error('Attachments unavailable');
      return {
        rows: (response.data.results as WorkAttachment[]).filter(
          (file) =>
            file.model_type === 'workorder' && Number(file.model_id) === work.id
        ),
        more: !!response.data.next
      };
    }
  });
  const save = async () => {
    if (
      saving.current ||
      contextChanged ||
      (!intent &&
        (changed ||
          needsReview ||
          !text.trim() ||
          request.status === 'resolved'))
    )
      return;
    const command: ObservationIntent = intent ?? {
      operation: 'artifact',
      expected_version: base.work,
      request_version: base.request,
      idempotency_key: crypto.randomUUID(),
      fields: {
        kind: 'observation',
        payload: {
          text,
          ...(observedAt ? { observed_at: observedAt } : {}),
          ...(conditions ? { conditions } : {})
        },
        ...(attachment ? { attachment_id: Number(attachment) } : {})
      }
    };
    saving.current = true;
    setBusy(true);
    setIntent(command);
    setNotice('');
    try {
      const response = await api.post(
        `/api/kanban/work-orders/${work.id}/investigation-requests/${request.id}/`,
        command
      );
      const confirmed = observationReceipt(
        response.data,
        work.id,
        request.id,
        command
      );
      if (!confirmed) {
        setUncertain(true);
        setNotice(
          t`The response did not confirm this observation. Outcome unknown; the draft and original save intent are retained. Retry the original save to reconcile.`
        );
        return;
      }
      setReceipt(confirmed.artifact);
      setUncertain(false);
      setIntent(null);
      setBase({
        work: work.lifecycle_version,
        request: confirmed.request.version
      });
      client.setQueryData<WorkOrderOverview>(
        ['work-order', work.id],
        (previous) => {
          if (!previous?.investigation?.requests.items) return previous;
          return {
            ...previous,
            investigation: {
              ...previous.investigation,
              requests: {
                ...previous.investigation.requests,
                items: previous.investigation.requests.items.map((row) =>
                  row.id === request.id ? confirmed.request : row
                )
              }
            }
          };
        }
      );
      void client.invalidateQueries({ queryKey: ['work-order', work.id] });
    } catch (error) {
      const status = (error as { response?: { status?: number } }).response
        ?.status;
      if (!uncertain && status && [400, 403, 404, 409].includes(status)) {
        setIntent(null);
        setNeedsReview(true);
        setNotice(
          status === 409
            ? t`This record changed. This attempt saved no observation. Draft retained; review the latest request before saving again.`
            : t`This attempt was refused. No observation was saved by this attempt. The draft is retained; check access, request status and entered facts.`
        );
      } else {
        setUncertain(true);
        setNotice(
          t`Save outcome unknown. The draft and original save intent are retained. Retry the original save to reconcile; do not create another observation.`
        );
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  const reviewLatest = async () => {
    if (saving.current) return;
    saving.current = true;
    setBusy(true);
    try {
      // A failed review must not put the owning page query into an error state
      // and unmount its unsaved drafts. Publish only an authorized successful read.
      const response = await api.get<WorkOrderOverview>(
        apiUrl(ApiEndpoints.kanban_card_overview, work.id)
      );
      const result = response.data;
      const latest = result.investigation?.requests.items?.find(
        (row) => row.id === request.id && row.work_order === work.id
      );
      if (result.id !== work.id || !latest)
        throw new Error('Request unavailable');
      client.setQueryData(['work-order', work.id], result);
      if (
        result.machine !== captureContext.machine ||
        (result.affected_component_ref || result.affected_component || '') !==
          captureContext.component
      ) {
        setNeedsReview(true);
        setNotice(
          t`Machine or component context changed. This draft remains in its original capture context and cannot be filed or retried against the changed asset. Review the existing Work evidence before recording again.`
        );
        return;
      }
      setBase({ work: result.lifecycle_version, request: latest.version });
      setNeedsReview(false);
      setNotice(
        t`Latest request loaded. Review its purpose, requirements and status above; the draft stays on this request.`
      );
    } catch (error) {
      const status = (error as { response?: { status?: number } }).response
        ?.status;
      if (status && [401, 403, 404].includes(status)) {
        // The native page must hide cached records on an authorization refusal.
        await client.resetQueries({
          queryKey: ['work-order', work.id],
          exact: true
        });
        return;
      }
      setNeedsReview(true);
      setNotice(
        t`Latest request could not be read. The draft is retained; saving stays blocked until the context can be reviewed.`
      );
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  return (
    <Stack
      {...resultFocus.props}
      aria-label={t`Manual observation for request ${request.id}`}
      gap='xs'
      data-testid={`native-observation-${request.id}`}
      onKeyDown={(event) => {
        const target = event.target as HTMLElement;
        if (
          target.closest(
            '[data-mantine-stop-propagation], [data-expanded], [role=combobox][aria-expanded=true], [aria-haspopup=listbox][aria-expanded=true]'
          )
        )
          return;
        if (opened && event.key === 'Escape' && !event.defaultPrevented) {
          event.stopPropagation();
          setOpened(false);
          trigger.current?.focus();
        }
      }}
    >
      <Button
        ref={trigger}
        variant='subtle'
        size='sm'
        h='auto'
        mih={44}
        styles={{ label: { whiteSpace: 'normal' } }}
        style={{ alignSelf: 'flex-start', maxWidth: '100%' }}
        aria-expanded={opened}
        aria-controls={opened ? id : undefined}
        onClick={() => setOpened((value) => !value)}
      >
        {opened ? t`Hide manual observation` : t`Record observation manually`}
      </Button>
      <Collapse expanded={opened} keepMounted={false} transitionDuration={0}>
        {opened && (
          <Stack gap='sm' id={id} maw={760} w='100%'>
            <Text size='sm'>{t`Secondary capture and recovery. Continue the diagnosis in the existing assistant; this records an observed fact only.`}</Text>
            <Text size='sm'>
              {t`Capture context`}:{' '}
              {captureContext.machineName || t`Machine not recorded`} ·{' '}
              {captureContext.componentName || t`Component not supplied`} ·{' '}
              {work.reference} · {t`Request ${request.id}`} · v{base.request}
            </Text>
            {receipt ? (
              <Alert color='blue' title={t`Observation saved`}>
                <Stack gap='xs'>
                  <Text size='sm'>{t`Correlated to Work ${work.id} · Request ${request.id} · Evidence ${receipt.id}`}</Text>
                  <Text size='sm'>
                    {receipt.recorded_by_name || t`User ${receipt.recorded_by}`}{' '}
                    · {recordedTime(receipt.created_at)}
                  </Text>
                  <Text size='sm'>{String(receipt.payload.text)}</Text>
                  <Text size='sm'>{t`The request remains a separate decision. No diagnosis or completion was promoted automatically.`}</Text>
                  <Button
                    variant='default'
                    size='sm'
                    onClick={() => {
                      setReceipt(null);
                      setText('');
                      setObservedAt('');
                      setConditions('');
                      setAttachment(null);
                      setBase({
                        work: work.lifecycle_version,
                        request: request.version
                      });
                    }}
                  >{t`Record another observation`}</Button>
                </Stack>
              </Alert>
            ) : (
              <>
                <Text
                  size='xs'
                  c='dimmed'
                >{t`Local observation draft — retained while switching Work panels or closing this disclosure. Reloading or leaving this Work discards the local draft. This view has no confirmed save for this draft. Existing Work attachments keep their own upload state.`}</Text>
                <Textarea
                  label={t`Observed fact`}
                  minRows={3}
                  autosize
                  value={text}
                  readOnly={busy || !!intent}
                  onChange={(event) => setText(event.currentTarget.value)}
                />
                <TextInput
                  label={t`Observed at (optional, with time zone)`}
                  description={t`Use the actual source time, for example 2026-10-20T08:30:00+03:00. Leave blank if unknown.`}
                  value={observedAt}
                  readOnly={busy || !!intent}
                  onChange={(event) => setObservedAt(event.currentTarget.value)}
                />
                <TextInput
                  label={t`Conditions or instrument (optional)`}
                  value={conditions}
                  readOnly={busy || !!intent}
                  onChange={(event) => setConditions(event.currentTarget.value)}
                />
                <Select
                  label={t`Existing Work attachment (optional)`}
                  placeholder={t`No attachment selected`}
                  clearable
                  value={attachment}
                  onChange={setAttachment}
                  readOnly={busy || !!intent}
                  disabled={files.isFetching || files.isError}
                  data={(files.isError ? [] : (files.data?.rows ?? [])).map(
                    (file) => ({
                      value: String(file.pk),
                      label: `${file.filename} · ${file.pk}`
                    })
                  )}
                />
                <Text
                  size='xs'
                  c='dimmed'
                >{t`Use the native Attachments resource above to upload or inspect files, then refresh this list. Upload received is separate from a saved observation. No chat file or Closeout transcript is promoted here.`}</Text>
                <Button
                  variant='subtle'
                  size='xs'
                  onClick={() => files.refetch()}
                  disabled={busy || !!intent}
                >{t`Refresh Work attachments`}</Button>
                {files.isError && (
                  <Text
                    size='sm'
                    c='red'
                  >{t`Work attachments could not be read. The draft and any selected reference are retained.`}</Text>
                )}
                {files.data?.more && (
                  <Text size='sm'>{t`First 100 Work attachments shown. Use the native resource for the full list.`}</Text>
                )}
                {request.status === 'resolved' && (
                  <Alert color='orange'>{t`This request is resolved. It must be explicitly reopened before more evidence can be saved.`}</Alert>
                )}
                {changed && !intent && (
                  <Alert color='orange'>{t`The Work or request changed. Draft retained on its original request; review the latest context before saving.`}</Alert>
                )}
                {contextChanged && intent && (
                  <Alert color='orange'>{t`Capture context changed. The original outcome remains unknown; the original intent is retained but cannot be retried against a different asset.`}</Alert>
                )}
                {notice && <Alert color='orange'>{notice}</Alert>}
                <Group gap='sm'>
                  <Button
                    size='sm'
                    mih={44}
                    loading={busy}
                    disabled={
                      contextChanged ||
                      (!intent &&
                        (changed ||
                          needsReview ||
                          !text.trim() ||
                          request.status === 'resolved'))
                    }
                    onClick={() => resultFocus.run(save)}
                  >
                    {intent
                      ? t`Retry original save`
                      : t`Save observation to request`}
                  </Button>
                  {!intent && (
                    <Button
                      variant='default'
                      size='sm'
                      mih={44}
                      disabled={busy}
                      onClick={() => resultFocus.run(reviewLatest)}
                    >{t`Review latest request`}</Button>
                  )}
                </Group>
              </>
            )}
          </Stack>
        )}
      </Collapse>
    </Stack>
  );
}
