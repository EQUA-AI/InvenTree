import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Divider,
  Group,
  Paper,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useEffect, useRef, useState } from 'react';
import { useApi } from '../../../contexts/ApiContext';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';

export function CloseoutReportDocument({
  reference,
  title,
  account,
  status,
  text,
  contentHash
}: Readonly<{
  reference: string;
  title: string;
  account: Record<string, unknown>;
  status: string;
  text?: string;
  contentHash?: string | null;
}>) {
  const labels: Record<string, string> = {
    cause: t`Cause / finding`,
    action: t`Action taken`,
    result: t`Observed result`,
    verification_summary: t`Verification recorded`,
    downtime_minutes: t`Downtime (minutes)`,
    follow_up_required: t`Follow-up required`,
    follow_up: t`Follow-up`
  };
  return (
    <Paper
      withBorder
      radius='sm'
      p={{ base: 'md', sm: 'xl' }}
      data-testid='closeout-report-document'
    >
      <Stack gap='lg'>
        <div
          style={{
            borderBottom: '3px solid var(--mantine-color-blue-7)',
            paddingBottom: 16
          }}
        >
          <Text size='xs' fw={700} c='blue' style={{ letterSpacing: 2 }}>
            EQUA AIMMS · {t`MAINTENANCE`}
          </Text>
          <Group justify='space-between' mt='xs'>
            <Title order={2}>{t`Service report`}</Title>
            <Badge variant='outline' tt='none'>
              {status}
            </Badge>
          </Group>
          <Text fw={600} mt='md'>
            {reference}
          </Text>
          <Text>{title}</Text>
        </div>
        <Alert color='blue'>{t`A report preview or download does not approve Work, establish operating permission, or change actual measurements.`}</Alert>
        {Object.entries(labels).map(([field, label]) => {
          const value = account[field];
          return (
            <div key={field}>
              <Text size='xs' c='dimmed' fw={600}>
                {label}
              </Text>
              <Text
                mt={4}
                style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
              >
                {value === null || value === undefined || value === ''
                  ? t`Not recorded`
                  : typeof value === 'boolean'
                    ? value
                      ? t`Yes`
                      : t`No`
                    : String(value)}
              </Text>
            </div>
          );
        })}
        {text && (
          <details>
            <summary>{t`Full account and source record`}</summary>
            <Text
              size='sm'
              style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
            >
              {text}
            </Text>
          </details>
        )}
        {contentHash && (
          <>
            <Divider />
            <Text size='xs' c='dimmed' style={{ overflowWrap: 'anywhere' }}>
              {t`Snapshot identity`}: {contentHash}
            </Text>
          </>
        )}
      </Stack>
    </Paper>
  );
}

export function ReportPDFButton({
  workId,
  version,
  source,
  reportId,
  account,
  disabled
}: Readonly<{
  workId: number;
  version?: number;
  source: 'draft' | 'submitted' | 'frozen';
  reportId?: number | null;
  account?: Record<string, unknown>;
  disabled?: boolean;
}>) {
  const api = useApi();
  const actor = useUserState((state) => state.user?.pk);
  const generation = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.hostKey);
  const boundary = JSON.stringify([
    actor,
    generation,
    host,
    workId,
    version,
    source,
    reportId
  ]);
  const active = useRef<string | null>(boundary);
  active.current = boundary;
  useEffect(() => {
    active.current = boundary;
    setBusy(false);
    setError(false);
    return () => {
      active.current = null;
    };
  }, [boundary]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const download = async () => {
    if (disabled || busy || !actor) return;
    const started = boundary;
    const current = () =>
      active.current === started &&
      useUserState.getState().authGeneration === generation &&
      useUserState.getState().user?.pk === actor &&
      useLocalState.getState().hostKey === host;
    setBusy(true);
    setError(false);
    try {
      const response =
        source === 'frozen'
          ? await api.get<Blob>(
              `/api/tasks/work-orders/${workId}/closeout/reports/${reportId}/pdf/`,
              { responseType: 'blob' }
            )
          : await api.post<Blob>(
              `/api/tasks/work-orders/${workId}/closeout/report.pdf`,
              {
                source,
                expected_version: version,
                ...(source === 'submitted'
                  ? { report_id: reportId }
                  : { account })
              },
              { responseType: 'blob' }
            );
      if (!current()) return;
      if ((await response.data.slice(0, 5).text()) !== '%PDF-')
        throw new Error('Invalid PDF');
      if (!current()) return;
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `work-${workId}-service-report${source === 'frozen' ? `-${reportId}` : ''}.pdf`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      if (current()) setError(true);
    } finally {
      if (current()) setBusy(false);
    }
  };
  return (
    <Stack gap='xs'>
      <Button
        disabled={disabled}
        loading={busy}
        onClick={() => void download()}
      >{t`Download PDF`}</Button>
      {error && (
        <Alert color='red'>{t`PDF could not be downloaded. Reload the current report and retry; no report was submitted.`}</Alert>
      )}
    </Stack>
  );
}
