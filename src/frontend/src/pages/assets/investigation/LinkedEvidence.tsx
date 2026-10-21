import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { t } from '@lingui/core/macro';
import { Loader, Stack } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { displayInstant } from '../displayClock';
import type { FaultOccurrence } from '../faults/occurrenceModel';
import { AttentionAlert, MetaText, MonitoringLink } from '../health/common';

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
      <AttentionAlert>{t`Linked retained evidence unavailable or not permitted. The source history is not a substitute for that evidence.`}</AttentionAlert>
    );
  if (query.isPending || query.isFetching)
    return <Loader size='xs' aria-label={t`Loading linked evidence`} />;
  const occurrence = query.data;
  if (occurrence.id !== id)
    return (
      <AttentionAlert>{t`Linked evidence identity did not match. No retained reference is displayed.`}</AttentionAlert>
    );
  const snapshot = occurrence.snapshots.find((item) => item.id === snapshotId);
  return (
    <Stack
      gap={4}
      data-testid='linked-evidence'
      style={{ overflowWrap: 'anywhere' }}
    >
      <MonitoringLink
        component={Link}
        size='sm'
        to={`/machines/machine/${machineId}/fault-history/?${new URLSearchParams({ occurrence: String(occurrence.id), ...(snapshotId ? { snapshot: snapshotId } : {}) })}`}
      >
        {t`Retained occurrence ${occurrence.id}`}: {occurrence.title}
      </MonitoringLink>
      <MetaText size='sm'>{t`This reference identifies retained evidence; charts below are new source reads for the selected window.`}</MetaText>
      {snapshotId && !snapshot && (
        <AttentionAlert>{t`The selected snapshot is not retained or not permitted. No other snapshot was substituted.`}</AttentionAlert>
      )}
      {snapshot && (
        <MetaText size='sm'>
          {t`Retained snapshot`}: {snapshot.id} ·{' '}
          {snapshot.source_name ?? t`Source not recorded`} · {t`Captured`}:{' '}
          {displayInstant(snapshot.captured_at) ?? t`Time unavailable`} ·{' '}
          {t`Retained window`}:{' '}
          {displayInstant(snapshot.window_start) ?? t`Time unavailable`} —{' '}
          {displayInstant(snapshot.window_end) ?? t`Time unavailable`} ·{' '}
          {t`Content hash`}: {snapshot.content_hash || t`Not supplied`}.{' '}
          {t`Quality at capture`}: {snapshot.quality} ·{' '}
          {snapshot.stale ? t`Stale at capture` : t`Not stale at capture`}.{' '}
          {t`Quality and staleness belong to capture time.`}
        </MetaText>
      )}
    </Stack>
  );
}
