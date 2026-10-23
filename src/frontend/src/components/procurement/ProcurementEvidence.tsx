import type { ProcurementEvidence } from '@lib/types/Procurement';
import { t } from '@lingui/core/macro';
import {
  Anchor,
  Group,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../App';
import { generateUrl } from '../../functions/urls';
import { MissingValue } from '../items/ValueTooltip';

type Attachment = {
  pk: number;
  filename: string;
  attachment: string | null;
  model_type: string;
  model_id: number;
};
const records = (data: Attachment[] | { results: Attachment[] }) =>
  Array.isArray(data) ? data : data.results;

export function useProcurementAttachments(workId: number, orderId?: number) {
  return useQuery({
    queryKey: ['procurement-attachments', workId, orderId],
    retry: false,
    queryFn: async ({ signal }) => {
      const owners = [
        { model_type: 'workorder', model_id: workId },
        ...(orderId ? [{ model_type: 'purchaseorder', model_id: orderId }] : [])
      ];
      const lists = await Promise.all(
        owners.map(async (params) =>
          records(
            (
              await api.get('/api/attachment/', {
                params: { ...params, limit: 500 },
                signal
              })
            ).data
          ).filter(
            (item) =>
              item.model_type === params.model_type &&
              item.model_id === params.model_id
          )
        )
      );
      return Array.from(
        new Map(lists.flat().map((item) => [item.pk, item])).values()
      );
    },
    enabled: workId > 0
  });
}

/** Uses the existing authorized attachment URL. IDs never become guessed URLs. */
export function EvidenceLink({
  evidence,
  workId,
  orderId,
  label
}: Readonly<{
  evidence: ProcurementEvidence;
  workId: number;
  orderId?: number;
  label: string;
}>) {
  const query = useProcurementAttachments(workId, orderId);
  const [failed, setFailed] = useState(false);
  const [reading, setReading] = useState(false);
  const attachment = query.data?.find(
    (item) => item.pk === evidence.attachment_id
  );
  return attachment?.attachment ? (
    <Stack gap={2}>
      <Anchor
        size='sm'
        href={`${generateUrl(attachment.attachment)}#page=${evidence.page}`}
        target='_blank'
        rel='noopener noreferrer'
        title={`${evidence.revision} · SHA-256 ${evidence.sha256}`}
        aria-busy={reading}
        onClick={async (event) => {
          event.preventDefault();
          if (reading) return;
          const viewer = window.open('about:blank', '_blank');
          if (viewer) viewer.opener = null;
          setReading(true);
          setFailed(false);
          try {
            const bytes = (
              await api.get<ArrayBuffer>(generateUrl(attachment.attachment!), {
                responseType: 'arraybuffer'
              })
            ).data;
            const hash = Array.from(
              new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
              (byte) => byte.toString(16).padStart(2, '0')
            ).join('');
            if (hash !== evidence.sha256 || !viewer)
              throw new Error('Source unavailable or changed');
            const url = URL.createObjectURL(
              new Blob([bytes], { type: 'application/pdf' })
            );
            viewer.location.href = `${url}#page=${evidence.page}`;
            window.setTimeout(() => URL.revokeObjectURL(url), 60000);
          } catch {
            viewer?.close();
            setFailed(true);
          } finally {
            setReading(false);
          }
        }}
      >
        {label} · {t`p. ${evidence.page}`}
      </Anchor>
      {reading && <Text size='xs'>{t`Checking source…`}</Text>}
      {failed && (
        <Text size='sm' c='red'>{t`Source unavailable or changed`}</Text>
      )}
    </Stack>
  ) : query.isError ? (
    <Text size='sm' c='red'>{t`Document read failed`}</Text>
  ) : (
    <MissingValue
      reason={t`Original document is not available in the authorized attachments`}
      label={label}
      aria-label={label}
    />
  );
}

export const blankEvidence = (): ProcurementEvidence => ({
  attachment_id: 0,
  sha256: '',
  page: 1,
  revision: ''
});

/** Hash the selected retained PDF, rather than asking a person to type its hash. */
export function EvidenceFields({
  value,
  onChange,
  workId,
  orderId,
  label
}: Readonly<{
  value: ProcurementEvidence;
  onChange: (next: ProcurementEvidence) => void;
  workId: number;
  orderId?: number;
  label: string;
}>) {
  const query = useProcurementAttachments(workId, orderId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function select(id: string | null) {
    const item = query.data?.find((entry) => String(entry.pk) === id);
    onChange({ ...value, attachment_id: 0, sha256: '' });
    if (!item?.attachment) return;
    setBusy(true);
    setError('');
    try {
      const bytes = (
        await api.get<ArrayBuffer>(generateUrl(item.attachment), {
          responseType: 'arraybuffer'
        })
      ).data;
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      const hash = Array.from(new Uint8Array(digest), (byte) =>
        byte.toString(16).padStart(2, '0')
      ).join('');
      onChange({ ...value, attachment_id: item.pk, sha256: hash });
    } catch {
      setError(t`PDF could not be read. Select it again.`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Stack gap='xs'>
      <Select
        label={label}
        aria-label={label}
        required
        searchable
        disabled={busy || query.isPending}
        value={value.attachment_id ? String(value.attachment_id) : null}
        data={(query.data ?? [])
          .filter(
            (item) =>
              item.attachment && item.filename.toLowerCase().endsWith('.pdf')
          )
          .map((item) => ({ value: String(item.pk), label: item.filename }))}
        onChange={(id) => void select(id)}
        error={
          error ||
          (query.isError ? t`Attachments could not be read` : undefined)
        }
      />
      {busy && <Text size='sm'>{t`Reading PDF…`}</Text>}
      <Group grow align='flex-start'>
        <NumberInput
          label={t`Page`}
          disabled={busy}
          aria-label={`${label} · ${t`Page`}`}
          required
          min={1}
          allowDecimal={false}
          value={value.page}
          onChange={(page) => onChange({ ...value, page: Number(page) })}
        />
        <TextInput
          label={t`Document revision`}
          disabled={busy}
          aria-label={`${label} · ${t`Document revision`}`}
          required
          value={value.revision}
          onChange={(event) =>
            onChange({ ...value, revision: event.currentTarget.value })
          }
        />
      </Group>
    </Stack>
  );
}
