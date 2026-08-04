import { t } from '@lingui/core/macro';
import { Badge, Button, Group, Stack, Text, Tooltip } from '@mantine/core';
import { IconKeyboard, IconUserCheck } from '@tabler/icons-react';
import { useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  EvidenceRelation,
  PreliminaryResults
} from '@lib/types/MachineHealth';
import { useApi } from '../../../contexts/ApiContext';
import PreliminaryResultsPanel from '../../assets/health/PreliminaryResults';

function relationOf(value: unknown): EvidenceRelation {
  return value === 'supports' || value === 'contradicts' ? value : 'unknown';
}

/**
 * Fill v2 defaults so blobs written before the schema upgrade (or by older
 * generators) render instead of crashing on a missing provenance section.
 * Mirrors `repair.schema.coerce_diagnosis` on the server.
 */
function normalizeDiagnosis(raw: unknown): PreliminaryResults | null {
  if (
    !raw ||
    typeof raw !== 'object' ||
    Object.keys(raw as object).length === 0
  ) {
    return null;
  }
  const blob = raw as Record<string, any>;
  const evidence = Array.isArray(blob.evidence)
    ? blob.evidence.map((item: any) =>
        item && typeof item === 'object'
          ? {
              snapshot_id: item.snapshot_id ?? null,
              observation: String(item.observation ?? ''),
              relation: relationOf(item.relation),
              signal_label: item.signal_label,
              observed_at: item.observed_at ?? null,
              stale: item.stale
            }
          : {
              snapshot_id: null,
              observation: String(item),
              relation: relationOf(null)
            }
      )
    : [];
  return {
    status: blob.status ?? (blob.likely_cause ? 'available' : 'unavailable'),
    authority: blob.authority ?? 'derived',
    authority_source: blob.authority_source ?? null,
    likely_cause: String(blob.likely_cause ?? ''),
    failure_mode: blob.failure_mode ?? null,
    confidence: Number(blob.confidence ?? 0),
    confidence_label: String(blob.confidence_label ?? 'unknown'),
    alternatives: Array.isArray(blob.alternatives) ? blob.alternatives : [],
    evidence,
    confirm_tests: Array.isArray(blob.confirm_tests) ? blob.confirm_tests : [],
    data_window: blob.data_window ?? {
      start: null,
      end: null,
      snapshot_count: 0
    },
    freshness: blob.freshness ?? { stale: false, stale_signal_count: 0 },
    quality: blob.quality ?? { summary: 'unknown', bad_signal_count: 0 },
    provider: String(blob.provider ?? blob.generator ?? ''),
    model_or_rule_version: String(blob.model_or_rule_version ?? ''),
    generated_at: blob.generated_at ?? null,
    verified_by_user: Boolean(blob.verified_by_user),
    verified_at: blob.verified_at ?? null,
    amendments: Array.isArray(blob.amendments) ? blob.amendments : [],
    schema_version: Number(blob.schema_version ?? 1)
  };
}

/**
 * Provenance-first diagnosis tab for a repair packet (execution-plan S10).
 *
 * Replaces the raw JSON dump: a preliminary banner until a technician
 * verifies, the declared confidence, evidence citations - and an explicit
 * chip when the content came from the offline keyword fallback, which never
 * analyzed anything and must not read as an AI conclusion.
 */
export function DiagnosisPanel({
  packetId,
  diagnosis,
  onRefresh
}: Readonly<{
  packetId: number | undefined;
  diagnosis: unknown;
  onRefresh: () => void;
}>) {
  const api = useApi();
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const results = useMemo(() => normalizeDiagnosis(diagnosis), [diagnosis]);
  const generator = String((diagnosis as Record<string, any>)?.generator ?? '');

  if (!results || !results.likely_cause) {
    return <Text c='dimmed'>{t`No data yet.`}</Text>;
  }

  const verify = () => {
    if (!packetId) {
      return;
    }
    setVerifying(true);
    setVerifyError(null);
    api
      .post(apiUrl(ApiEndpoints.repair_packet_verify_diagnosis, packetId), {})
      .then(() => onRefresh())
      .catch((error) => {
        setVerifyError(error?.response?.data?.detail || t`Verification failed`);
      })
      .finally(() => setVerifying(false));
  };

  return (
    <Stack gap='sm'>
      <Group gap='xs' justify='space-between' wrap='wrap'>
        <Group gap='xs'>
          {generator === 'heuristic' && (
            <Tooltip
              label={t`This content was produced by an offline keyword match over the fault text. No machine data was read and no AI analysis ran.`}
            >
              <Badge
                color='gray'
                variant='filled'
                leftSection={<IconKeyboard size={14} />}
                data-testid='diagnosis-fallback-chip'
              >
                {t`Offline keyword fallback — not analyzed`}
              </Badge>
            </Tooltip>
          )}
        </Group>
        {!results.verified_by_user && (
          <Button
            size='xs'
            variant='light'
            leftSection={<IconUserCheck size={16} />}
            loading={verifying}
            onClick={verify}
            data-testid='diagnosis-verify-button'
          >
            {t`Verify diagnosis`}
          </Button>
        )}
      </Group>
      {verifyError && (
        <Text size='sm' c='red'>
          {verifyError}
        </Text>
      )}
      <PreliminaryResultsPanel results={results} />
    </Stack>
  );
}

export default DiagnosisPanel;
