/**
 * Talking about — the editable conversation-focus chip and target picker
 * (plan package C).
 *
 * Shows ONLY the server-confirmed focus label (a hint or a label-only match
 * is never rendered as confirmed context) with Change/Clear actions, next to
 * the separate read-only Analysis-scope indicator. Every selection commits a
 * canonical `{kind, id}` candidate through the owner focus PUT and renders
 * the exact server ack; denied, conflicting or unsupported targets surface
 * an honest error and change nothing. Read-only shared threads can never
 * select or mutate.
 *
 * The picker searches AUTHORIZED candidates (search hints never authorize)
 * via TanStack Query with visible resolving/error states, disambiguated by
 * canonical ids (two machines may share a display name).
 */
import { t } from '@lingui/core/macro';
import { Trans } from '@lingui/react/macro';
import {
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  SegmentedControl,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { useThreadFocusState } from '../../states/ThreadFocusState';
import { voiceController } from '../../states/VoiceSessionState';
import {
  type ConversationFocus,
  type ConversationFocusKind,
  type ConversationFocusRef,
  focusChipState,
  focusRefOf,
  sameFocusRef,
  searchFocusCandidates
} from './conversationFocus';

const KIND_LABELS: Record<ConversationFocusKind, string> = {
  // The wire kind `kanban_card` is user-facing "Task" (the board card).
  machine: t`Machine`,
  kanban_card: t`Task`,
  work_order: t`Work order`
};

function focusSecondary(focus: ConversationFocus): string {
  const parts: string[] = [KIND_LABELS[focus.kind]];
  if (focus.machine_id != null) parts.push(t`Machine ${focus.machine_id}`);
  if (focus.work_order_id != null)
    parts.push(t`Work order ${focus.work_order_id}`);
  parts.push(`#${focus.id}`);
  return parts.join(' · ');
}

function failureMessage(code: string | null | undefined): string {
  switch (code) {
    case 'focus_version_conflict':
      return t`The conversation focus changed elsewhere — nothing was applied. Try again.`;
    case 'focus_out_of_analysis_scope':
      // Actionable refusal: the user's existing explicit controls are the
      // keep-scope / change-scope / new-conversation paths. Never auto-expand
      // the scope and never navigate on the user's behalf.
      return t`That target is outside the current analysis scope — keep the current scope, change the scope, or start a new conversation.`;
    case 'focus_decision_not_set_aside':
      // Fail closed, single attempt: a failed set-aside never consumes an
      // approval and is never retried automatically.
      return t`A pending decision could not be set aside — the focus was not changed. Review the pending decision first.`;
    case 'shared-thread':
      return t`Shared conversations are read-only — the focus cannot be changed.`;
    case 'no-thread':
    case 'context_changed':
      return t`The conversation changed before the selection was confirmed — nothing was applied.`;
    default:
      return t`That target is not available — the focus was not changed.`;
  }
}

/**
 * Commit a focus selection (or clear) and settle the live-session fallout in
 * ONE place for the chip and the entry preflight. Only an ACTUAL change
 * invalidates old-context work (an idempotent re-select of the same target
 * must not discard anything); a live voice session then keeps running with
 * fresh capture stamps.
 */
export async function commitFocusChange(
  ref: ConversationFocusRef | null,
  onAcknowledged?: (version: number) => void
): Promise<{ ok: boolean; code?: string }> {
  const before = focusRefOf(useThreadFocusState.getState().focus);
  const result = await useThreadFocusState
    .getState()
    .select(ref, onAcknowledged);
  if (result.ok && !sameFocusRef(before, ref)) {
    voiceController.focusChanged();
  }
  return result;
}

/**
 * The chip. `focusLabel` is server-confirmed state from the thread focus
 * store (bound per thread by the chat hook).
 */
export function ConversationFocusControl() {
  const focus = useThreadFocusState((state) => state.focus);
  const unavailable = useThreadFocusState((state) => state.unavailable);
  const status = useThreadFocusState((state) => state.status);
  const error = useThreadFocusState((state) => state.error);
  const shared = useThreadFocusState((state) => state.binding?.shared ?? false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const resolving = status === 'loading' || status === 'updating' || busy;
  const chip = focusChipState(focus, unavailable);

  const commit = async (ref: ConversationFocusRef | null) => {
    setBusy(true);
    setActionError(null);
    const result = await commitFocusChange(ref);
    setBusy(false);
    if (result.ok) {
      setPickerOpen(false);
      return;
    }
    setActionError(failureMessage(result.code));
  };

  return (
    <Group gap='xs' data-testid='conversation-focus-chip'>
      <Badge
        variant='outline'
        color={focus ? 'teal' : 'gray'}
        leftSection={resolving ? <Loader size={10} /> : undefined}
        aria-label={t`Talking about`}
      >
        <Trans>Talking about</Trans>:{' '}
        {chip === 'target' && focus ? (
          <span data-testid='conversation-focus-label'>{focus.label}</span>
        ) : chip === 'unavailable' ? (
          // Revoked/unavailable reference: a brief localized state — never
          // the stale label, and never an implied access grant.
          <span data-testid='conversation-focus-label'>
            <Trans>Reference no longer available</Trans>
          </span>
        ) : (
          <span data-testid='conversation-focus-label'>
            <Trans>nothing yet</Trans>
          </span>
        )}
      </Badge>
      {!shared && (
        <>
          <Button
            size='compact-xs'
            variant='subtle'
            data-testid='conversation-focus-change'
            aria-label={t`Change what we are talking about`}
            disabled={resolving}
            onClick={() => {
              setActionError(null);
              setPickerOpen(true);
            }}
          >
            <Trans>Change</Trans>
          </Button>
          {focus && (
            <Button
              size='compact-xs'
              variant='subtle'
              color='gray'
              data-testid='conversation-focus-clear'
              aria-label={t`Clear what we are talking about`}
              disabled={resolving}
              onClick={() => void commit(null)}
            >
              <Trans>Clear</Trans>
            </Button>
          )}
        </>
      )}
      {focus && !shared && (
        <Text size='xs' c='dimmed' title={focusSecondary(focus)}>
          {focusSecondary(focus)}
        </Text>
      )}
      {(actionError ?? (status === 'error' ? failureMessage(error) : null)) && (
        <Text size='xs' c='red' data-testid='conversation-focus-error'>
          {actionError ?? failureMessage(error)}
        </Text>
      )}
      <ConversationFocusPicker
        opened={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onCommit={commit}
      />
    </Group>
  );
}

/**
 * The target picker: kind + query over the authorized candidate search.
 * Selecting a row commits `{kind, id}` only — labels are never sent.
 */
export function ConversationFocusPicker({
  opened,
  onClose,
  onCommit
}: {
  opened: boolean;
  onClose: () => void;
  onCommit: (ref: ConversationFocusRef | null) => Promise<void>;
}) {
  const host = useThreadFocusState((state) => state.binding?.host ?? '');
  const [kind, setKind] = useState<ConversationFocusKind>('machine');
  const [query, setQuery] = useState('');
  const candidates = useQuery({
    queryKey: ['focus-candidates', host, kind, query],
    enabled: opened && host !== '',
    queryFn: async () => {
      const result = await searchFocusCandidates(host, kind, query, 10);
      if (!result.ok) throw new Error(result.code);
      return result.payload;
    },
    staleTime: 30_000,
    retry: false
  });

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t`What are we talking about?`}
      data-testid='conversation-focus-picker'
    >
      <Stack gap='sm'>
        <SegmentedControl
          value={kind}
          aria-label={t`Target kind`}
          data-testid='conversation-focus-kind'
          onChange={(value) => setKind(value as ConversationFocusKind)}
          data={Object.entries(KIND_LABELS).map(([value, label]) => ({
            value,
            label
          }))}
        />
        <TextInput
          aria-label={t`Search targets`}
          data-testid='conversation-focus-search'
          placeholder={t`Search by name or reference...`}
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
        />
        {candidates.isPending && opened ? (
          <Group>
            <Loader size='sm' />
            <Trans>Loading targets…</Trans>
          </Group>
        ) : candidates.isError ? (
          <Text size='sm' c='red' data-testid='conversation-focus-error'>
            <Trans>Could not load targets — try again.</Trans>
          </Text>
        ) : (candidates.data?.candidates.length ?? 0) === 0 ? (
          <Text size='sm' c='dimmed'>
            <Trans>No matching targets.</Trans>
          </Text>
        ) : (
          <Stack gap={4}>
            {candidates.data?.candidates.map((candidate) => (
              <Button
                key={`${candidate.kind}-${candidate.id}`}
                variant='light'
                justify='flex-start'
                data-testid='conversation-focus-option'
                aria-label={`${candidate.label} (${focusSecondary(candidate)})`}
                onClick={() =>
                  void onCommit({ kind: candidate.kind, id: candidate.id })
                }
              >
                <Stack gap={0} align='flex-start'>
                  <Text size='sm'>{candidate.label}</Text>
                  <Text size='xs' c='dimmed'>
                    {focusSecondary(candidate)}
                  </Text>
                </Stack>
              </Button>
            ))}
          </Stack>
        )}
        {candidates.data?.has_more && (
          <Text size='xs' c='dimmed'>
            <Trans>More matches exist — refine the search.</Trans>
          </Text>
        )}
      </Stack>
    </Modal>
  );
}
