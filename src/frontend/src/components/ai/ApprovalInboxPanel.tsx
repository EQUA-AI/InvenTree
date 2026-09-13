import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Paper,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../../App';
import { useVoiceDecisionState } from '../../states/VoiceDecisionState';
import { MailboxApprovalReview } from './MailboxApprovalReview';
import { matchesConfirmPhrase } from './confirmPhrase';

type Item = {
  id: string;
  summary: string;
  action_type: string;
  status: string;
  risk_tier: number;
};
type Review = Item & {
  current_revision_number: number;
  review_hash: string;
  payload: Record<string, unknown>;
  execution_result: Record<string, unknown> | null;
  review_sections: {
    id: string;
    label: string;
    text: string;
    required: boolean;
  }[];
};

/** All-tab screen review consumes the same sections, hashes and action endpoints. */
export function ApprovalInboxPanel({
  statuses,
  emptyText
}: Readonly<{ statuses: string[]; emptyText: string }>) {
  const client = useQueryClient();
  const decision = useVoiceDecisionState((state) => state.decision);
  const sessionId = useVoiceDecisionState((state) => state.sessionId);
  const [selected, setSelected] = useState<string | null>(null);
  const statusKey = statuses.join(',');
  const list = useQuery({
    queryKey: ['approval-inbox', statusKey],
    queryFn: async () => {
      const response = await api.get('/api/approvals/', {
        params: { status: statusKey, ordering: '-created_at' }
      });
      return (
        Array.isArray(response.data)
          ? response.data
          : (response.data.results ?? [])
      ) as Item[];
    },
    refetchInterval: sessionId ? 5000 : 30000
  });
  const review = useQuery({
    queryKey: ['approval-review', selected],
    queryFn: async () =>
      ({
        ...(await api.get(`/api/approvals/${selected}/card-package/`)).data,
        id: selected
      }) as Review,
    enabled: !!selected,
    refetchInterval: sessionId ? 5000 : 30000
  });
  useEffect(() => {
    const refresh = () => {
      void client.invalidateQueries({ queryKey: ['approval-inbox'] });
      void client.invalidateQueries({ queryKey: ['approval-review'] });
      void client.invalidateQueries({ queryKey: ['approval-count'] });
    };
    window.addEventListener('aimms:proposals-refresh', refresh);
    return () => window.removeEventListener('aimms:proposals-refresh', refresh);
  }, [client]);
  const matching =
    decision?.source_id === selected &&
    ['presented', 'executing'].includes(decision.state);
  return (
    <Stack p='md' data-testid='approval-inbox-panel'>
      <Group justify='space-between'>
        <Text fw={600}>{t`Approvals`}</Text>
        {selected && (
          <Button
            variant='subtle'
            onClick={() => setSelected(null)}
          >{t`Back`}</Button>
        )}
        <Button
          variant='subtle'
          onClick={() => {
            void list.refetch();
            if (selected) void review.refetch();
          }}
        >{t`Refresh`}</Button>
      </Group>
      {(list.isError || (selected && review.isError)) && (
        <Alert color='red'>{t`This approval is unavailable or your access changed. Refresh the inbox.`}</Alert>
      )}
      {(list.isLoading || (selected && review.isLoading)) && (
        <Text>{t`Loading review…`}</Text>
      )}
      {!selected && !list.isError && !list.isLoading && !list.data?.length && (
        <Text>{emptyText}</Text>
      )}
      {!selected &&
        list.data?.map((item) => (
          <Paper
            key={item.id}
            withBorder
            p='sm'
            data-testid='approval-inbox-row'
            data-source-id={item.id}
          >
            <Group justify='space-between'>
              <Button variant='subtle' onClick={() => setSelected(item.id)}>
                {item.summary}
              </Button>
              <Badge>{item.status}</Badge>
            </Group>
            {decision?.source_id === item.id && (
              <Text
                c='blue'
                size='sm'
              >{t`Focused in the shared decision card`}</Text>
            )}
          </Paper>
        ))}
      {selected &&
        !review.isError &&
        review.data &&
        (matching ? (
          <Alert data-testid='approval-shared-focus'>{t`This request is focused in the shared decision card above. Use its controls so voice and touch keep the same confirmation.`}</Alert>
        ) : review.data.action_type === 'email' &&
          review.data.payload._mailbox ? (
          <MailboxApprovalReview approvalId={selected} />
        ) : (
          <ApprovalScreenReview
            key={`${selected}:${review.data.review_hash}`}
            review={review.data}
            onChanged={() => {
              void client.invalidateQueries({ queryKey: ['approval-inbox'] });
              void client.invalidateQueries({ queryKey: ['approval-review'] });
              window.dispatchEvent(new Event('aimms:proposals-refresh'));
            }}
          />
        ))}
    </Stack>
  );
}

