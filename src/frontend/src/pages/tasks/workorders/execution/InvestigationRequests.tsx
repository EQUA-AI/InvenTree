import type {
  InvestigationRequest,
  InvestigationRequests as Requests
} from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Alert, Badge, Group, List, Paper, Stack, Text } from '@mantine/core';
import { useSearchParams } from 'react-router-dom';
import { ContinueInvestigation } from './ContinueInvestigation';

function statusLabel(status: InvestigationRequest['status']): string {
  return (
    {
      open: t`Open`,
      answered: t`Evidence received — decision unresolved`,
      cannot_perform: t`Cannot perform — unresolved`,
      pending_external: t`Pending external — internal coordinator`,
      disputed: t`Disputed — unresolved`,
      resolved: t`Resolved`
    }[status] ?? t`Status not recorded`
  );
}
function kindLabel(kind: string): string {
  return (
    (
      {
        observation: t`Observation`,
        reading_set: t`Reading set`,
        photo: t`Photo`,
        report: t`Report`,
        laboratory: t`Laboratory result`,
        check_result: t`Check result`
      } as Record<string, string>
    )[kind] ?? kind
  );
}
function factLabel(key: string): string {
  return (
    (
      {
        sample: t`Sample`,
        method: t`Method`,
        observed_at: t`Observed at`,
        source: t`Source`,
        status: t`Result status`,
        results: t`Results`,
        readings: t`Readings`,
        conditions: t`Conditions`,
        description: t`Description`,
        text: t`Observation`
      } as Record<string, string>
    )[key] ?? key
  );
}

