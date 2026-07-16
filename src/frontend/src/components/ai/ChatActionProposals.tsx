/**
 * Governed chat action proposals (WS7-T7).
 *
 * Renders durable server proposals from /api/aichat/proposals/ and lets the
 * user confirm or reject them. Confirmation is this explicit visual action —
 * never speech, transcripts, or model output — and the card shows the real
 * command receipt or failure code afterwards.
 */

import { Alert, Badge, Button, Card, Group, Stack, Text } from '@mantine/core';
import { IconClockPause, IconPlayerPlay } from '@tabler/icons-react';
import { useCallback, useEffect, useState } from 'react';

import { api } from '../../App';

export interface ChatActionProposalPayload {
  id: string;
  action_type: string;
  state: string;
  work_order_id: number;
  target_version: number;
  preview: {
    reference?: string;
    title?: string;
    current_status?: string;
    resulting_status?: string;
    warning?: string;
  };
  reason: string;
  expires_at: string;
  receipt: Record<string, unknown> | null;
  failure_code: string | null;
}

const STATE_COLORS: Record<string, string> = {
  proposed: 'blue',
  executed: 'teal',
  rejected: 'gray',
  expired: 'yellow',
  failed: 'red'
};

const ACTION_LABELS: Record<string, string> = {
  'work_order.hold': 'Hold work order',
  'work_order.resume': 'Resume work order'
};

function ProposalCard({
  proposal,
  onChanged
}: Readonly<{
  proposal: ChatActionProposalPayload;
  onChanged: () => void;
}>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = useCallback(
    async (verb: 'confirm' | 'reject') => {
      setBusy(true);
      setError(null);
      try {
        await api.post(`/api/aichat/proposals/${proposal.id}/${verb}/`);
      } catch (err: any) {
        setError(err?.response?.data?.error ?? 'PROPOSAL_REQUEST_FAILED');
      } finally {
        setBusy(false);
        onChanged();
      }
    },
    [proposal.id, onChanged]
  );

  const pending = proposal.state === 'proposed';
  const label = ACTION_LABELS[proposal.action_type] ?? proposal.action_type;
  const target = proposal.preview.reference || `WO-${proposal.work_order_id}`;

  return (
    <Card withBorder radius='md' p='sm' data-testid='chat-action-proposal'>
      <Stack gap={6}>
        <Group justify='space-between' wrap='nowrap'>
          <Group gap='xs' wrap='nowrap'>
            {proposal.action_type === 'work_order.hold' ? (
              <IconClockPause size={16} />
            ) : (
              <IconPlayerPlay size={16} />
            )}
            <Text fw={600} size='sm'>
              {label}: {target}?
            </Text>
          </Group>
          <Badge color={STATE_COLORS[proposal.state] ?? 'gray'} size='sm'>
            {proposal.state}
          </Badge>
        </Group>
        {proposal.preview.title && (
          <Text size='xs' c='dimmed'>
            {proposal.preview.title} — {proposal.preview.current_status} →{' '}
            {proposal.preview.resulting_status}
          </Text>
        )}
        {proposal.reason && (
          <Text size='xs' fs='italic'>
            “{proposal.reason}”
          </Text>
        )}
        <Text size='xs' c='orange'>
          {proposal.preview.warning ??
            'This does not change any safety status.'}
        </Text>
        {pending && (
          <Group gap='xs'>
            <Button
              size='xs'
              color='teal'
              loading={busy}
              onClick={() => void act('confirm')}
              data-testid='proposal-confirm'
            >
              Confirm
            </Button>
            <Button
              size='xs'
              variant='light'
              color='gray'
              disabled={busy}
              onClick={() => void act('reject')}
              data-testid='proposal-reject'
            >
              Dismiss
            </Button>
            <Text size='xs' c='dimmed'>
              expires {new Date(proposal.expires_at).toLocaleTimeString()}
            </Text>
          </Group>
        )}
        {proposal.state === 'executed' && proposal.receipt && (
          <Text size='xs' c='teal'>
            Executed: {String(proposal.receipt.command)} →{' '}
            {String(proposal.receipt.lifecycle_status)} (event #
            {String(proposal.receipt.event_id)})
          </Text>
        )}
        {proposal.failure_code && (
          <Text size='xs' c='red'>
            {proposal.failure_code}
          </Text>
        )}
        {error && (
          <Alert color='red' p={4}>
            <Text size='xs'>{error}</Text>
          </Alert>
        )}
      </Stack>
    </Card>
  );
}

export function ChatActionProposalList() {
  const [proposals, setProposals] = useState<ChatActionProposalPayload[]>([]);

  const refresh = useCallback(async () => {
    try {
      const response = await api.get('/api/aichat/proposals/');
      setProposals(response.data?.results ?? []);
    } catch {
      setProposals([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  if (proposals.length === 0) {
    return null;
  }

  return (
    <Stack gap='xs' data-testid='chat-action-proposals'>
      <Text size='sm' fw={600}>
        Action proposals
      </Text>
      {proposals.map((proposal) => (
        <ProposalCard
          key={proposal.id}
          proposal={proposal}
          onChanged={() => void refresh()}
        />
      ))}
    </Stack>
  );
}
