/**
 * Governed chat action proposals (WS7-T7).
 *
 * Renders durable server proposals from /api/aichat/proposals/ and lets the
 * user confirm or reject them. Confirmation is this explicit visual action —
 * never speech, transcripts, or model output — and the card shows the real
 * command receipt or failure code afterwards.
 *
 * Every governed action is rendered here, not just hold/resume: the card reads
 * the server-derived preview (never model text) and, for an irreversible action,
 * demands the exact strict phrase before Confirm is enabled — the same tier the
 * voice rail enforces verbally (§5.3).
 */

import { PROPOSAL_ACTION_LABELS } from '@lib/types/AimmsWire.generated';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import {
  IconBolt,
  IconClockPause,
  IconPlayerPlay,
  IconTrash
} from '@tabler/icons-react';
import { useCallback, useEffect, useState } from 'react';

import { api } from '../../App';
import { InlineMarkdown } from '../aichat/MarkdownMessage';

export interface ChatActionProposalPreview {
  action?: string;
  reference?: string;
  title?: string;
  current_status?: string;
  resulting_status?: string;
  warning?: string;
  irreversible?: boolean;
  confirm_phrase?: string;
  proposed_start?: string | null;
  proposed_end?: string | null;
  proposed_estimated_minutes?: number | null;
  proposed_assigned_to_id?: number | null;
  proposed_title?: string;
  candidate_count?: number;
  to_status?: string;
  [key: string]: unknown;
}

export interface ChatActionProposalPayload {
  id: string;
  action_type: string;
  state: string;
  work_order_id: number | null;
  target_version: number | null;
  intent?: Record<string, unknown>;
  preview: ChatActionProposalPreview;
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

// S43: labels are GENERATED from ProposalAction (all 16 actions, en
// labels) — the hand map silently missed repair_work_package.create.
const ACTION_LABELS: Record<string, string> = PROPOSAL_ACTION_LABELS;

function actionIcon(actionType: string) {
  if (actionType === 'work_order.hold') return <IconClockPause size={16} />;
  if (actionType === 'work_order.resume') return <IconPlayerPlay size={16} />;
  if (
    actionType === 'work_order.delete' ||
    actionType === 'dependency.delete'
  ) {
    return <IconTrash size={16} />;
  }
  return <IconBolt size={16} />;
}

/** A short, server-derived summary of what the proposal will do. */
function previewSummary(proposal: ChatActionProposalPayload): string | null {
  const p = proposal.preview;
  switch (proposal.action_type) {
    case 'work_order.schedule':
      return `${p.proposed_start ?? '—'} → ${p.proposed_end ?? '—'}`;
    case 'work_order.resize':
      return p.proposed_estimated_minutes != null
        ? `${p.proposed_estimated_minutes} min`
        : null;
    case 'work_order.assign':
      return `assignee → ${p.proposed_assigned_to_id ?? 'unassigned'}`;
    case 'work_order.create':
    case 'work_order.create_child':
      return p.proposed_title ? `“${p.proposed_title}”` : null;
    case 'schedule.optimize':
      return `${p.candidate_count ?? 0} work orders`;
    default:
      if (p.current_status && p.resulting_status) {
        return `${p.current_status} → ${p.resulting_status}`;
      }
      return p.resulting_status ?? null;
  }
}

/** A short, human line describing the recorded receipt of an executed action. */
function receiptSummary(receipt: Record<string, unknown>): string {
  const command = String(receipt.command ?? 'done');
  if (receipt.lifecycle_status != null) {
    return `${command} → ${String(receipt.lifecycle_status)} (event #${String(receipt.event_id)})`;
  }
  if (receipt.deletion_record_id != null) {
    return `${command} (record #${String(receipt.deletion_record_id)})`;
  }
  if (receipt.dependency_id != null) {
    return `${command} (dependency #${String(receipt.dependency_id)})`;
  }
  if (receipt.child_id != null) {
    return `${command} (child #${String(receipt.child_id)})`;
  }
  if (Array.isArray(receipt.applied)) {
    return `${command} (${receipt.applied.length} scheduled)`;
  }
  return command;
}

export function ProposalCard({
  proposal,
  onChanged
}: Readonly<{
  proposal: ChatActionProposalPayload;
  onChanged: () => void;
}>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phrase, setPhrase] = useState('');

  const requiredPhrase = proposal.preview.confirm_phrase ?? '';
  const irreversible =
    Boolean(proposal.preview.irreversible) || requiredPhrase !== '';
  const phraseSatisfied =
    !irreversible ||
    phrase.trim().toLowerCase() === requiredPhrase.trim().toLowerCase();

  const act = useCallback(
    async (verb: 'confirm' | 'reject') => {
      setBusy(true);
      setError(null);
      try {
        const body =
          verb === 'confirm' && irreversible
            ? { confirm_phrase: phrase }
            : undefined;
        await api.post(`/api/aichat/proposals/${proposal.id}/${verb}/`, body);
      } catch (err: any) {
        setError(err?.response?.data?.error ?? 'PROPOSAL_REQUEST_FAILED');
      } finally {
        setBusy(false);
        onChanged();
      }
    },
    [proposal.id, onChanged, irreversible, phrase]
  );

  const pending = proposal.state === 'proposed';
  const label = ACTION_LABELS[proposal.action_type] ?? proposal.action_type;
  const target =
    proposal.preview.reference ||
    (proposal.work_order_id
      ? `WO-${proposal.work_order_id}`
      : 'new work order');
  const summary = previewSummary(proposal);

  return (
    <Card withBorder radius='md' p='sm' data-testid='chat-action-proposal'>
      <Stack gap={6}>
        <Group justify='space-between' wrap='nowrap'>
          <Group gap='xs' wrap='nowrap'>
            {actionIcon(proposal.action_type)}
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
            {proposal.preview.title}
          </Text>
        )}
        {summary && (
          <Text size='xs' c='dimmed' component='div'>
            <InlineMarkdown content={summary} />
          </Text>
        )}
        {proposal.reason && (
          <Text size='xs' fs='italic' component='div'>
            “<InlineMarkdown content={proposal.reason} />”
          </Text>
        )}
        {irreversible && pending && (
          <Alert color='red' p={6}>
            <Text size='xs' fw={600}>
              This is irreversible. Type “{requiredPhrase}” to confirm.
            </Text>
          </Alert>
        )}
        <Text size='xs' c='orange'>
          {proposal.preview.warning ??
            'This does not change any safety status.'}
        </Text>
        {pending && (
          <Stack gap={6}>
            {irreversible && (
              <TextInput
                size='xs'
                value={phrase}
                onChange={(event) => setPhrase(event.currentTarget.value)}
                placeholder={requiredPhrase}
                data-testid='proposal-confirm-phrase'
              />
            )}
            <Group gap='xs'>
              <Button
                size='xs'
                color='teal'
                loading={busy}
                disabled={!phraseSatisfied}
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
          </Stack>
        )}
        {proposal.state === 'executed' && proposal.receipt && (
          <Text size='xs' c='teal'>
            Executed: {receiptSummary(proposal.receipt)}
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
    // S46: a finished chat turn nudges an immediate refresh so a proposal
    // minted by the turn you just watched appears now; the poll stays as
    // the backstop until S49's STATE_DELTA replaces it.
    const onTurnFinished = () => void refresh();
    window.addEventListener('aimms:proposals-refresh', onTurnFinished);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener('aimms:proposals-refresh', onTurnFinished);
    };
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