function ApprovalScreenReview({
  review,
  onChanged
}: { review: Review; onChanged: () => void }) {
  const [checked, setChecked] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [preview, setPreview] = useState<{
    action: string;
    phrase: string;
    reason: string;
  } | null>(null);
  const [phrase, setPhrase] = useState('');
  const form = useForm({
    initialValues: { reason: '' },
    validate: {
      reason: (value) =>
        value.trim().length > 0 && value.length <= 400
          ? null
          : t`Enter a reason of up to 400 characters.`
    }
  });
  const path = `/api/approvals/${review.id}/`;
  const focus = {
    revision: review.current_revision_number,
    review_hash: review.review_hash
  };
  const mutation = useMutation({
    mutationFn: async (action: string) => {
      const latest = (await api.get(`${path}card-package/`)).data as Review;
      if (latest.review_hash !== review.review_hash)
        throw new Error('Review changed');
      if (action === 'open') return api.post(`${path}open/`);
      if (action === 'confirm-viewed')
        return api.post(`${path}confirm-viewed/`, {
          ...focus,
          sections: review.review_sections
            .filter((section) => section.required)
            .map((section) => section.id)
        });
      if (!preview || !matchesConfirmPhrase(phrase, preview.phrase))
        throw new Error('Confirmation required');
      return api.post(`${path}${action}/`, {
        ...focus,
        reason: preview.reason,
        instructions: preview.reason
      });
    },
    onSuccess: (_response, action) => {
      setAcknowledged(action === 'confirm-viewed');
      setPreview(null);
      setPhrase('');
      onChanged();
    },
    onError: () => {
      setAcknowledged(false);
      setPreview(null);
      onChanged();
    }
  });
  const terminal = !['pending', 'in_review', 'changes_requested'].includes(
    review.status
  );
  const propose = (action: string, required: string) => {
    if (
      ['deny', 'request-changes'].includes(action) &&
      form.validate().hasErrors
    )
      return;
    setPhrase('');
    setPreview({ action, phrase: required, reason: form.values.reason.trim() });
  };
  return (
    <Stack data-testid='approval-screen-review'>
      <Text fw={600}>{review.summary}</Text>
      <Badge>{review.status}</Badge>
      {terminal && (
        <Alert
          color={review.status === 'succeeded' ? 'green' : 'blue'}
          data-testid='approval-recorded-outcome'
        >
          {review.status === 'succeeded'
            ? t`The action is recorded as succeeded.`
            : t`Current recorded status:`}{' '}
          {review.status}
          {review.execution_result && (
            <Text
              component='pre'
              size='sm'
              style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
            >
              {JSON.stringify(review.execution_result, null, 2)}
            </Text>
          )}
          {['approved', 'executing', 'failed'].includes(review.status) && (
            <Text>{t`Do not retry an unverified action. Refresh to check its recorded result.`}</Text>
          )}
        </Alert>
      )}
      {review.review_sections.map((section) => (
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
      {mutation.isError && (
        <Alert color='red'>{t`The decision was not verified. Refresh and review the current request before trying again.`}</Alert>
      )}
      {!terminal && review.action_type !== 'email' && (
        <>
          {['pending', 'changes_requested'].includes(review.status) && (
            <Button
              loading={mutation.isPending}
              onClick={() => mutation.mutate('open')}
            >{t`Open for review`}</Button>
          )}
          {review.status === 'in_review' && (
            <>
              <Checkbox
                checked={checked}
                onChange={(event) => setChecked(event.currentTarget.checked)}
                label={t`I have reviewed every required section of this request.`}
              />
              <Button
                disabled={!checked || mutation.isPending}
                onClick={() => mutation.mutate('confirm-viewed')}
              >{t`Confirm reviewed`}</Button>
              <Textarea
                label={t`Reason or requested changes`}
                {...form.getInputProps('reason')}
              />
              <Group>
                <Button
                  disabled={!acknowledged || mutation.isPending}
                  onClick={() =>
                    propose('approve', `approve ${review.id.slice(0, 8)}`)
                  }
                >{t`Prepare approval`}</Button>
                <Button
                  disabled={mutation.isPending}
                  color='red'
                  onClick={() => propose('deny', 'confirm rejection')}
                >{t`Prepare rejection`}</Button>
                <Button
                  disabled={mutation.isPending}
                  variant='light'
                  onClick={() =>
                    propose('request-changes', 'confirm request changes')
                  }
                >{t`Request changes`}</Button>
              </Group>
            </>
          )}
          <Button
            variant='subtle'
            disabled={mutation.isPending}
            onClick={() => propose('cancel', 'confirm cancel request')}
          >{t`Prepare cancellation`}</Button>
          {preview && (
            <Paper withBorder p='sm' data-testid='approval-action-preview'>
              <Stack>
                <Text>
                  {review.id.slice(0, 8)} · {preview.action}
                </Text>
                {preview.reason && <Text>{preview.reason}</Text>}
                <TextInput
                  label={t`Type the required confirmation phrase`}
                  placeholder={preview.phrase}
                  value={phrase}
                  onChange={(event) => setPhrase(event.currentTarget.value)}
                />
                <Button
                  disabled={!matchesConfirmPhrase(phrase, preview.phrase)}
                  loading={mutation.isPending}
                  onClick={() => mutation.mutate(preview.action)}
                >{t`Confirm decision`}</Button>
                <Button
                  variant='subtle'
                  onClick={() => setPreview(null)}
                >{t`Set aside`}</Button>
              </Stack>
            </Paper>
          )}
        </>
      )}
    </Stack>
  );
}
