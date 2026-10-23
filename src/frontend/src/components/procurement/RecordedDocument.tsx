import type { ProcurementRecordedDocument } from '@lib/types/Procurement';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Select,
  Stack,
  TextInput,
  Textarea
} from '@mantine/core';
import { useState } from 'react';
import { api } from '../../App';
import { CommandNotice } from './CommandNotice';
import { useProcurementCommand } from './useProcurementCommand';

export function RecordedDocumentForm({
  orderId,
  onSaved
}: Readonly<{ orderId: number; onSaved: () => void }>) {
  const [to, setTo] = useState('');
  const [from, setFrom] = useState('');
  const [shipTo, setShipTo] = useState('');
  const [buyer, setBuyer] = useState('');
  const [reference, setReference] = useState('');
  const [provenance, setProvenance] = useState<string | null>(null);
  const command = useProcurementCommand(`document:${orderId}`, onSaved);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!provenance) return;
        void command.submit(
          `/api/order/po/${orderId}/recorded-evidence/`,
          'document',
          {
            to,
            from_address: from,
            ship_to: shipTo,
            buyer_name: buyer,
            internal_ref: reference,
            provenance_kind: provenance
          }
        );
      }}
    >
      <Stack gap='sm'>
        <CommandNotice command={command} />
        <TextInput
          label={t`Recipient`}
          type='email'
          required
          value={to}
          onChange={(event) => setTo(event.currentTarget.value)}
        />
        <TextInput
          label={t`Buyer email`}
          type='email'
          required
          value={from}
          onChange={(event) => setFrom(event.currentTarget.value)}
        />
        <TextInput
          label={t`Buyer name`}
          required
          value={buyer}
          onChange={(event) => setBuyer(event.currentTarget.value)}
        />
        <Textarea
          label={t`Delivery address`}
          required
          value={shipTo}
          onChange={(event) => setShipTo(event.currentTarget.value)}
        />
        <TextInput
          label={t`Internal reference`}
          value={reference}
          onChange={(event) => setReference(event.currentTarget.value)}
        />
        <Select
          label={t`Provenance`}
          required
          value={provenance}
          onChange={setProvenance}
          data={[
            { value: 'native_record', label: t`Native record` },
            { value: 'fixture', label: t`Representative fixture` }
          ]}
        />
        <Button
          type='submit'
          loading={command.pending}
          disabled={!!command.retained || !provenance}
        >{t`Record PDF and email · no send`}</Button>
      </Stack>
    </form>
  );
}

export function DocumentDownload({
  document
}: Readonly<{ document: ProcurementRecordedDocument }>) {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  async function download(email: boolean) {
    setPending(true);
    setFailed(false);
    try {
      const result = (
        await api.get(`/api/order/po/${document.order_id}/recorded-evidence/`, {
          params: { operation_key: document.operation_key }
        })
      ).data;
      if (
        result.operation_key !== document.operation_key ||
        result.attachment_state !== 'verified' ||
        result.pdf_sha256 !== document.pdf_sha256 ||
        result.email_sha256 !== document.email_sha256
      )
        throw new Error('Evidence mismatch');
      const bytes = Uint8Array.from(
        atob(email ? result.email_base64 : result.pdf_base64),
        (char) => char.charCodeAt(0)
      );
      const digest = Array.from(
        new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
        (byte) => byte.toString(16).padStart(2, '0')
      ).join('');
      if (digest !== (email ? document.email_sha256 : document.pdf_sha256))
        throw new Error('Hash mismatch');
      const url = URL.createObjectURL(
        new Blob([bytes], {
          type: email ? 'message/rfc822' : 'application/pdf'
        })
      );
      const link = window.document.createElement('a');
      link.href = url;
      link.download = `PO-${document.order_id}-${document.pk}.${email ? 'eml' : 'pdf'}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setFailed(true);
    } finally {
      setPending(false);
    }
  }
  return (
    <Stack gap={2} align='flex-start'>
      <Button
        size='compact-xs'
        variant='subtle'
        disabled={document.attachment_state !== 'verified'}
        loading={pending}
        onClick={() => void download(false)}
      >{t`Recorded PDF`}</Button>
      <Button
        size='compact-xs'
        variant='subtle'
        disabled={document.attachment_state !== 'verified'}
        loading={pending}
        onClick={() => void download(true)}
      >{t`Recorded email`}</Button>
      {(failed || document.attachment_state === 'corrupt') && (
        <Alert color='red' p='xs'>{t`Document verification failed`}</Alert>
      )}
    </Stack>
  );
}
