import type { AcceptedReportSummary } from '@lib/types/Closeout';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Card,
  Group,
  Select,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { recordedTime } from './execution/ExecutionFacts';
import { workPanelParams } from './workPanelState';

export function AcceptedReportLinks({
  reports,
  title
}: Readonly<{ reports?: AcceptedReportSummary[]; title?: string }>) {
  const [params] = useSearchParams();
  if (!reports?.length) return null;
  const links = (
    <Stack gap={4}>
      {title && (
        <Text size='sm' fw={600}>
          {title}
        </Text>
      )}
      {reports.map((report) => {
        const next = new URLSearchParams(workPanelParams(params, 'closeout'));
        next.set('report', String(report.id));
        return (
          <Anchor
            component={Link}
            replace
            key={report.id}
            to={`?${next}#accepted-report`}
          >
            {t`Frozen report`} · {t`Revision`} {report.revision}
          </Anchor>
        );
      })}
    </Stack>
  );
  return title ? (
    <Card withBorder data-testid='accepted-report-links'>
      {links}
    </Card>
  ) : (
    links
  );
}

/** Frozen server payloads are deliberately separate from the current account. */
export function AcceptedReport({
  workId,
  reports
}: Readonly<{ workId: number; reports?: AcceptedReportSummary[] }>) {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const selected = params.has('report')
    ? reports?.find((row) => String(row.id) === params.get('report'))
    : reports?.[0];
  const actor = useUserState((state) => state.user?.pk);
  const identity = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.hostKey);
  const query = useQuery({
    queryKey: [
      'accepted-report',
      host,
      actor,
      identity,
      workId,
      selected?.id,
      selected?.content_hash
    ],
    enabled: !!selected,
    retry: false,
    queryFn: async () => {
      const { data } = await api.get(
        `/api/tasks/work-orders/${workId}/closeout/reports/${selected!.id}/`
      );
      if (
        data.id !== selected!.id ||
        data.content_hash !== selected!.content_hash ||
        typeof data.text !== 'string' ||
        typeof data.canonical_export !== 'string' ||
        !data.document
      )
        throw new Error('Report identity mismatch');
      return data as AcceptedReportSummary & {
        text: string;
        document: Record<string, unknown>;
        canonical_export: string;
      };
    }
  });
  const save = (json: boolean) => {
    if (!query.data || query.isError) return;
    const url = URL.createObjectURL(
      new Blob([json ? query.data.canonical_export : query.data.text], {
        type: json ? 'application/json' : 'text/plain;charset=utf-8'
      })
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `work-${workId}-frozen-report-${selected!.id}.${json ? 'json' : 'txt'}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <Card withBorder id='accepted-report' data-testid='accepted-report'>
      <Stack gap='sm'>
        <Title order={5}>{t`Frozen closeout report`}</Title>
        {!selected ? (
          <Text size='sm'>
            {reports === undefined
              ? t`Frozen report information is not supplied by this readout.`
              : params.has('report')
                ? t`The requested report is not listed for this Work.`
                : t`No frozen report is recorded for this earlier completion. Current supporting records cannot reconstruct a historical snapshot.`}
          </Text>
        ) : (
          <>
            <Text size='sm'>{t`The original snapshot is frozen at completion with its account sources. Amendments create attributed revisions; they do not change earlier reports.`}</Text>
            <Select
              label={t`Report revision`}
              value={String(selected.id)}
              data={reports!.map((row) => ({
                value: String(row.id),
                label: `${t`Revision`} ${row.revision} · ${row.frozen_by_name}`
              }))}
              onChange={(value) => {
                const next = new URLSearchParams(params);
                if (value) next.set('report', value);
                setParams(next);
              }}
            />
            <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
              {t`SHA-256 of verifiable snapshot (.json)`}:{' '}
              {selected.content_hash}
            </Text>
            <Text
              size='xs'
              c='dimmed'
            >{t`The hash covers the exact UTF-8 canonical JSON download containing both document and text.`}</Text>
            <Text size='sm'>
              {t`Completed by`} {selected.completed_by_name} ·{' '}
              {recordedTime(selected.completed_at)}
            </Text>
            {selected.amendment_id !== null && (
              <Text size='sm'>
                {t`Revision recorded by`} {selected.frozen_by_name} ·{' '}
                {recordedTime(selected.frozen_at)}
              </Text>
            )}
            {!query.isError &&
            query.data?.verified_by &&
            query.data.verified_at ? (
              <Text size='sm'>
                {t`Verified by`}{' '}
                {query.data.verified_by_name ?? t`Name not recorded`} ·{' '}
                {recordedTime(query.data.verified_at)}
              </Text>
            ) : (
              <Text size='sm'>
                {query.isError || query.isPending
                  ? t`Acceptance information unavailable`
                  : t`Acceptance: Not recorded`}
              </Text>
            )}
            {query.isError ? (
              <Alert color='red'>
                {t`The frozen report could not be read. No cached report is shown.`}
                <Button
                  variant='subtle'
                  onClick={() => void query.refetch()}
                >{t`Retry report`}</Button>
              </Alert>
            ) : query.isPending ? (
              <Text>{t`Loading frozen report…`}</Text>
            ) : (
              <details>
                <summary>{t`Read frozen report`}</summary>
                <Text
                  component='pre'
                  size='sm'
                  style={{
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                    fontFamily: 'inherit'
                  }}
                >
                  {query.data?.text}
                </Text>
              </details>
            )}
            <Group>
              <Button
                variant='default'
                disabled={!query.data || query.isError}
                onClick={() => save(false)}
              >{t`Download report text (.txt)`}</Button>
              <Button
                variant='subtle'
                disabled={!query.data || query.isError}
                onClick={() => save(true)}
              >{t`Download verifiable JSON`}</Button>
            </Group>
          </>
        )}
      </Stack>
    </Card>
  );
}