export function InvestigationRequests({
  requests,
  workOrder
}: Readonly<{ requests: Requests; workOrder: WorkOrderOverview }>) {
  const [params] = useSearchParams();
  if (!requests.available)
    return (
      <Alert
        color='gray'
        title={t`No saved investigation request`}
      >{t`This server keeps no investigation request with an owner, status or due time, so there is no request queue to show. Checks are agreed in the assistant.`}</Alert>
    );
  if (!Array.isArray(requests.items))
    return (
      <Alert
        color='orange'
        title={t`Request queue unavailable`}
      >{t`The server did not supply the saved queue. Refresh the Work before continuing.`}</Alert>
    );
  const items = requests.items;
  const unresolved = items.filter((r) => r.status !== 'resolved');
  const selected = items.find(
    (r) => String(r.id) === params.get('investigation_request')
  );
  const priority = selected ?? unresolved.find((r) => r.priority != null);
  return (
    <Stack gap='sm' data-testid='investigation-requests'>
      {priority && (
        <Alert
          color={
            ['cannot_perform', 'disputed', 'pending_external'].includes(
              priority.status
            )
              ? 'orange'
              : 'blue'
          }
          title={
            selected
              ? t`Selected request ${priority.id}`
              : t`Priority next check · Request ${priority.id}`
          }
        >
          <Stack gap={4}>
            <Text fw={600} size='sm'>
              {priority.next_check}
            </Text>
            <Text size='sm'>
              {t`Decision`}: {priority.purpose}
            </Text>
            <ContinueInvestigation workOrder={workOrder} request={priority} />
          </Stack>
        </Alert>
      )}
      {!items.length && (
        <Text
          size='sm'
          c='dimmed'
        >{t`No investigation requests have been saved for this Work. Proposed checks below remain proposals.`}</Text>
      )}
      {items.length > 0 && (
        <Text
          size='sm'
          fw={600}
        >{t`Saved request queue (${unresolved.length} unresolved)`}</Text>
      )}
      {items.map((r) => (
        <Paper
          key={r.id}
          withBorder
          p='sm'
          radius='sm'
          id={`investigation-request-${r.id}`}
          data-testid={`investigation-request-${r.id}`}
          style={{ overflowWrap: 'anywhere' }}
        >
          <Stack gap='xs'>
            <Group gap='xs'>
              <Text size='sm' fw={600}>
                {t`Request ${r.id}`} · v{r.version}
              </Text>
              <Badge
                color={
                  r.status === 'resolved'
                    ? 'gray'
                    : [
                          'disputed',
                          'cannot_perform',
                          'pending_external'
                        ].includes(r.status)
                      ? 'orange'
                      : 'blue'
                }
                variant='light'
                styles={{
                  root: { height: 'auto' },
                  label: { whiteSpace: 'normal' }
                }}
              >
                {statusLabel(r.status)}
              </Badge>
            </Group>
            <Text size='sm' fw={600}>
              {r.next_check}
            </Text>
            <Text size='sm'>
              {t`Decision purpose`}: {r.purpose}
            </Text>
            <Text size='sm'>
              {t`Owner`}: {r.owner_name || t`Not recorded`} ·{' '}
              {t`Next checkpoint`}: {r.next_checkpoint || t`Not recorded`}
            </Text>
            <Text size='sm'>
              {t`Priority`}: {r.priority ?? t`Not recorded`} · {t`Saved`}:{' '}
              {r.updated_at} · {t`Updated by`}: {r.updated_by}
            </Text>
            {r.required_artifacts.length > 0 ? (
              <List size='sm' spacing={3}>
                {r.required_artifacts.map((a, i) => (
                  <List.Item key={`${r.id}-${i}`}>
                    {kindLabel(a.kind)} · {a.description} · {t`Unit`}:{' '}
                    {a.unit || t`Not recorded`} · {t`Conditions`}:{' '}
                    {a.conditions || t`Not recorded`}
                  </List.Item>
                ))}
              </List>
            ) : (
              <Text
                size='sm'
                c='dimmed'
              >{t`Required artifacts and conditions not recorded`}</Text>
            )}
            {r.blocked_reason && (
              <Alert color='orange' title={statusLabel(r.status)}>
                <Text size='sm'>{r.blocked_reason}</Text>
                <Text size='xs'>{t`This decision remains unresolved until addressed. Other findings and unrelated work retain their own status.`}</Text>
              </Alert>
            )}
            {r.source_finding && (
              <Text size='sm'>
                {t`Source finding`}: {r.source_finding}
              </Text>
            )}
            {r.source_proposal && (
              <Text size='sm'>
                {t`Source proposal`}: {r.source_proposal.check} ·{' '}
                {r.source_proposal.generated_at || t`Time not recorded`}
              </Text>
            )}
            {r.resolution && (
              <Text size='sm'>
                {t`Recorded resolution`}: {r.resolution}
              </Text>
            )}
            {r.artifacts.map((a) => (
              <Paper
                key={a.id}
                withBorder
                p='xs'
                data-testid={`investigation-artifact-${a.id}`}
              >
                <Stack gap={2}>
                  <Text size='sm' fw={600}>
                    {t`Evidence ${a.id}`} · {kindLabel(a.kind)}
                    {a.supersedes
                      ? ` · ${t`Corrects evidence ${a.supersedes}`}`
                      : ''}
                  </Text>
                  {Object.entries(a.payload).map(([key, value]) => (
                    <Text key={key} size='sm'>
                      <Text span fw={500}>
                        {factLabel(key)}:{' '}
                      </Text>
                      {Array.isArray(value)
                        ? value
                            .map((v) =>
                              typeof v === 'object' && v
                                ? `${String(v.name ?? '')}: ${v.value === null || v.value === undefined ? t`Not recorded` : String(v.value)} ${String(v.unit ?? '')}`
                                : String(v)
                            )
                            .join('; ')
                        : value === null
                          ? t`Not recorded`
                          : String(value)}
                    </Text>
                  ))}
                  <Text size='sm'>
                    {a.recorded_by_name || t`Recorder not recorded`} ·{' '}
                    {a.created_at}
                    {a.attachment
                      ? ` · ${t`Work attachment ${a.attachment}`}`
                      : ''}
                  </Text>
                </Stack>
              </Paper>
            ))}
            <ContinueInvestigation
              workOrder={workOrder}
              request={r}
              caption={false}
              variant='light'
              label={t`Discuss request ${r.id}`}
            />
          </Stack>
        </Paper>
      ))}
      {items.length > 0 && (
        <Text
          size='xs'
          c='dimmed'
        >{t`Saved evidence informs a decision; it does not establish physical readiness or resolve a request automatically. Typed evidence is shown only after a confirmed save.`}</Text>
      )}
    </Stack>
  );
}
