import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { CalibrationTraceability as Traceability } from '@lib/types/RoutineWork';
import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Modal,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useApi } from '../../../../contexts/ApiContext';
import { generateUrl } from '../../../../functions/urls';
import { useLocalState } from '../../../../states/LocalState';
import { useUserState } from '../../../../states/UserState';
import {
  ExecutionFact,
  executionBadgeStyles,
  recordedTime
} from './ExecutionFacts';
import {
  recordedCertificateAvailable,
  savedCalibrationRevision
} from './calibrationCommand';

function TraceabilityFacts({ row }: Readonly<{ row: Traceability }>) {
  const expired = row.validity_state === 'expired_at_reading';
  const label = expired
    ? t`Certificate expired at reading time`
    : row.validity_state === 'within_date_at_reading'
      ? t`Certificate within recorded date at reading time`
      : t`Certificate date validity unknown`;
  return (
    <Stack gap='xs'>
      <Group gap='xs'>
        <Badge
          color='gray'
          styles={executionBadgeStyles}
        >{t`Revision ${row.revision}`}</Badge>
        <Badge
          color={expired ? 'orange' : 'gray'}
          styles={executionBadgeStyles}
        >
          {label}
        </Badge>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='xs'>
        <ExecutionFact label={t`Reference instrument ID`}>
          {row.reference_instrument_id}
        </ExecutionFact>
        <ExecutionFact label={t`Certificate number`}>
          {row.certificate_number}
        </ExecutionFact>
        <ExecutionFact label={t`Issuing lab`}>{row.issuing_lab}</ExecutionFact>
        <ExecutionFact label={t`Valid until`}>
          {row.valid_until ?? t`Unknown`}
        </ExecutionFact>
      </SimpleGrid>
      <Text
        size='xs'
        c='dimmed'
      >{t`Date comparison uses the reading time in ${row.validity_timezone}. This is not calibration acceptance.`}</Text>
      <Text size='sm'>
        {recordedCertificateAvailable(row) ? (
          <Anchor
            data-testid='calibration-certificate-link'
            href={generateUrl(row.certificate_url!)}
            target='_blank'
            rel='noopener noreferrer'
          >{t`Open attached certificate`}</Anchor>
        ) : row.certificate_integrity_state === 'changed' ? (
          t`Certificate changed since recorded`
        ) : row.certificate_integrity_state === 'identity_not_recorded' ||
          row.certificate_integrity_state === undefined ? (
          t`Certificate byte identity not recorded`
        ) : (
          t`Attached certificate unavailable`
        )}
        {' · '}
        {row.certificate_snapshot.filename.split('/').pop() ||
          t`Certificate filename not recorded`}
      </Text>
      {row.correction_reason && (
        <Text size='sm'>
          {t`Correction reason`}: {row.correction_reason}
        </Text>
      )}
      <Text size='xs'>
        {t`Traceability recorded by`}: {row.recorded_by_name ?? t`Not recorded`}{' '}
        · {recordedTime(row.recorded_at)}
      </Text>
    </Stack>
  );
}

interface Intent {
  operation: 'record' | 'correct';
  expected_version: number;
  expected_revision: number;
  idempotency_key: string;
  reference_instrument_id: string;
  certificate_number: string;
  issuing_lab: string;
  valid_until: string | null;
  certificate_id: number;
  correction_reason: string;
}

