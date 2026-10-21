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
import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
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
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { useAIChatState } from '../../states/AIChatState';
import { useVoiceDecisionState } from '../../states/VoiceDecisionState';
import { InvestigationArtifactReview } from '../aichat/InvestigationArtifactReview';

import { api } from '../../App';
import { InlineMarkdown } from '../aichat/MarkdownMessage';
import { ReadErrorNotice } from '../common/ReadErrorNotice';
import { describeFailure, parseBusinessResult } from './businessResult';

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
  preview_hash?: string;
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
  onChanged,
  onPublicationError
}: Readonly<{
  proposal: ChatActionProposalPayload;
  onChanged: () => void;
  onPublicationError?: (message: string) => void;
}>) {
  const queryClient = useQueryClient();
  const publication = proposal.action_type === 'investigation.artifact';
  const supported = proposal.preview.publication_supported !== false;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phrase, setPhrase] = useState('');
  const decision = useVoiceDecisionState((state) => state.decision);

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
      if (publication) onPublicationError?.('');
      try {
        const body =
          verb === 'confirm'
            ? {
                confirm_phrase: irreversible ? phrase : '',
                expected_preview_hash: proposal.preview_hash || undefined
              }
            : undefined;
        const response = await api.post(
          `/api/aichat/proposals/${proposal.id}/${verb}/`,
          body
        );
        // A8: a 2xx is not a result. The proposal's recorded state is.
        const expected = verb === 'confirm' ? 'executed' : 'rejected';
        const result = parseBusinessResult<Partial<ChatActionProposalPayload>>(
          response.status,
          response.data,
          {
            failureCodeOf: (payload) =>
              typeof payload?.state === 'string' && payload.state !== expected
                ? `STATE_${payload.state.toUpperCase()}`
                : null
          }
        );
        if (!result.ok) {
          setError(describeFailure(result));
        } else if (publication && verb === 'confirm') {
          const receipt = response.data?.receipt;
          if (
            receipt?.command === 'investigation.artifact' &&
            typeof proposal.work_order_id === 'number' &&
            receipt.work_order_id === proposal.work_order_id &&
            receipt.request_id === proposal.preview.request_id &&
            typeof receipt.request_version === 'number'
          ) {
            useAIChatState
              .getState()
              .advanceRequestFocus(
                proposal.work_order_id,
                receipt.request_id,
                receipt.request_version
              );
          }
          void queryClient.invalidateQueries({
            queryKey: ['work-order', proposal.work_order_id]
          });
        }
      } catch (err: any) {
        const failure = parseBusinessResult(
          err?.response?.status ?? 0,
          err?.response?.data
        );
        const detail = err?.response?.data?.detail;
        const message =
          publication && !failure.ok && failure.committed === 'no'
            ? `${typeof detail === 'string' ? detail : describeFailure(failure)} No additional evidence was shared by this attempt.`
            : publication && typeof detail === 'string'
              ? detail
              : failure.ok
                ? 'PROPOSAL_REQUEST_FAILED'
                : describeFailure({
                    ...failure,
                    code:
                      err?.response?.data?.error ??
                      (failure.code === 'HTTP_0'
                        ? 'PROPOSAL_REQUEST_FAILED'
                        : failure.code)
                  });
        setError(message);
        if (publication) onPublicationError?.(message);
      } finally {
        setBusy(false);
        onChanged();
      }
    },
    [
      proposal.id,
      proposal.preview_hash,
      proposal.work_order_id,
      proposal.preview.request_id,
      onChanged,
      onPublicationError,
      irreversible,
      phrase,
      publication,
      queryClient
    ]
  );

  const pending = proposal.state === 'proposed';
  const label = ACTION_LABELS[proposal.action_type] ?? proposal.action_type;
  const target =
    proposal.preview.reference ||
    (proposal.work_order_id
      ? `WO-${proposal.work_order_id}`
      : 'new work order');
  const summary = previewSummary(proposal);

  if (decision?.source_id === proposal.id && decision.state === 'presented') {
    return (
      <Alert color='blue' data-testid='proposal-delegated-to-decision'>
        {t`This action is focused in the shared decision card. Review it there so voice and touch keep the same confirmation.`}{' '}
        {decision.target_label}
      </Alert>
    );
  }

  return (
    <Card withBorder radius='md' p='sm' data-testid='chat-action-proposal'>
      <Stack gap={6} style={{ overflowWrap: 'anywhere' }}>
        <Group justify='space-between' align='flex-start'>
          <Group gap='xs' wrap='nowrap' style={{ flex: 1, minWidth: 160 }}>
            {actionIcon(proposal.action_type)}
            <Text fw={600} size='sm'>
              {label}: {target}
              {publication ? '' : '?'}
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
        {publication && (
          <Stack gap='xs' data-testid='investigation-publication-review'>
            <Text size='sm' fw={600}>
              Request {String(proposal.preview.request_id)} · revision{' '}
              {String(proposal.preview.request_version)}
            </Text>
            <Text size='sm'>
              Decision purpose:{' '}
              {String(proposal.preview.purpose ?? 'Not recorded')}
            </Text>
            <Text size='sm' fw={600}>
              {String(proposal.preview.kind ?? 'Artifact')}
            </Text>
            <InvestigationArtifactReview payload={proposal.preview.payload} />
            {proposal.preview.supersedes_id != null && (
              <Text size='sm'>
                Correction of evidence {String(proposal.preview.supersedes_id)}.
                The original remains saved.
              </Text>
            )}
            {proposal.preview.file != null &&
              typeof proposal.preview.file === 'object' &&
              (() => {
                const file = proposal.preview.file as Record<string, unknown>;
                return (
                  <Stack gap={2}>
                    <Text size='sm'>
                      Selected chat file: {String(file.filename)} ·{' '}
                      {String(file.size)} bytes
                    </Text>
                  </Stack>
                );
              })()}
            <Alert color={supported ? 'blue' : 'orange'} p='xs'>
              {String(
                proposal.preview.publication ??
                  'Publication details unavailable. Nothing is saved.'
              )}
            </Alert>
          </Stack>
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
        <Text
          size={publication ? 'sm' : 'xs'}
          c={publication ? undefined : 'orange'}
        >
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
                mih={publication ? 44 : undefined}
                color={publication ? 'blue.8' : 'teal'}
                loading={busy}
                disabled={!phraseSatisfied || (publication && !supported)}
                onClick={() => void act('confirm')}
                data-testid='proposal-confirm'
              >
                {publication ? t`Approve and share evidence` : t`Confirm`}
              </Button>
              <Button
                size='xs'
                mih={publication ? 44 : undefined}
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
          <Text
            size={publication ? 'sm' : 'xs'}
            c={publication ? undefined : 'teal'}
          >
            {publication
              ? t`Saved evidence ${String(proposal.receipt.artifact_id)} for request ${String(proposal.receipt.request_id)} · Recorded by: ${typeof proposal.receipt.recorded_by_name === 'string' && proposal.receipt.recorded_by_name.trim() ? proposal.receipt.recorded_by_name : t`Not recorded`}. Continue here or return to Diagnose.`
              : `Executed: ${receiptSummary(proposal.receipt)}`}
          </Text>
        )}
        {publication &&
          ['rejected', 'expired', 'failed'].includes(proposal.state) && (
            <Text size='sm' c='dimmed'>
              Nothing was shared from this proposal.
            </Text>
          )}
        {proposal.failure_code && (
          <Text size='xs' c='red'>
            {proposal.failure_code}
          </Text>
        )}
        {error && (
          <Alert color='red' p={4} data-testid='proposal-error'>
            <Text size='xs'>{error}</Text>
          </Alert>
        )}
      </Stack>
    </Card>
  );
}

