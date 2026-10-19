import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { t } from '@lingui/core/macro';
import { Alert, Anchor, Loader, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import type { FaultOccurrence } from '../faults/occurrenceModel';

/** Re-authorize linked references. Query parameters are navigation, not proof. */
export function LinkedEvidence({ machineId }: Readonly<{ machineId: number }>) {
  const [params] = useSearchParams();
  const raw = params.get('occurrence');
  const id =
    raw &&
    /^\d+$/.test(raw) &&
    Number.isSafeInteger(Number(raw)) &&
    Number(raw) > 0
      ? Number(raw)
      : null;
  const snapshotId = params.get('snapshot');
  const api = useApi();
  const query = useQuery<FaultOccurrence>({
    queryKey: ['machine-fault-occurrence', machineId, id],
    enabled: id !== null,
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get(
          apiUrl(ApiEndpoints.asset_machine_fault_occurrence, machineId, {
            occurrence: id!
          }),
          { signal }
        )
      ).data
  });
  if (!raw && !snapshotId) return null;
  if (!id || query.isError)
    return (
      <Alert color='yellow'>{t`Linked retained evidence unavailable or not permitted. The source history is not a substitute for that evidence.`}</Alert>
    );
  if (query.isPending || query.isFetching)
    return <Loader size='xs' aria-label={t`Loading linked evidence`} />;
  const occurrence = query.data;
  if (occurrence.id !== id)
    return (
      <Alert color='yellow'>{t`Linked evidence identity did not match. No retained reference is displayed.`}</Alert>
    );
  const snapshot = occurrence.snapshots.find((item) => item.id === snapshotId);
  return (
    <Stack
      gap={4}
      data-testid='linked-evidence'
      style={{ overflowWrap: 'anywhere' }}
    >
      <Anchor
        size='sm'
        href={`/machines/machine/${machineId}/fault-history/?occurrence=${occurrence.id}`}
      >
        {t`Retained occurrence ${occurrence.id}`}: {occurrence.title}
      </Anchor>
      <Text size='xs'>{t`This reference identifies retained evidence; charts below are new source reads for the selected window.`}</Text>
      {snapshotId && !snapshot && (
        <Alert color='yellow'>{t`The selected snapshot is not retained or not permitted. No other snapshot was substituted.`}</Alert>
      )}
      {snapshot && (
        <Text size='xs'>
          {t`Retained snapshot`}: {snapshot.id} ·{' '}
          {snapshot.source_name ?? t`Source not recorded`} · {t`Captured`}:{' '}
          {snapshot.captured_at} · {t`Retained window`}:{' '}
          {snapshot.window_start ?? t`Not recorded`} —{' '}
          {snapshot.window_end ?? t`Not recorded`} · {t`Content hash`}:{' '}
          {snapshot.content_hash || t`Not supplied`}. {t`Quality at capture`}:{' '}
          {snapshot.quality} ·{' '}
          {snapshot.stale ? t`Stale at capture` : t`Not stale at capture`}.{' '}
          {t`Quality and staleness belong to capture time.`}
        </Text>
      )}
    </Stack>
  );
}
