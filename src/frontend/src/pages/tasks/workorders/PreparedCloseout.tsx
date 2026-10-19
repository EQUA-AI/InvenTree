import type {
  CloseoutAmendment,
  CloseoutCapture,
  CloseoutPartUsageRow,
  CloseoutProposal,
  CloseoutReadingRow,
  CloseoutWorkOrder
} from '@lib/types/Closeout';
import type { StructuredCloseoutOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Text,
  Title
} from '@mantine/core';
import dayjs from 'dayjs';
import { Link } from 'react-router-dom';
import { ContinueInvestigation } from './execution/ContinueInvestigation';

const when = (value?: string | null) =>
  value ? dayjs(value).format('DD MMM YYYY, HH:mm') : t`Not recorded`;
const fact = (value: unknown) =>
  value == null || value === '' ? t`Not recorded` : String(value);

/** A readable projection, never a report-generation or completion command. */
export function PreparedCloseout({
  workOrder,
  updateStatus,
  captures,
  proposal,
  fields,
  accepted,
  amendments,
  readings,
  error,
  changed,
  partUsage,
  draftDowntime,
  draftFollowUp
}: Readonly<{
  workOrder: CloseoutWorkOrder;
  updateStatus: string;
  captures?: CloseoutCapture[];
  proposal?: CloseoutProposal;
  fields: Record<string, string>;
  accepted?: StructuredCloseoutOverview | null;
  amendments?: CloseoutAmendment[];
  readings?: CloseoutReadingRow[];
  error: boolean;
  changed: boolean;
  partUsage?: CloseoutPartUsageRow[];
  draftDowntime: number | '';
  draftFollowUp: string;
}>) {
  const values = accepted ?? fields;
  const labels: Record<string, string> = {
    cause: t`Cause`,
    action: t`Action taken`,
    result: t`Result`,
    verification_summary: t`Verification summary`
  };
  const report = [
    `${workOrder.reference} · ${workOrder.title}`,
    accepted
      ? `${t`Accepted account`} ${accepted.id} · ${when(accepted.completed_at)}`
      : `${t`Prepared account`} · ${t`Work version`} ${workOrder.lifecycle_version}`,
    ...Object.entries(labels).map(
      ([key, label]) => `${label}: ${fact(values[key as keyof typeof values])}`
    ),
    `${t`Downtime (minutes)`}: ${fact(accepted ? accepted.downtime_minutes : draftDowntime)}`,
    `${t`Person-work time`}: ${t`Not recorded`}`,
    `${t`Follow-up`}: ${fact(accepted ? accepted.follow_up : draftFollowUp)}`,
    `${t`Operating / service return`}: ${t`Not established by this account`}`,
    accepted
      ? t`Accepted fields are preserved. Material and reading rows are current supporting records; a complete accepted report snapshot is not supplied by this server.`
      : t`Prepared draft; no record update has been submitted by preview or download.`,
    ...(captures ?? []).flatMap((capture) => [
      `${t`Story source`}: ${capture.source_type} · ${t`Capture`} ${capture.id} · ${capture.status} · ${t`Revision`} ${capture.revision ?? '?'} · ${t`Recorded by user`} ${capture.created_by ?? '?'} · ${when(capture.created_at)}`,
      `${t`Transcript reference`}: ${fact(capture.transcript_reference)}`,
      capture.narrative,
      ...(capture.revisions ?? []).map(
        (row) =>
          `${t`Revision`} ${row.revision} · ${t`Recorded by user`} ${row.created_by} · ${when(row.created_at)}\n${row.narrative}`
      )
    ]),
    ...(partUsage ?? []).map(
      (row) =>
        `${t`Material usage row`} ${row.id} · ${row.source} · ${row.state} · ${t`Issued`}: ${fact(row.issued_quantity)} · ${t`Used`}: ${fact(row.used_quantity)} · ${fact(row.disposition)}`
    ),
    ...(accepted?.original
      ? [
          `${t`Original accepted snapshot`} · ${fact(accepted.content_hash)}\n${JSON.stringify(accepted.original)}`
        ]
      : []),
    ...(readings ?? []).map(
      (row) =>
        `${row.label}: ${row.raw_text} ${row.unit} · ${row.verification_state} · ${when(row.recorded_at)} · ${t`Recorded by user`} ${row.recorded_by ?? t`Not recorded`}`
    ),
    ...(amendments ?? []).map(
      (row) =>
        `${t`Amendment`} ${row.id} · ${row.status} · ${t`Requested by user`} ${row.requested_by} · ${when(row.created_at)}\n${row.reason}\n${JSON.stringify(row.changes)}`
    )
  ].join('\n\n');
  const download = () => {
    const url = URL.createObjectURL(
      new Blob([report], { type: 'text/plain;charset=utf-8' })
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `work-${workOrder.id}-account-${accepted?.id ?? `v${workOrder.lifecycle_version}`}.txt`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <Stack
      gap='md'
      data-testid='prepared-closeout'
      data-update-status={updateStatus}
    >
      {error && (
        <Alert color='red'>{t`Some closeout sources could not be read. Missing information is unknown; refresh before updating.`}</Alert>
      )}
      <Card withBorder>
        <Stack gap='sm'>
          <Title order={4}>{t`Technician story & insights`}</Title>
          {captures === undefined ? (
            <Text>{t`Story sources are not available yet.`}</Text>
          ) : captures.length === 0 ? (
            <Text>{t`No saved technician story. Add the finding, work and reasoning, failed approaches, practical advice and unresolved follow-up in the existing assistant or native capture below.`}</Text>
          ) : (
            captures.map((capture) => (
              <Stack key={capture.id} gap={4}>
                <Group gap='xs'>
                  <Badge variant='light'>
                    {capture.source_type === 'voice'
                      ? t`Voice transcript`
                      : t`Typed account`}
                  </Badge>
                  <Text size='sm'>
                    {t`Capture`} {capture.id} · {capture.status} · {t`Revision`}{' '}
                    {capture.revision ?? t`Not recorded`}
                  </Text>
                </Group>
                <Text size='xs' c='dimmed'>
                  {t`Recorded by user`} {capture.created_by ?? t`Not recorded`}{' '}
                  · {when(capture.created_at)}
                </Text>
                <Text
                  style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                >
                  {capture.narrative}
                </Text>
                {capture.transcript_reference && (
                  <Text size='xs' style={{ overflowWrap: 'anywhere' }}>
                    {t`Transcript reference`}: {capture.transcript_reference}
                  </Text>
                )}
                <details>
                  <summary>{t`Retained story revisions`}</summary>
                  {capture.revisions?.length ? (
                    [...capture.revisions]
                      .sort((a, b) => a.revision - b.revision)
                      .map((row) => (
                        <Card key={row.id} withBorder mt='xs' padding='sm'>
                          <Text size='sm'>
                            {t`Revision`} {row.revision} · {t`Recorded by user`}{' '}
                            {row.created_by} · {when(row.created_at)}
                          </Text>
                          <Text style={{ whiteSpace: 'pre-wrap' }}>
                            {row.narrative}
                          </Text>
                        </Card>
                      ))
                  ) : (
                    <Text size='sm'>{t`Earlier revisions are not supplied by this server.`}</Text>
                  )}
                </details>
              </Stack>
            ))
          )}
          <Text
            size='xs'
            c='dimmed'
          >{t`Topic coverage and reviewed reusable lessons are not supplied. A story or extraction is not an approved procedure. Audio playback and timing are unavailable without a linked media source.`}</Text>
          <ContinueInvestigation
            workOrder={workOrder}
            panel='closeout'
            label={t`Complete missing story`}
            caption={false}
            variant='light'
          />
          <Text
            size='xs'
            c='dimmed'
          >{t`Carries this Work to the existing assistant; naming a story there does not save a closeout capture automatically.`}</Text>
        </Stack>
      </Card>
      <Card withBorder>
        <Stack gap='sm'>
          <Group justify='space-between'>
            <Title order={4}>{t`Prepared maintenance account`}</Title>
            <Badge color={accepted ? 'green' : 'gray'}>
              {accepted
                ? t`Accepted account`
                : changed
                  ? t`Unsaved local changes`
                  : t`Prepared draft`}
            </Badge>
          </Group>
          <Text size='xs' c='dimmed'>
            {accepted
              ? `${t`Closeout id`}: ${accepted.id} · ${when(accepted.completed_at)}`
              : `${t`Work version`} ${workOrder.lifecycle_version} · ${t`Extraction is not a verified fact`}`}
          </Text>
          {Object.entries(labels).map(([key, label]) => (
            <div key={key}>
              <Text size='xs' c='dimmed'>
                {label}
              </Text>
              <Text
                style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
              >
                {key === 'cause' && !values.cause
                  ? t`Not established`
                  : fact(values[key as keyof typeof values])}
              </Text>
              {!accepted &&
                proposal?.decisions.find((row) => row.field_path === key) && (
                  <Text size='xs' c='dimmed'>
                    {t`Saved decision`}:{' '}
                    {
                      proposal.decisions.find((row) => row.field_path === key)
                        ?.decision
                    }{' '}
                    · {t`Recorded by user`}{' '}
                    {proposal.decisions.find((row) => row.field_path === key)
                      ?.decided_by ?? t`Not recorded`}
                  </Text>
                )}
            </div>
          ))}
          <Anchor
            component={Link}
            to='?work_panel=verify'
          >{t`View verification in Verify & Return`}</Anchor>
          <Text
            size='sm'
            c='dimmed'
          >{t`This account does not establish permission to operate or service return.`}</Text>
          <Title order={5}>{t`Materials and time`}</Title>
          <Anchor
            component={Link}
            to='?work_panel=parts'
          >{t`View recorded materials and custody`}</Anchor>
          <Text size='sm'>
            {t`Person-work time`}: {t`Not recorded`} · {t`Downtime (minutes)`}:{' '}
            {fact(accepted ? accepted.downtime_minutes : draftDowntime)}
          </Text>
          <Text
            size='xs'
            c='dimmed'
          >{t`Elapsed work time and recording length are not person-work time. Material discrepancies use the native reconciliation controls.`}</Text>
          {partUsage === undefined ? (
            <Text size='sm'>{t`Material reconciliation is not available.`}</Text>
          ) : partUsage.length === 0 ? (
            <Text size='sm'>{t`No material usage rows recorded.`}</Text>
          ) : (
            partUsage.map((row) => (
              <Text size='sm' key={row.id}>
                {t`Material usage row`} {row.id} · {row.source} · {row.state} ·{' '}
                {t`Issued`}: {fact(row.issued_quantity)} · {t`Used`}:{' '}
                {fact(row.used_quantity)} · {fact(row.disposition)}
              </Text>
            ))
          )}
          {!accepted && (draftDowntime !== '' || draftFollowUp) && (
            <Text
              size='xs'
              c='dimmed'
            >{t`Optional time and follow-up edits are an unsaved draft.`}</Text>
          )}
          <Title order={5}>{t`Saved readings and attempts`}</Title>
          {readings === undefined ? (
            <Text size='sm'>{t`Reading records are not available.`}</Text>
          ) : readings.length === 0 ? (
            <Text size='sm'>{t`No closeout readings recorded.`}</Text>
          ) : (
            readings.map((row) => (
              <Text key={row.id} size='sm'>
                {row.label}: {row.raw_text || t`Not recorded`} {row.unit} ·{' '}
                {row.verification_state}
              </Text>
            ))
          )}
          <Title order={5}>{t`Report preview`}</Title>
          {accepted && (
            <Text
              size='sm'
              c='dimmed'
            >{t`Accepted fields are preserved. Material and reading rows are current supporting records; a complete accepted report snapshot is not supplied by this server.`}</Text>
          )}
          <Text size='sm'>{t`The preview exports the displayed account and recorded source references. It does not update the maintenance record.`}</Text>
          <details>
            <summary>{t`Read report`}</summary>
            <Text
              component='pre'
              size='sm'
              style={{
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                fontFamily: 'inherit'
              }}
            >
              {report}
            </Text>
          </details>
          <Button
            variant='default'
            onClick={download}
            disabled={error || captures === undefined || readings === undefined}
          >{t`Download account (.txt)`}</Button>
          {accepted && (
            <details>
              <summary>{t`Original accepted snapshot`}</summary>
              <Text size='xs' style={{ overflowWrap: 'anywhere' }}>
                {t`Content reference`}: {fact(accepted.content_hash)}
              </Text>
              {accepted.original ? (
                Object.entries(accepted.original).map(([key, value]) => (
                  <Text
                    key={key}
                    size='sm'
                    style={{ overflowWrap: 'anywhere' }}
                  >
                    {labels[key] ?? key}: {fact(value)}
                  </Text>
                ))
              ) : (
                <Text>{t`Original snapshot is not supplied by this server.`}</Text>
              )}
            </details>
          )}
          {accepted && (
            <>
              <Title order={5}>{t`Attributed amendments`}</Title>
              {amendments === undefined ? (
                <Text>{t`Amendments could not yet be read.`}</Text>
              ) : amendments.length === 0 ? (
                <Text size='sm'>{t`No amendments recorded.`}</Text>
              ) : (
                amendments.map((row) => (
                  <Card withBorder key={row.id} padding='sm'>
                    <Text size='sm'>
                      {t`Amendment`} {row.id} · {row.status}
                    </Text>
                    <Text size='xs' c='dimmed'>
                      {t`Requested by user`} {row.requested_by} ·{' '}
                      {when(row.created_at)} · {t`Decided by user`}{' '}
                      {row.decided_by ?? t`Not recorded`} ·{' '}
                      {when(row.applied_at)}
                    </Text>
                    <Text size='sm'>{row.reason}</Text>
                    {Object.entries(row.changes).map(([key, value]) => (
                      <Text size='sm' key={key}>
                        {labels[key] ?? key}: {fact(value.from)} →{' '}
                        {fact(value.to)}
                      </Text>
                    ))}
                  </Card>
                ))
              )}
            </>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