export function ChatActionProposalList({
  proposals,
  refresh,
  error,
  loading
}: Readonly<{
  proposals: ChatActionProposalPayload[];
  refresh: () => void;
  error?: unknown;
  loading?: boolean;
}>) {
  const [publicationError, setPublicationError] = useState<{
    proposalId: string;
    message: string;
  } | null>(null);
  // A scope refresh may remove the private preview; retain only the attempt's
  // outcome so a refused publication does not disappear with that card.
  const notice =
    publicationError &&
    (error ||
      loading ||
      !proposals.some((p) => p.id === publicationError.proposalId)) ? (
      <Alert color='orange' data-testid='publication-outcome'>
        {publicationError.message}
      </Alert>
    ) : null;
  if (error)
    return (
      <Stack gap='xs'>
        {notice}
        <ReadErrorNotice error={error} retry={refresh} />
      </Stack>
    );
  if (loading)
    return (
      <Stack gap='xs'>
        {notice}
        <Loader size='xs' aria-label={t`Loading action proposals`} />
      </Stack>
    );
  if (proposals.length === 0) {
    return notice;
  }

  return (
    <Stack gap='xs' data-testid='chat-action-proposals'>
      {notice}
      <Text size='sm' fw={600}>
        {t`Recent action proposals`}
      </Text>
      {proposals.map((proposal) => (
        <ProposalCard
          key={proposal.id}
          proposal={proposal}
          onChanged={() => void refresh()}
          onPublicationError={(message) =>
            setPublicationError(
              message ? { proposalId: proposal.id, message } : null
            )
          }
        />
      ))}
    </Stack>
  );
}