function TraceabilityForm({
  workId,
  readingId,
  workVersion: openingWorkVersion,
  latest: openingLatest,
  close,
  setCommandLocked
}: Readonly<{
  workId: number;
  readingId: number;
  workVersion: number;
  latest?: Traceability;
  close: () => void;
  setCommandLocked: (locked: boolean) => void;
}>) {
  // Keep the draft bound to the facts and Work version the person opened.
  const [{ latest, workVersion }] = useState(() => ({
    latest: openingLatest,
    workVersion: openingWorkVersion
  }));
  const api = useApi();
  const cache = useQueryClient();
  const identity = useUserState((s) => s.authGeneration);
  const actor = useUserState((s) => s.user?.pk);
  const host = useLocalState((s) => s.hostKey);
  const [instrument, setInstrument] = useState(
    latest?.reference_instrument_id ?? ''
  );
  const [number, setNumber] = useState(latest?.certificate_number ?? '');
  const [lab, setLab] = useState(latest?.issuing_lab ?? '');
  const [until, setUntil] = useState(latest?.valid_until ?? '');
  const [certificate, setCertificate] = useState<string | null>(
    recordedCertificateAvailable(latest) && latest?.certificate_id
      ? String(latest.certificate_id)
      : null
  );
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Intent | null>(null);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const files = useQuery<{ value: string; label: string }[]>({
    queryKey: ['calibration-certificates', host, actor, identity, workId],
    retry: false,
    queryFn: async ({ signal }) => {
      const options: { value: string; label: string }[] = [];
      const seen = new Set<number>();
      let offset = 0;
      while (true) {
        const response = await api.get(apiUrl(ApiEndpoints.attachment_list), {
          signal,
          params: {
            model_type: 'workorder',
            model_id: workId,
            limit: 100,
            offset
          }
        });
        const data = response.data;
        if (!Array.isArray(data?.results))
          throw new Error('Attachment list unavailable');
        for (const file of data.results) {
          if (!Number.isSafeInteger(file.pk) || seen.has(file.pk))
            throw new Error('Attachment pagination changed');
          seen.add(file.pk);
          if (file.attachment)
            options.push({
              value: String(file.pk),
              label: file.filename || file.comment || String(file.pk)
            });
        }
        if (!data.next) break;
        if (data.results.length === 0)
          throw new Error('Attachment pagination unavailable');
        offset += data.results.length;
      }
      return options;
    }
  });
  const locked = busy || !!pending || stale;
  const submit = async () => {
    const intent: Intent = pending ?? {
      operation: latest ? 'correct' : 'record',
      expected_version: workVersion,
      expected_revision: latest?.revision ?? 0,
      idempotency_key: `cal-${crypto.randomUUID()}`,
      reference_instrument_id: instrument.trim(),
      certificate_number: number.trim(),
      issuing_lab: lab.trim(),
      valid_until: until || null,
      certificate_id: Number(certificate),
      correction_reason: reason.trim()
    };
    if (busy || stale) return;
    setCommandLocked(true);
    setBusy(true);
    setPending(intent);
    setError('');
    try {
      const response = await api.post(
        apiUrl(
          `kanban/work-orders/${workId}/calibration-traceability/${readingId}/`
        ),
        intent
      );
      if (
        !savedCalibrationRevision(
          response.data,
          workId,
          readingId,
          intent.expected_revision
        )
      )
        throw new Error('Unconfirmed calibration receipt');
      await cache.invalidateQueries({ queryKey: ['work-order', workId] });
      setCommandLocked(false);
      close();
    } catch (cause) {
      const status = (cause as { response?: { status?: number } }).response
        ?.status;
      if (status && status >= 400 && status < 500) {
        setPending(null);
        setCommandLocked(false);
        setStale(status === 409);
        setError(
          status === 409
            ? t`The Work or traceability changed. Close this form and refresh the Work before trying again.`
            : status === 403 || status === 404
              ? t`This command is not permitted or the Work is unavailable. Nothing was confirmed saved.`
              : t`Traceability was not saved. Check the fields and select an uploaded certificate belonging to this Work.`
        );
      } else {
        setError(
          t`Save outcome unknown. Keep this form open and retry the same command to retrieve its saved result. Fields are locked to prevent a duplicate revision.`
        );
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <Stack gap='sm'>
        <Text size='sm'>{t`Reading ${readingId}. Record the certificate facts used for this reading. This does not accept the calibration.`}</Text>
        {latest && (
          <Text size='sm'>{t`A correction creates revision ${latest.revision + 1}. Earlier revisions are retained.`}</Text>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            label={t`Reference instrument ID`}
            required
            maxLength={200}
            value={instrument}
            disabled={locked}
            onChange={(e) => setInstrument(e.currentTarget.value)}
          />
          <TextInput
            label={t`Certificate number`}
            required
            maxLength={200}
            value={number}
            disabled={locked}
            onChange={(e) => setNumber(e.currentTarget.value)}
          />
          <TextInput
            label={t`Issuing lab`}
            required
            maxLength={200}
            value={lab}
            disabled={locked}
            onChange={(e) => setLab(e.currentTarget.value)}
          />
          <TextInput
            label={t`Valid until`}
            type='date'
            inputWrapperOrder={['label', 'input', 'description', 'error']}
            description={t`Leave empty if unknown. Compared with the reading date.`}
            value={until}
            disabled={locked}
            onChange={(e) => setUntil(e.currentTarget.value)}
          />
        </SimpleGrid>
        <Select
          label={t`Attached certificate`}
          required
          searchable
          data={files.data ?? []}
          value={certificate}
          disabled={locked || !files.isSuccess || files.isFetching}
          onChange={setCertificate}
          placeholder={
            files.isPending
              ? t`Loading Work attachments…`
              : t`Select an uploaded file`
          }
        />
        <Text size='xs'>{t`Upload the certificate through Work attachments above, then refresh this list. External links are not uploaded certificates.`}</Text>
        {files.isError && (
          <Alert
            color='red'
            title={t`Certificate list unavailable`}
          >{t`The complete authorized file list could not be read. Retry the list before saving.`}</Alert>
        )}
        {files.isSuccess && files.data.length === 0 && (
          <Text size='sm'>{t`No uploaded certificate files returned for this Work.`}</Text>
        )}
        <Button
          variant='subtle'
          w='fit-content'
          disabled={locked || files.isFetching}
          onClick={() => void files.refetch()}
        >{t`Refresh certificate list`}</Button>
        {latest && (
          <Textarea
            label={t`Correction reason`}
            required
            maxLength={4000}
            value={reason}
            disabled={locked}
            onChange={(e) => setReason(e.currentTarget.value)}
          />
        )}
        {error && (
          <Alert
            color='red'
            title={t`Traceability save not confirmed`}
            role='alert'
          >
            {error}
          </Alert>
        )}
        <Group justify='flex-end'>
          <Button
            variant='default'
            disabled={busy || !!pending}
            onClick={close}
          >{t`Cancel`}</Button>
          <Button
            type='submit'
            loading={busy}
            disabled={
              stale ||
              (!pending &&
                (!files.isSuccess ||
                  files.isFetching ||
                  !certificate ||
                  !instrument.trim() ||
                  !number.trim() ||
                  !lab.trim() ||
                  (!!latest && !reason.trim())))
            }
          >
            {pending
              ? t`Retry same command`
              : latest
                ? t`Save new revision`
                : t`Record traceability`}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

export function CalibrationTraceability({
  workId,
  readingId,
  workVersion,
  revisions,
  canRecord
}: Readonly<{
  workId: number;
  readingId: number;
  workVersion: number;
  revisions?: Traceability[];
  canRecord: boolean;
}>) {
  const [opened, setOpened] = useState(false);
  const [commandLocked, setCommandLocked] = useState(false);
  // The server returns newest first; filter the exact parents before showing facts.
  const rows =
    revisions?.filter(
      (r) => r.work_order === workId && r.reading === readingId
    ) ?? [];
  const latest = rows[0];
  return (
    <Stack
      gap='xs'
      mt='xs'
      pl='sm'
      style={{
        borderInlineStart: '2px solid var(--mantine-color-default-border)'
      }}
      component='section'
      aria-label={t`Reading ${readingId} calibration traceability`}
    >
      <Group justify='space-between' gap='xs'>
        <Text size='sm' fw={600}>{t`Calibration traceability`}</Text>
        {canRecord && revisions && (
          <Button size='sm' variant='light' onClick={() => setOpened(true)}>
            {latest ? t`Correct traceability` : t`Record traceability`}
          </Button>
        )}
      </Group>
      {!revisions ? (
        <Text size='sm'>{t`Calibration traceability unavailable — this server does not supply the per-reading records.`}</Text>
      ) : latest ? (
        <TraceabilityFacts row={latest} />
      ) : (
        <Text size='sm'>{t`Calibration traceability not recorded`}</Text>
      )}
      {rows.length > 1 && (
        <Accordion variant='contained'>
          <Accordion.Item value='history'>
            <Accordion.Control>{t`Earlier traceability revisions (${rows.length - 1})`}</Accordion.Control>
            <Accordion.Panel>
              <Stack gap='md'>
                {rows.slice(1).map((row) => (
                  <TraceabilityFacts key={row.id} row={row} />
                ))}
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
      {canRecord && revisions && (
        <Modal
          opened={opened}
          onClose={() => {
            if (!commandLocked) setOpened(false);
          }}
          withCloseButton={!commandLocked}
          closeButtonProps={{
            'aria-label': t`Close calibration traceability form`
          }}
          closeOnClickOutside={!commandLocked}
          closeOnEscape={!commandLocked}
          title={
            latest
              ? t`Correct calibration traceability`
              : t`Record calibration traceability`
          }
          size='lg'
        >
          {opened && (
            <TraceabilityForm
              workId={workId}
              readingId={readingId}
              workVersion={workVersion}
              latest={latest}
              close={() => setOpened(false)}
              setCommandLocked={setCommandLocked}
            />
          )}
        </Modal>
      )}
    </Stack>
  );
}
