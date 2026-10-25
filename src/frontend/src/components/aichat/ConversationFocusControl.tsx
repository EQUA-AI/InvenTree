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
  SegmentedControl,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import {
  startTargetConversation,
  useAIChatState
} from '../../states/AIChatState';
import { useThreadFocusState } from '../../states/ThreadFocusState';
import { useUserState } from '../../states/UserState';
import { voiceController } from '../../states/VoiceSessionState';
import { AssistantModal } from './AssistantModal';
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
      return t`That target is outside the current analysis scope. The current target and scope have been kept.`;
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
  const binding = useThreadFocusState((state) => state.binding);
  const shared = binding?.shared ?? false;
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef<symbol | null>(null);
  const [failure, setFailure] = useState<{
    code: string;
    candidate: ConversationFocus | null;
  } | null>(null);
  const [recoveryError, setRecoveryError] = useState(false);
  const restoreFocus = useRef<(() => void) | null>(null);
  const resolving = status === 'loading' || status === 'updating' || busy;
  const chip = focusChipState(focus, unavailable);

  useEffect(() => {
    inFlight.current = null;
    setBusy(false);
    setPickerOpen(false);
    setFailure(null);
    setRecoveryError(false);
  }, [binding]);

  useLayoutEffect(() => {
    if (pickerOpen || !restoreFocus.current) return;
    const restore = restoreFocus.current;
    restoreFocus.current = null;
    const frame = requestAnimationFrame(restore);
    return () => cancelAnimationFrame(frame);
  }, [pickerOpen]);

  const commit = async (candidate: ConversationFocus | null) => {
    const current = useThreadFocusState.getState();
    if (
      inFlight.current !== null ||
      current.binding !== binding ||
      !binding ||
      binding.shared ||
      current.status === 'loading' ||
      current.status === 'updating'
    )
      return;
    const operation = Symbol();
    inFlight.current = operation;
    // C01: an actual explicit user admission — a picker selection or Clear,
    // including a candidate the server later refuses — immediately supersedes
    // the pending page intent so an older entry hint can never be reapplied
    // behind the user's newer choice. The entry preflight (applyFocusToThread)
    // is NOT explicit admission and keeps its own compare-and-consume path.
    useAIChatState.getState().clearHint();
    setBusy(true);
    setFailure(null);
    setRecoveryError(false);
    try {
      const result = await commitFocusChange(focusRefOf(candidate));
      if (useThreadFocusState.getState().binding !== binding) return;
      if (result.ok) setPickerOpen(false);
      else setFailure({ code: result.code ?? 'error', candidate });
    } catch {
      if (useThreadFocusState.getState().binding === binding)
        setFailure({ code: 'error', candidate });
    } finally {
      if (inFlight.current === operation) {
        inFlight.current = null;
        setBusy(false);
      }
    }
  };

  const keepCurrent = () => {
    if (resolving) return;
    setFailure(null);
    setRecoveryError(false);
    setPickerOpen(false);
  };

  return (
    <Group gap='xs' data-testid='conversation-focus-chip'>
      <Badge
        variant='outline'
        color={focus ? 'teal' : 'gray'}
        leftSection={resolving ? <Loader size={10} /> : undefined}
        aria-label={t`Talking about`}
        styles={{
          label: { whiteSpace: 'normal', overflowWrap: 'anywhere' },
          root: { height: 'auto', minHeight: 24, maxWidth: '100%' }
        }}
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
            onClick={(event) => {
              // Capture before the parent becomes inert; Mantine's later
              // focus capture otherwise observes the document body.
              const target = event.currentTarget;
              const binding = useThreadFocusState.getState().binding;
              restoreFocus.current = () => {
                if (
                  target.isConnected &&
                  !target.disabled &&
                  !target.closest('[inert]') &&
                  useThreadFocusState.getState().binding === binding
                ) {
                  // The reactivated parent trap also queues initial focus.
                  // Give it the same target until the user leaves this control.
                  const autofocus = target.getAttribute('data-autofocus');
                  target.setAttribute('data-autofocus', 'true');
                  target.addEventListener(
                    'blur',
                    () => {
                      if (autofocus === null) {
                        target.removeAttribute('data-autofocus');
                      } else {
                        target.setAttribute('data-autofocus', autofocus);
                      }
                    },
                    { once: true }
                  );
                  target.focus({ preventScroll: true });
                }
              };
              setFailure(null);
              setRecoveryError(false);
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
      {!pickerOpen && (failure || status === 'error') && (
        <Text
          size='xs'
          c='red'
          role='alert'
          data-testid='conversation-focus-error'
        >
          {failureMessage(failure?.code ?? error)}
        </Text>
      )}
      <ConversationFocusPicker
        opened={pickerOpen}
        returnFocus={false}
        onClose={keepCurrent}
        onCommit={commit}
        busy={resolving}
        failure={failure ? failureMessage(failure.code) : null}
        failedTarget={failure?.candidate}
        onRetry={failure ? () => void commit(failure.candidate) : undefined}
        recoveryError={recoveryError}
        onStartConversation={
          failure?.code === 'focus_out_of_analysis_scope' && failure.candidate
            ? () => {
                const candidate = failure.candidate;
                if (
                  !candidate ||
                  resolving ||
                  useThreadFocusState.getState().binding !== binding
                )
                  return;
                if (startTargetConversation(candidate)) keepCurrent();
                else setRecoveryError(true);
              }
            : undefined
        }
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
  onCommit,
  busy = false,
  failure,
  failedTarget,
  onRetry,
  onStartConversation,
  recoveryError = false,
  returnFocus = true
}: {
  opened: boolean;
  onClose: () => void;
  onCommit: (candidate: ConversationFocus) => Promise<void>;
  busy?: boolean;
  failure?: string | null;
  failedTarget?: ConversationFocus | null;
  onRetry?: () => void;
  onStartConversation?: () => void;
  recoveryError?: boolean;
  returnFocus?: boolean;
}) {
  const host = useThreadFocusState((state) => state.binding?.host ?? '');
  const userId = useUserState((state) => state.userId());
  const generation = useAIChatState((state) => state.sessionGeneration);
  const [kind, setKind] = useState<ConversationFocusKind>('machine');
  const [query, setQuery] = useState('');
  const candidates = useQuery({
    queryKey: ['focus-candidates', host, userId, generation, kind, query],
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
    <AssistantModal
      opened={opened}
      onClose={onClose}
      closeOnEscape={!busy}
      closeOnClickOutside={!busy}
      closeButtonProps={{
        disabled: busy,
        'aria-label': t`Close target picker`
      }}
      returnFocus={returnFocus}
      title={t`What are we talking about?`}
      data-testid='conversation-focus-picker'
    >
      <Stack gap='sm'>
        {busy && (
          <Text
            component='output'
            aria-live='polite'
            size='sm'
            data-testid='conversation-focus-pending'
          >
            <Trans>Confirming target…</Trans>
          </Text>
        )}
        {failure && (
          <Stack gap='xs'>
            <Text
              role='alert'
              size='sm'
              c='red'
              data-testid='conversation-focus-error'
            >
              {failure}
            </Text>
            {failedTarget && (
              <Text size='sm'>
                {failedTarget.label} ({focusSecondary(failedTarget)})
              </Text>
            )}
            <Group gap='xs'>
              <Button variant='default' disabled={busy} onClick={onClose}>
                <Trans>Keep current target</Trans>
              </Button>
              {onRetry && (
                <Button variant='light' disabled={busy} onClick={onRetry}>
                  <Trans>Retry selection</Trans>
                </Button>
              )}
            </Group>
            {onStartConversation && (
              <>
                <Button
                  variant='light'
                  h='auto'
                  py='xs'
                  styles={{ label: { whiteSpace: 'normal' } }}
                  disabled={busy}
                  onClick={onStartConversation}
                >
                  <Trans>Start a new conversation for this target</Trans>
                </Button>
                <Text size='xs' c='dimmed'>
                  <Trans>
                    The target will be confirmed before your first message.
                  </Trans>
                </Text>
              </>
            )}
            {recoveryError && (
              <Text role='alert' size='sm' c='red'>
                <Trans>
                  Could not start a new conversation. Finish the current chat or
                  voice activity and try again.
                </Trans>
              </Text>
            )}
          </Stack>
        )}
        <SegmentedControl
          disabled={busy}
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
          disabled={busy}
          aria-label={t`Search targets`}
          data-testid='conversation-focus-search'
          placeholder={t`Search by name or reference...`}
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
        />
        {candidates.isFetching && opened ? (
          <Group>
            <Loader size='sm' />
            <Text component='output' aria-live='polite'>
              <Trans>Loading targets…</Trans>
            </Text>
          </Group>
        ) : candidates.isError ? (
          <Stack gap='xs'>
            <Text
              role='alert'
              size='sm'
              c='red'
              data-testid='conversation-focus-search-error'
            >
              <Trans>Could not load targets — try again.</Trans>
            </Text>
            <Button
              variant='light'
              disabled={busy}
              onClick={() => void candidates.refetch()}
            >
              <Trans>Retry search</Trans>
            </Button>
          </Stack>
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
                disabled={busy}
                data-testid='conversation-focus-option'
                aria-label={`${candidate.label} (${focusSecondary(candidate)})`}
                onClick={() => void onCommit(candidate)}
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
    </AssistantModal>
  );
}
