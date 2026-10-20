import { displayCloseout, displayReading } from '@lib/types/CanonicalDisplay';
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
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { openGlobalAIChatAboutWork } from '../../../states/AIChatState';
import { OriginalMedia } from './OriginalMedia';
import { ContinueInvestigation } from './execution/ContinueInvestigation';
import { narrativeExcerpts } from './storyProvenance';

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
  draftFollowUp,
  sourcesEnabled = true,
  accountLoading = false,
  accountUnavailable = false,
  manualAccepted = false,
  sourceCapture,
  reconciliation,
  updateAction
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
  sourcesEnabled?: boolean;
  accountLoading?: boolean;
  accountUnavailable?: boolean;
  manualAccepted?: boolean;
  sourceCapture?: CloseoutCapture | null;
  reconciliation?: ReactNode;
  updateAction?: ReactNode;
}>) {
  captures = captures?.map((row) => displayCloseout(row).record);
  const values = accepted ?? fields;
  const verification = accepted?.verified_at
    ? `${t`Verified by`} ${fact(accepted.verified_by_name)} · ${when(accepted.verified_at)}`
    : t`Acceptance: Not recorded`;
  const labels: Record<string, string> = {
    cause: t`Cause`,
    action: t`Action taken`,
    result: t`Result`,
    verification_summary: t`Verification summary`
  };
  const loading =
    accountLoading ||
    captures === undefined ||
    readings === undefined ||
    partUsage === undefined ||
    (!!accepted && amendments === undefined);
  const report = [
    `${workOrder.reference} · ${workOrder.title}`,
    accepted
      ? `${t`Completed account`} ${accepted.id} · ${when(accepted.completed_at)} · ${t`Completed by`} ${fact(accepted.completed_by_name)}\n${verification}`
      : `${t`Prepared account`} · ${t`Work version`} ${workOrder.lifecycle_version}`,
    ...Object.entries(labels).map(
      ([key, label]) =>
        `${label}: ${key === 'cause' && !values.cause ? t`Not established` : fact(values[key as keyof typeof values])}`
    ),
    `${t`Downtime (minutes)`}: ${fact(accepted ? accepted.downtime_minutes : draftDowntime)}`,
    `${t`Person-work time`}: ${t`Unavailable — coverage unknown`}`,
    `${t`Follow-up`}: ${fact(accepted ? accepted.follow_up : draftFollowUp)}`,
    `${t`Operating / service return`}: ${t`Not established by this account`}`,
    accepted
      ? accepted.reports?.length
        ? t`This is the current account reference. Use the frozen closeout report above for the account and sources at completion.`
        : t`Completed fields are preserved. Material and reading rows are current supporting records; a complete frozen report snapshot is not supplied by this server.`
      : t`Prepared draft; no record update has been submitted by preview or download.`,
    ...(sourceCapture
      ? Object.entries(proposal?.fields ?? {}).flatMap(([key, field]) =>
          narrativeExcerpts(sourceCapture.narrative, field).map(
            (span) =>
              `${t`Source`}: ${labels[key] ?? key} · ${t`Capture`} ${sourceCapture.id} · ${t`Revision`} ${sourceCapture.revision} · ${t`Narrative code points`} [${span.start}, ${span.end}) · ${span.text}`
          )
        )
      : []),
    ...(proposal?.decisions ?? []).map(
      (row) =>
        `${t`Saved decision`}: ${labels[row.field_path] ?? row.field_path} · ${row.decision} · ${t`Recorded by`} ${fact(row.decided_by_name)} · ${when(row.decided_at)}`
    ),
    ...(captures ?? []).flatMap((capture) => [
      `${t`Story source`}: ${capture.source_type} · ${t`Capture`} ${capture.id} · ${capture.status} · ${t`Revision`} ${capture.revision ?? '?'} · ${t`Recorded by`} ${fact(capture.created_by_name)} · ${when(capture.created_at)}`,
      `${t`Transcript reference`}: ${fact(capture.transcript_reference)}`,
      capture.narrative,
      ...(capture.source_type === 'typed'
        ? [
            `${t`Voice unavailable reason`}: ${capture.voice_unavailable_reason || t`Not supplied`}`
          ]
        : []),
      ...[...(capture.revisions ?? [])]
        .sort((a, b) => a.revision - b.revision)
        .map(
          (row) =>
            `${t`Revision`} ${row.revision} · ${t`Recorded by`} ${fact(row.created_by_name)} · ${when(row.created_at)}\n${row.narrative}`
        )
    ]),
    ...(partUsage === undefined ? [t`Materials: not available yet`] : []),
    ...(manualAccepted
      ? [t`Per-field decisions are not supplied for this manual account.`]
      : []),
    ...(accepted && amendments === undefined
      ? [t`Amendments: not available yet`]
      : []),
    ...(partUsage ?? []).map(
      (row) =>
        `${t`Material usage row`} ${row.id} · ${row.source} · ${row.state} · ${t`Issued`}: ${fact(row.issued_quantity)} · ${t`Used`}: ${fact(row.used_quantity)} · ${fact(row.disposition)}`
    ),
    ...(accepted?.original
      ? [
          `${t`Original completed account`} · ${fact(accepted.content_hash)}\n${JSON.stringify(accepted.original)}`
        ]
      : []),
    ...(readings ?? []).map(
      (row) =>
        `${row.label}: ${row.raw_text} ${row.unit} · ${row.verification_state} · ${when(row.recorded_at)} · ${t`Recorded by`} ${fact(row.recorded_by_name)}`
    ),
    ...(amendments ?? []).map(
      (row) =>
        `${t`Amendment`} ${row.id} · ${row.status} · ${t`Requested by`} ${fact(row.requested_by_name)} · ${when(row.created_at)}\n${row.reason}\n${row.status === 'proposed' ? t`Not yet decided` : `${t`Decided by`} ${fact(row.decided_by_name)} · ${when(row.applied_at)}`}\n${JSON.stringify(row.changes)}`
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
      <Card withBorder data-testid='closeout-account'>
        <Stack gap='sm'>
          <Group justify='space-between'>
            <Title order={2} size='h4'>{t`Prepared maintenance account`}</Title>
            <Badge color={accepted ? 'green' : 'gray'}>
              {accepted
                ? t`Completed account`
                : changed
                  ? t`Unsaved local changes`
                  : t`Prepared draft`}
            </Badge>
          </Group>
          <Text size='xs' c='dimmed'>
            {accepted
              ? `${t`Closeout id`}: ${accepted.id} · ${when(accepted.completed_at)} · ${t`Completed by`} ${fact(accepted.completed_by_name)}`
              : `${t`Work version`} ${workOrder.lifecycle_version} · ${t`Extraction is not a verified fact`}`}
          </Text>
          {accepted && <Text size='sm'>{verification}</Text>}
          {Object.entries(labels).map(([key, label]) => {
            const decision =
              !accountLoading &&
              proposal?.decisions.find((row) => row.field_path === key);
            return (
              <div key={key}>
                <Text size='xs' c='dimmed'>
                  {label}
                </Text>
                <Text
                  style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                >
                  {accountUnavailable && !accepted
                    ? t`Account decisions unavailable`
                    : accountLoading && !accepted
                      ? t`Loading account decisions…`
                      : key === 'cause' && !values.cause
                        ? t`Not established`
                        : fact(values[key as keyof typeof values])}
                </Text>
                {!accountLoading &&
                  sourceCapture &&
                  narrativeExcerpts(
                    sourceCapture.narrative,
                    proposal?.fields[key]
                  ).map((span) => (
                    <Text
                      key={`${span.start}-${span.end}`}
                      size='xs'
                      c='dimmed'
                    >
                      {t`Source`}: {t`Capture`} {sourceCapture.id} ·{' '}
                      {t`Revision`} {sourceCapture.revision} ·{' '}
                      {t`Narrative code points`} [{span.start}, {span.end}) · “
                      {span.text}”
                    </Text>
                  ))}
                {decision && (
                  <Text size='xs' c='dimmed'>
                    {t`Saved decision`}: {decision.decision} · {t`Recorded by`}{' '}
                    {fact(decision.decided_by_name)} ·{' '}
                    {when(decision.decided_at)}
                  </Text>
                )}
              </div>
            );
          })}
          {manualAccepted && (
            <Text
              size='xs'
              c='dimmed'
            >{t`Per-field decisions are not supplied for this manual account.`}</Text>
          )}
          <Anchor
            component={Link}
            to='?work_panel=verify'
          >{t`View verification in Verify & Return`}</Anchor>
          <Text
            size='sm'
            c='dimmed'
          >{t`This account does not establish permission to operate or service return.`}</Text>
          <Title order={3} size='h5'>{t`Materials and time`}</Title>
          <Anchor
            component={Link}
            to='?work_panel=parts'
          >{t`View recorded materials and custody`}</Anchor>
          <Text size='sm'>
            {t`Person-work time`}: {t`Unavailable — coverage unknown`} ·{' '}
            {t`Downtime (minutes)`}:{' '}
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
          {reconciliation}
          <Title order={3} size='h5'>{t`Saved readings and attempts`}</Title>
          {readings === undefined ? (
            <Text size='sm'>{t`Reading records are not available.`}</Text>
          ) : readings.length === 0 ? (
            <Text size='sm'>{t`No closeout readings recorded.`}</Text>
          ) : (
            readings.map((row) => (
              <Group key={row.id} gap='xs' wrap='wrap'>
                <Text size='sm'>
                  {row.label}: {row.raw_text || t`Not recorded`} {row.unit} ·{' '}
                  {row.verification_state}
                </Text>
                {displayReading(row).focus && (
                  <Button
                    size='compact-xs'
                    variant='subtle'
                    disabled={error || loading}
                    onClick={() => {
                      const evidence = displayReading(row).focus;
                      if (evidence)
                        openGlobalAIChatAboutWork({
                          workOrderId: workOrder.id,
                          lifecycleVersion: workOrder.lifecycle_version,
                          evidence,
                          label: `${workOrder.reference} · ${row.label}`,
                          returnPath: `/maintenance/work-orders/${workOrder.id}?work_panel=closeout`
                        });
                    }}
                  >{t`Ask about this reading`}</Button>
                )}
              </Group>
            ))
          )}
          <Title order={3} size='h5'>{t`Report preview`}</Title>
          {accepted && (
            <Text size='sm' c='dimmed'>
              {accepted.reports?.length
                ? t`This is the current account reference. Use the frozen closeout report above for the account and sources at completion.`
                : t`Completed fields are preserved. Material and reading rows are current supporting records; a complete frozen report snapshot is not supplied by this server.`}
            </Text>
          )}
          <Text size='sm'>{t`The preview exports the displayed account and recorded source references. It does not update the maintenance record.`}</Text>
          {loading ? (
            <Text size='sm'>
              {sourcesEnabled
                ? t`Loading report sources… Preview is unavailable until the account, materials, readings and amendments have been read.`
                : t`Report sources are not enabled. Preview and download are unavailable.`}
            </Text>
          ) : (
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
          )}
          <Button
            variant='default'
            onClick={download}
            disabled={error || loading}
          >{t`Download account (.txt)`}</Button>
          {updateAction}
          {accepted && (
            <details>
              <summary>{t`Original completed account`}</summary>
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
              <Title order={3} size='h5'>{t`Attributed amendments`}</Title>
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
                      {t`Requested by`} {fact(row.requested_by_name)} ·{' '}
                      {when(row.created_at)} ·{' '}
                      {row.status === 'proposed'
                        ? t`Not yet decided`
                        : `${t`Decided by`} ${fact(row.decided_by_name)} · ${when(row.applied_at)}`}
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
      <Card withBorder data-testid='closeout-story'>
        <Stack gap='sm'>
          <Title order={2} size='h4'>{t`Technician story & insights`}</Title>
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
                  {t`Recorded by`} {fact(capture.created_by_name)} ·{' '}
                  {when(capture.created_at)}
                </Text>
                <Text
                  style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                >
                  {capture.narrative}
                </Text>
                {capture.source_type === 'typed' && (
                  <Text size='xs'>
                    {t`Voice unavailable reason`}:{' '}
                    {capture.voice_unavailable_reason ||
                      t`Not supplied for this capture`}
                  </Text>
                )}
                <OriginalMedia originals={capture.originals} />
                {!!capture.source_spans?.length && (
                  <details>
                    <summary>{t`Original source spans`}</summary>
                    <Stack gap='xs' mt='xs'>
                      {capture.source_spans.map((span) => (
                        <Stack
                          gap={3}
                          key={`${span.original_id}-${span.transcript_revision}`}
                          style={{ overflowWrap: 'anywhere' }}
                        >
                          <Text size='sm'>
                            {t`Original media`}:{' '}
                            {capture.originals?.find(
                              (original) => original.id === span.original_id
                            )?.filename ?? span.original_id}
                          </Text>
                          <Text size='xs'>
                            {t`Transcript revision`}: {span.transcript_revision}
                          </Text>
                          <Text size='xs'>
                            {span.segments.length
                              ? t`${span.segments.length} supplied transcript segments are retained with this source.`
                              : t`Audio segment timing was not supplied.`}
                          </Text>
                        </Stack>
                      ))}
                      <Text size='xs' c='dimmed'>
                        {t`Exact story ranges and supplied segment references are retained in the snapshot export. Story character positions are not audio times.`}
                      </Text>
                    </Stack>
                  </details>
                )}
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
                            {t`Revision`} {row.revision} · {t`Recorded by`}{' '}
                            {fact(row.created_by_name)} · {when(row.created_at)}
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
          >{t`Topic coverage and reviewed reusable lessons are not supplied. A story or extraction is not an approved procedure. Original media and supplied timing are shown with each saved story.`}</Text>
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
    </Stack>
  );
}
