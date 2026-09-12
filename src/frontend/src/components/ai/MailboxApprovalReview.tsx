import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../App';

type Review = {
  status: string;
  current_revision_number: number;
  review_hash: string;
  review_sections: {
    id: string;
    label: string;
    text: string;
    required: boolean;
  }[];
  payload: {
    to: string[];
    cc: string[];
    bcc: string[];
    subject: string;
    body: string;
    attachments?: { id: string; filename: string }[];
    _mailbox?: { account_id: string };
    [key: string]: unknown;
  };
};

export function MailboxApprovalReview({ approvalId }: { approvalId: string }) {
  const path = `/api/approvals/${approvalId}/`;
  const [acknowledged, setAcknowledged] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const review = useQuery({
    queryKey: ['mailbox-review', approvalId],
    queryFn: async () => (await api.get(`${path}card-package/`)).data as Review,
    refetchInterval: 10000
  });
  const detail = useQuery({
    queryKey: ['mailbox-operation', approvalId],
    queryFn: async () => (await api.get(path)).data,
    refetchInterval: 5000
  });
  const download = useMutation({
    mutationFn: async (attachment: { id: string; filename: string }) => {
      const accountId = review.data?.payload._mailbox?.account_id;
      if (!accountId) throw new Error('Mailbox unavailable');
      const response = await api.get(
        `/api/aichat/email/accounts/${accountId}/attachments/${attachment.id}/`,
        { responseType: 'blob' }
      );
      const url = URL.createObjectURL(response.data);
      try {
        const link = document.createElement('a');
        link.href = url;
        link.download = attachment.filename;
        link.click();
      } finally {
        URL.revokeObjectURL(url);
      }
    }
  });
  const decision = useMutation({
    mutationFn: async (action: string) => {
      if (!review.data) return;
      if (action === 'approve') {
        if (
          review.data.status === 'pending' ||
          review.data.status === 'changes_requested'
        )
          await api.post(`${path}open/`);
        await api.post(`${path}confirm-viewed/`, {
          revision: review.data.current_revision_number,
          review_hash: review.data.review_hash,
          sections: review.data.review_sections
            .filter((section) => section.required)
            .map((section) => section.id)
        });
        return api.post(`${path}approve/`);
      }
      return api.post(`${path}cancel/`, { reason: t`Canceled by reviewer` });
    },
    onSuccess: () => {
      setAcknowledged(null);
      void review.refetch();
      void detail.refetch();
    }
  });
  const state = detail.data?.execution_result?.execution_state;
  const stateLabel =
    state === 'succeeded'
      ? t`Accepted by the mail provider. Recipient delivery is not confirmed.`
      : state === 'partial'
        ? t`Some recipients were accepted. Do not resend this operation.`
        : state === 'unknown' || state === 'submitting'
          ? t`Submission is unverified. Do not resend this operation.`
          : state === 'failed_before_effect'
            ? t`No recipients were accepted.`
            : t`Waiting for dispatch.`;
  if (review.isError || detail.isError)
    return (
      <Alert color='red'>{t`This approval is no longer accessible.`}</Alert>
    );
  if (!review.data) return <Text>{t`Loading review…`}</Text>;
  const canDecide = ['pending', 'in_review', 'changes_requested'].includes(
    review.data.status
  );
  return (
    <Stack>
      {review.data.review_sections.map((section) => (
        <Stack key={section.id} gap={2}>
          <Text fw={600}>{section.label}</Text>
          <Text
            size='sm'
            style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
          >
            {section.text}
          </Text>
        </Stack>
      ))}
      {review.data.payload.attachments?.map((attachment) => (
        <Button
          key={attachment.id}
          variant='light'
          loading={download.isPending}
          onClick={() => download.mutate(attachment)}
        >
          {t`Download attachment`}: {attachment.filename}
        </Button>
      ))}
      {download.isError && (
        <Alert color='red'>{t`Attachment is unavailable. Refresh this review and check mailbox access.`}</Alert>
      )}
      {state && (
        <Alert color={state === 'succeeded' ? 'green' : 'yellow'}>
          {stateLabel}
        </Alert>
      )}
      {detail.data?.execution_result?.receipt_id && (
        <Text size='xs'>
          {t`Receipt`}: {detail.data.execution_result.receipt_id}
        </Text>
      )}
      {decision.isError && (
        <Alert color='red'>{t`The approval could not proceed. Refresh and review the current revision and your mailbox permissions.`}</Alert>
      )}
      {canDecide && (
        <>
          <Checkbox
            checked={acknowledged === review.data.review_hash}
            onChange={(event) =>
              setAcknowledged(
                event.currentTarget.checked ? review.data.review_hash : null
              )
            }
            label={t`I have reviewed the sender, every recipient, full message and attachments.`}
          />
          <Group>
            <Button
              disabled={acknowledged !== review.data.review_hash}
              loading={decision.isPending}
              onClick={() => decision.mutate('approve')}
            >{t`Approve email submission`}</Button>
            <Button
              variant='light'
              onClick={() => {
                setAcknowledged(null);
                setEditing(!editing);
              }}
            >{t`Revise draft`}</Button>
            <Button
              color='red'
              variant='light'
              onClick={() => decision.mutate('cancel')}
            >{t`Cancel draft`}</Button>
          </Group>
          {editing && (
            <Revision
              key={review.data.review_hash}
              path={path}
              review={review.data}
              onSaved={() => {
                setEditing(false);
                void review.refetch();
              }}
            />
          )}
        </>
      )}
    </Stack>
  );
}

function Revision({
  path,
  review,
  onSaved
}: { path: string; review: Review; onSaved: () => void }) {
  const form = useForm({
    initialValues: {
      to: review.payload.to.join(', '),
      cc: review.payload.cc.join(', '),
      bcc: review.payload.bcc.join(', '),
      subject: review.payload.subject,
      body: review.payload.body
    }
  });
  const save = useMutation({
    mutationFn: async () => {
      if (review.status === 'pending') await api.post(`${path}open/`);
      return api.post(`${path}revise/`, {
        expected_revision: review.current_revision_number,
        payload: { ...review.payload, ...form.values }
      });
    },
    onSuccess: onSaved
  });
  return (
    <form onSubmit={form.onSubmit(() => save.mutate())}>
      <Stack gap='xs'>
        <TextInput label={t`To`} {...form.getInputProps('to')} />
        <TextInput label={t`CC`} {...form.getInputProps('cc')} />
        <TextInput label={t`BCC`} {...form.getInputProps('bcc')} />
        <TextInput label={t`Subject`} {...form.getInputProps('subject')} />
        <Textarea
          label={t`Full message`}
          autosize
          minRows={5}
          {...form.getInputProps('body')}
        />
        <Button
          type='submit'
          loading={save.isPending}
        >{t`Save revision for new review`}</Button>
        {save.isError && (
          <Alert color='red'>{t`Revision could not be saved. Refresh the current draft.`}</Alert>
        )}
      </Stack>
    </form>
  );
}
