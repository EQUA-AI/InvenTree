import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Group,
  Paper,
  Tabs,
  Text,
  Tooltip,
  useMantineTheme
} from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import { showNotification } from '@mantine/notifications';
import { IconArrowDown, IconSparkles, IconX } from '@tabler/icons-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore
} from 'react';
import { useLocation } from 'react-router-dom';
import { VoiceDock } from '../ai/voice/VoiceDock';
import { AssistantHeader } from '../aichat/AssistantHeader';
import { AssistantMessageRenderer as ChatMessageItem } from '../aichat/AssistantMessageRenderer';
import workspaceStyles from '../aichat/AssistantWorkspace.module.css';
import { ChatComposer } from '../aichat/ChatComposer';
import { ChatTranscript } from '../aichat/ChatTranscript';
import {
  ConversationHistory as ThreadHistoryPanel,
  ConversationBrowser as ThreadSelector
} from '../aichat/ConversationBrowser';
import { ConversationContextBar } from '../aichat/ConversationContextBar';
import {
  ASSISTANT_MIN_WIDTH,
  useAssistantGeometry
} from '../aichat/useAssistantGeometry';

import { Boundary } from '@lib/components/Boundary';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../App';
import {
  CHAT_INDEX_PREFIX,
  chatIndexKey,
  chatInvalidationKey
} from '../../functions/chatThreadCache';
import {
  type QuestionPayload,
  type QuestionResolution,
  useAIChat
} from '../../hooks/UseAIChat';
import { useChatProposals } from '../../hooks/useChatProposals';
import { useVoiceLiveSession } from '../../hooks/useVoiceLiveSession';
import {
  type AIChatTargetHint,
  registerTargetConversationStarter,
  useAIChatState
} from '../../states/AIChatState';
import {
  type ChatDraftBinding,
  type ChatDraftScope,
  bindChatDrafts,
  subscribeChatDrafts
} from '../../states/ChatDraftState';
import { useEvidenceViewerState } from '../../states/EvidenceViewerState';
import { useLocalState } from '../../states/LocalState';
import { useThreadFocusState } from '../../states/ThreadFocusState';
import { useUserState } from '../../states/UserState';
import { useVoiceDecisionState } from '../../states/VoiceDecisionState';
import {
  registerContextPreflight,
  useVoiceSessionState,
  useVoiceSurfaceState,
  voiceController
} from '../../states/VoiceSessionState';
import {
  type PageContextKind,
  isNearBottom,
  pageContextKind,
  shouldFollowNewContent,
  shouldSendOnEnter,
  suggestionKinds
} from './chatComposer';

import { ApprovalInboxPanel } from '../ai/ApprovalInboxPanel';
import { ChatActionProposalList } from '../ai/ChatActionProposals';
import { MailboxPanel } from '../ai/MailboxPanel';
import { VoiceDecisionCard } from '../ai/VoiceDecisionCard';
import {
  VoiceActiveStrip,
  VoiceComposerControl
} from '../ai/voice/VoiceComposerControl';
import { VOICE_SHORTCUT } from '../ai/voice/voiceShortcuts';
import { voiceStartBlockReason } from '../ai/voice/voiceStartEligibility';
import { AssistantSurface } from '../aichat/AssistantSurface';
import { ChatAttachmentQueue } from '../aichat/ChatAttachmentQueue';
import { commitFocusChange } from '../aichat/ConversationFocusControl';
import { LegacyChatStorageNotice } from '../aichat/LegacyChatStorageNotice';
import { ThreadMemoryModal } from '../aichat/ThreadMemoryModal';
import {
  type ContextPreflight,
  type ContextTargetHint,
  type ExplicitMachineScope,
  canAdoptSessionThread,
  runContextPreflight as executeContextPreflight,
  isFreshSessionTurn
} from '../aichat/contextPreflight';
import type { ConversationFocusRef } from '../aichat/conversationFocus';
import { hintIsRedundant } from '../aichat/scopeContext';
import { type ChatWorkFocus, focusReturnPath } from '../aichat/workFocus';

type AIChatDrawerTab = 'chat' | 'approvals' | 'history' | 'mail';

/**
 * AI Chat toggle button component - displays in the header
 * CopilotKit-style sparkle icon button
 */
export function AIChatButton({
  onClick,
  opened = false
}: Readonly<{
  onClick: () => void;
  opened?: boolean;
}>) {
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const tooltipTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined
  );
  const showTooltip = () => {
    clearTimeout(tooltipTimer.current);
    setTooltipOpen(true);
  };
  const hideTooltip = () => {
    tooltipTimer.current = setTimeout(() => setTooltipOpen(false), 150);
  };
  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setTooltipOpen(false);
    };
    window.addEventListener('keydown', dismiss);
    return () => {
      window.removeEventListener('keydown', dismiss);
      clearTimeout(tooltipTimer.current);
    };
  }, []);
  return (
    <Tooltip
      position='bottom-end'
      label={t`AI Assistant · Voice: ${VOICE_SHORTCUT.label}`}
      opened={tooltipOpen}
      events={{ hover: true, focus: true, touch: false }}
      styles={{ tooltip: { pointerEvents: 'auto' } }}
      onMouseEnter={showTooltip}
      onMouseLeave={hideTooltip}
    >
      <ActionIcon
        onClick={onClick}
        variant='subtle'
        size='lg'
        radius='xl'
        data-testid='open-ai-chat'
        aria-label={t`Open AI chat`}
        aria-expanded={opened}
        aria-controls='ai-chat-drawer'
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        onFocus={showTooltip}
        onBlur={() => setTooltipOpen(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setTooltipOpen(false);
            event.stopPropagation();
          }
        }}
        style={{
          transition: 'transform 0.2s ease, background-color 0.2s ease'
        }}
      >
        <IconSparkles size={20} />
      </ActionIcon>
    </Tooltip>
  );
}

/**
 * AI Chat Drawer component - CopilotKit-style side panel
 */
type AIChatDrawerProps = Readonly<{ opened: boolean; onClose: () => void }>;

/** Identity/entitlement changes discard the whole chat/voice/composer subtree. */
export function AIChatDrawer(props: AIChatDrawerProps) {
  const userId = useUserState((state) =>
    state.isLoggedIn() ? state.userId() : undefined
  );
  const host = useLocalState((state) => state.getHost());
  const generation = useAIChatState((state) => state.sessionGeneration);
  const key = chatIndexKey(host, userId);
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === chatInvalidationKey(key) && event.newValue !== null) {
        // Keep the writer's current index, including any pending-delete row.
        // Rebuild all RAM/query/voice state without a second invalidation loop.
        useAIChatState.getState().resetSession(false);
        return;
      }
      // A reset/deletion in another tab must not leave a cached transcript in
      // this one. Ordinary metadata updates are not a reason to reload chat.
      if (
        (event.key === key || event.key === null) &&
        event.newValue === null &&
        (event.key === null || event.key.startsWith(CHAT_INDEX_PREFIX))
      ) {
        useAIChatState.getState().resetSession(false);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [key]);
  if (!userId || !key) return null;
  return <AIChatSessionDrawer key={`${key}:${generation}`} {...props} />;
}

function AIChatSessionDrawer({
  opened,
  onClose
}: Readonly<{
  opened: boolean;
  onClose: () => void;
}>) {
  const returnFocusTarget = useAIChatState((state) => state.returnFocusTarget);
  const theme = useMantineTheme();
  const {
    messages,
    isLoading,
    error,
    activeThreadId,
    activeThreadDeletionPending,
    threads,
    sendMessage,
    clearChat,
    cancelRequest,
    switchThread,
    createNewThread,
    deleteThread,
    prepareThreadDeletion,
    exportTranscripts,
    deleteThreadPage,
    renameThread,
    isSyncing,
    syncThreads,
    searchThreads,
    hasEarlierMessages,
    loadingEarlier,
    loadEarlierMessages,
    hasMoreThreads,
    loadingMoreThreads,
    loadMoreThreads,
    // S32b: read-only sharing
    sharedThreads,
    activeThreadShared,
    shareThread,
    revokeThreadShare,
    // S1: active analysis scope
    activeScope,
    scopeCapable,
    scopeConflict,
    setThreadScope,
    refreshThreadScope,
    // Plan C: thread-focus capability + capture-time turn stamps
    focusCapable,
    workFocusCapable,
    referenceFocusCapable,
    requestFocusCapable,
    acknowledgeWorkFocus,
    resendLastTurn,
    aiHost,
    // Structured questions (S22/S23)
    pendingQuestion,
    armQuestion,
    answeredQuestionIds,
    uploadFile
  } = useAIChat();

  // S14 B5: machine routing hint preloaded by "Ask about this machine".
  const pendingRoutingHint = useAIChatState((state) => state.routingHint);
  const hintThreadId = useAIChatState((state) => state.hintThreadId);
  const routingHint =
    hintThreadId === null || hintThreadId === activeThreadId
      ? pendingRoutingHint
      : undefined;
  const clearRoutingHint = useAIChatState((state) => state.clearHint);
  // Plan C: the generalized pending target (machine/task/work order) behind
  // a contextual Ask — a candidate, committed through the owner focus PUT.
  const pendingTargetHint = useAIChatState((state) => state.targetHint);
  const targetHint: ContextTargetHint | undefined =
    hintThreadId === null || hintThreadId === activeThreadId
      ? pendingTargetHint
        ? {
            kind: pendingTargetHint.kind,
            id: pendingTargetHint.id,
            label: pendingTargetHint.label
          }
        : undefined
      : undefined;
  // P02/P33: a question asked from one Work order (never with a machine hint).
  const pendingWorkFocus = useAIChatState((state) => state.workFocusHint);
  const workFocusHint =
    hintThreadId === null || hintThreadId === activeThreadId
      ? pendingWorkFocus
      : undefined;
  const clearWorkFocus = useAIChatState((state) => state.clearWorkFocus);
  // U1: machine Ask/scan carries a one-shot "open the Chat tab" intent.
  const chatOpenIntent = useAIChatState((state) => state.chatOpenIntent);

  // S22: resolutions live on the answering turn's assistant message; the
  // asking card looks its own outcome up by interrupt_id when frozen.
  const questionResolutions = new Map<string, QuestionResolution>();
  for (const message of messages) {
    const resolution = message.questionResolution;
    if (resolution?.interrupt_id) {
      questionResolutions.set(resolution.interrupt_id, resolution);
    }
  }

  // Realtime voice (WS5): explicit user-started sessions in the same
  // drawer, converging on the same server-backed conversation history.
  const backendHost = useLocalState((state) => state.getHost());
  const voiceHost = new URL('api/ai/', `${backendHost.replace(/\/$/, '')}/`)
    .toString()
    .replace(/\/$/, '');
  // Plan 3.1.5: the visible thread at which the current voice session was
  // created. Late turn results may never navigate a deliberately switched
  // drawer back to their own thread; only the initial server-assigned thread
  // adoption is allowed (and initial undefined→server-created adoption is
  // not a switch).
  const voiceBindingRef = useRef<{
    sessionId: string;
    threadId: string | null;
  } | null>(null);
  const voice = useVoiceLiveSession({
    host: voiceHost,
    enabled: true,
    threadId: activeThreadId ?? undefined,
    onTurnResult: (turn) => {
      useVoiceDecisionState.getState().applyTurn(turn.session_id, turn);
      // Typed and voice turns share one server history; resync so the
      // drawer renders the converged conversation.
      void syncThreads();
      // Guard drawer callbacks by session/thread (the controller already
      // fences by generation): a late result stays in its thread's history
      // but must not move the visible drawer, replace B's focus or arm its
      // questions.
      const currentSessionId =
        useVoiceSessionState.getState().session?.id ?? null;
      const binding = voiceBindingRef.current;
      const turnContext = {
        turnSessionId: turn.session_id ?? null,
        turnThreadId: turn.thread_id ?? null,
        currentSessionId,
        createdThreadId:
          binding && binding.sessionId === currentSessionId
            ? binding.threadId
            : null,
        visibleThreadId: activeThreadId
      };
      if (!isFreshSessionTurn(turnContext)) return;
      if (canAdoptSessionThread(turnContext)) {
        switchThread(turn.thread_id);
      }
      // S22/S23: voice turns deliver the pending question on the REST
      // response (no SSE QUESTION event on this rail). Arm the singleton so
      // the card renders clickable instead of permanently frozen, and
      // disarm when a turn resolves it.
      armQuestion((turn.pending_question as QuestionPayload | null) ?? null);
      // Plan C: a spoken focus switch/clarify ("talk about …", a confirmed
      // focus answer) changes the thread focus server-side. The chip
      // converges from the authoritative thread read — a turn's echoed
      // `focus` is its recorded snapshot and never moves the chip.
      void useThreadFocusState.getState().refresh();
    },
    onFocusStale: () => {
      // Plan C: a turn/session refused on a stale focus stamp. The session
      // is alive; re-read the authoritative focus and tell the user the
      // words were not sent (the controller holds them for review).
      void useThreadFocusState.getState().refresh();
      showNotification({
        color: 'yellow',
        message: t`The conversation focus changed — the last words were not sent.`
      });
    }
  });
  const voiceSessionId = voice.session?.id ?? null;
  if (voiceSessionId && voiceBindingRef.current?.sessionId !== voiceSessionId) {
    voiceBindingRef.current = {
      sessionId: voiceSessionId,
      threadId: activeThreadId
    };
  } else if (!voiceSessionId && voiceBindingRef.current) {
    voiceBindingRef.current = null;
  }
  const handleClose = useCallback(() => {
    void voice.end();
    useVoiceSurfaceState.getState().closeConsent();
    clearRoutingHint();
    onClose();
  }, [onClose, voice.end, clearRoutingHint]);
  const voiceFullscreen = useVoiceSurfaceState((state) => state.fullscreen);
  const evidenceOpen = useEvidenceViewerState((state) => state.item !== null);
  const consentOpen = useVoiceSurfaceState((state) => state.consent);
  const location = useLocation();
  useEffect(() => {
    if (!opened) {
      void voice.end();
      useVoiceSurfaceState.getState().closeConsent();
    }
  }, [opened, voice.end]);
  useEffect(
    () => () => {
      void voiceController.end();
    },
    []
  );
  const proposals = useChatProposals(opened);
  const voiceDecision = useVoiceDecisionState((state) => state.decision);

  const [activeTab, setActiveTab] = useLocalStorage<AIChatDrawerTab>({
    key: 'ai-chat-drawer-active-tab',
    defaultValue: 'chat'
  });

  const approvalCount = useQuery({
    queryKey: ['approval-count'],
    enabled: opened,
    refetchInterval: voiceDecision ? 5000 : 30000,
    queryFn: async () => {
      const resp = await api.get('/api/approvals/count/', {
        params: {
          status: 'pending'
        }
      });
      const count = Number((resp.data as any)?.count);
      return Number.isFinite(count) ? count : 0;
    }
  });
  const pendingApprovalCount = approvalCount.isError
    ? 0
    : (approvalCount.data ?? 0);
  // C1: one truthful "needs attention" signal — an actionable proposal, a
  // presented focused decision, or inbox items awaiting review. Never a
  // numeric aggregate (the count is known-partial and can be unavailable).
  const needsAttention =
    proposals.proposals.some((proposal) => proposal.state === 'proposed') ||
    voiceDecision?.state === 'presented' ||
    pendingApprovalCount > 0;
  // C2: the routing hint collapses once the server-confirmed scope already
  // covers it (display decision only; authorization stays server-side).
  const hintRedundant = hintIsRedundant(activeScope, routingHint ?? undefined);
  useEffect(() => {
    if (
      routingHint &&
      scopeCapable &&
      activeScope?.mode === 'explicit_assets' &&
      activeScope.machineIds === undefined
    )
      void refreshThreadScope();
  }, [routingHint, scopeCapable, activeScope, refreshThreadScope]);
  const [approvalView, setApprovalView] = useState<'open' | 'resolved'>('open');
  // U1: friendly page labels for the drawer's context line (never raw path).
  const pageContextLabels: Record<PageContextKind, string> = {
    home: t`Current page: Home`,
    machines: t`Current page: Machines`,
    maintenance: t`Current page: Maintenance`,
    parts: t`Current page: Parts`,
    stock: t`Current page: Stock`,
    orders: t`Current page: Orders`,
    other: t`Current page`
  };

  // M2 PR 9: the thread whose memory modal is open (owner-only surface).
  const [memoryThreadId, setMemoryThreadId] = useState<string | null>(null);
  const [isPreparingSend, setIsPreparingSend] = useState(false);
  const sendPreparationRef = useRef<symbol | null>(null);
  const [sendNotice, setSendNotice] = useState<{
    binding: ChatDraftBinding;
    revision: number;
    message: string;
    kind?: 'scope';
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const earlierScrollRef = useRef<{
    threadId: string;
    top: number;
    height: number;
  } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const activeThreadIdRef = useRef(activeThreadId);
  const previousThreadIdRef = useRef(activeThreadId);
  const composerVersionRef = useRef(0);
  activeThreadIdRef.current = activeThreadId;

  // C06: the RAM-only draft binding. The scope (host/user/session generation)
  // is fixed for this mount — the parent remounts this subtree whenever the
  // identity or generation changes — while the thread follows the selection.
  const draftScope = useMemo<ChatDraftScope>(() => {
    const draftUser = useUserState.getState();
    return {
      host: useLocalState.getState().getHost(),
      userId: draftUser.isLoggedIn() ? draftUser.userId() : undefined,
      sessionGeneration: useAIChatState.getState().sessionGeneration
    };
  }, []);
  const draftBinding = useMemo<ChatDraftBinding>(
    () => bindChatDrafts({ ...draftScope, threadId: activeThreadId }),
    [draftScope, activeThreadId]
  );
  const draftBindingRef = useRef<ChatDraftBinding>(draftBinding);
  draftBindingRef.current = draftBinding;
  useSyncExternalStore(subscribeChatDrafts, () => draftBinding.read().revision);
  const { text: inputValue, files: attachedFiles } = draftBinding.read();
  const attachments = draftBinding.readAttachments();
  const isUploading = attachments.some(
    (item) => item.status === 'queued' || item.status === 'uploading'
  );
  const hasUnresolvedAttachments = attachments.some(
    (item) => item.status !== 'ready'
  );
  const setInputValue = useCallback((text: string) => {
    draftBindingRef.current.writeText(text);
  }, []);

  useEffect(() => {
    if (previousThreadIdRef.current !== activeThreadId) {
      composerVersionRef.current += 1;
      sendPreparationRef.current = null;
      setIsPreparingSend(false);
      setIsApplyingScope(false);
      setMemoryThreadId(null);
      previousThreadIdRef.current = activeThreadId;
    }
  }, [activeThreadId]);

  const geometry = useAssistantGeometry(opened);
  const drawerWidth = geometry.width;
  const narrow = geometry.narrow;

  // Empty-state suggestion prompts (U1): real buttons, translated label AND
  // message text, machine-relevant prompts while a machine hint is active.
  const suggestionPrompts = suggestionKinds(routingHint?.machineName).map(
    (kind) => {
      switch (kind) {
        case 'machine-status':
          return {
            kind,
            label: t`Machine status`,
            message: t`What is the status of ${routingHint?.machineName ?? ''}?`
          };
        case 'machine-maintenance':
          return {
            kind,
            label: t`Maintenance history`,
            message: t`Show the maintenance history for ${routingHint?.machineName ?? ''}.`
          };
        case 'search-parts':
          return {
            kind,
            label: t`Search parts`,
            message: t`Search for parts in inventory`
          };
        case 'create-order':
          return {
            kind,
            label: t`Create order`,
            message: t`Help me create a purchase order`
          };
        case 'low-stock':
          return {
            kind,
            label: t`Low stock`,
            message: t`Show me low stock items`
          };
      }
    }
  );

  // U1: reading-position awareness — follow new content only when the
  // reader was already near the bottom; otherwise offer "Jump to latest".
  // Mantine exposes the actual viewport via its ref.
  const nearBottomRef = useRef(true);
  const followThreadRef = useRef(activeThreadId);
  const [showJumpLatest, setShowJumpLatest] = useState(false);

  useEffect(() => {
    const viewport = scrollAreaRef.current;
    if (!viewport) return;
    const onScroll = () => {
      nearBottomRef.current = isNearBottom(viewport);
      if (nearBottomRef.current) setShowJumpLatest(false);
    };
    viewport.addEventListener('scroll', onScroll);
    return () => viewport.removeEventListener('scroll', onScroll);
  }, [activeTab, activeThreadId, opened]);

  const jumpToLatest = useCallback(() => {
    const viewport = scrollAreaRef.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
    nearBottomRef.current = true;
    setShowJumpLatest(false);
  }, []);

  // Keep the visible position while prepending history; new replies follow
  // only a reader who was already at the bottom (thread switches always do).
  useLayoutEffect(() => {
    const viewport = scrollAreaRef.current;
    if (!viewport) return;
    const anchor = earlierScrollRef.current;
    if (anchor?.threadId === activeThreadId) {
      if (!loadingEarlier) {
        viewport.scrollTop = anchor.top + viewport.scrollHeight - anchor.height;
        earlierScrollRef.current = null;
      }
      return;
    }
    earlierScrollRef.current = null;
    const threadChanged = followThreadRef.current !== activeThreadId;
    if (threadChanged) {
      followThreadRef.current = activeThreadId;
      nearBottomRef.current = true;
      setShowJumpLatest(false);
    }
    const follow = shouldFollowNewContent({
      threadChanged,
      anchoredPrepend: false,
      nearBottom: nearBottomRef.current
    });
    if (follow) {
      viewport.scrollTop = viewport.scrollHeight;
      nearBottomRef.current = true;
      setShowJumpLatest(false);
    } else {
      setShowJumpLatest(true);
    }
  }, [messages, activeThreadId, loadingEarlier]);

  // Opening focus is a one-shot convenience. A delayed callback must never
  // reclaim focus after the reader moves into an answer or another control.
  const composerFocusCleanup = useRef<() => void>(() => {});
  const requestComposerFocus = useCallback(() => {
    composerFocusCleanup.current();
    const originalFocus = document.activeElement;
    const cancel = () => {
      window.clearTimeout(timer);
      document.removeEventListener('focusin', onFocus, true);
      document.removeEventListener('keydown', cancel, true);
      document.removeEventListener('pointerdown', cancel, true);
    };
    const onFocus = (event: FocusEvent) => {
      if (
        event.target !== inputRef.current &&
        event.target !== originalFocus &&
        event.target !== document.body
      )
        cancel();
    };
    const timer = window.setTimeout(() => {
      cancel();
      const active = document.activeElement;
      if (
        active === originalFocus ||
        active === document.body ||
        active === inputRef.current
      )
        inputRef.current?.focus();
    }, 100);
    document.addEventListener('focusin', onFocus, true);
    document.addEventListener('keydown', cancel, true);
    document.addEventListener('pointerdown', cancel, true);
    composerFocusCleanup.current = cancel;
  }, []);

  useEffect(() => {
    if (opened) requestComposerFocus();
    return () => composerFocusCleanup.current();
  }, [opened, requestComposerFocus]);

  // U1: consume the Ask/scan intent ONCE — select Chat and focus the
  // composer. Never an effect that keeps forcing the tab.
  useEffect(() => {
    if (opened && chatOpenIntent === 'chat') {
      useAIChatState.getState().consumeChatOpenIntent();
      setActiveTab('chat');
      requestComposerFocus();
    }
  }, [opened, chatOpenIntent, setActiveTab, requestComposerFocus]);

  // S2: a scope PUT seeded by the routing hint is in flight before a send.
  const [isApplyingScope, setIsApplyingScope] = useState(false);
  const scopeSendFocus = useRef<Element | null>(null);
  useLayoutEffect(() => {
    if (isApplyingScope || isLoading || isSyncing) return;
    const previous = scopeSendFocus.current;
    scopeSendFocus.current = null;
    if (
      opened &&
      previous &&
      (document.activeElement === document.body ||
        document.activeElement === previous)
    )
      inputRef.current?.focus();
  }, [opened, isApplyingScope, isLoading, isSyncing]);

  // P02/P33: why a Work-focused send was held back, if it was.
  const [workFocusLaunchError, setWorkFocusLaunchError] = useState<
    'unsupported' | 'refused' | null
  >(null);

  useEffect(() => {
    setIsApplyingScope(false);
    setWorkFocusLaunchError(null);
  }, [routingHint, pendingTargetHint, workFocusHint]);

  // Plan 3.1: the shared machine-context preflight. Typed send and every
  // voice entry path (microphone, consent completion, keyboard start and
  // restart) await this exact callback through the controller; it never
  // touches the typed draft or attachments and returns the authoritative
  // thread id for voice session creation.
  const applyScopeToThread = useCallback(
    async (scope: ExplicitMachineScope) => {
      setIsApplyingScope(true);
      try {
        return await setThreadScope(scope);
      } finally {
        setIsApplyingScope(false);
      }
    },
    [setThreadScope]
  );
  // Plan C: the focus half of the entry commit — one owner focus PUT with
  // the exact server ack (its version becomes the turn's capture stamp).
  const applyFocusToThread = useCallback(async (ref: ConversationFocusRef) => {
    let acknowledgedVersion: number | undefined;
    const result = await commitFocusChange(ref, (version) => {
      acknowledgedVersion = version;
    });
    return {
      ok: result.ok,
      code: result.code,
      version: acknowledgedVersion ?? useThreadFocusState.getState().version
    };
  }, []);
  const fetchScopeForPreflight = useCallback(
    async () => (await refreshThreadScope()) ?? null,
    [refreshThreadScope]
  );
  const contextPreflight = useCallback<ContextPreflight>(
    async (context) => {
      const threadId = activeThreadIdRef.current;
      const hintAtEntry = useAIChatState.getState().targetHint;
      const outcome = await executeContextPreflight({
        hint: targetHint ?? null,
        scopeCapable,
        focusCapable,
        threadShared: activeThreadShared,
        busy: isApplyingScope,
        confirmed: activeScope
          ? {
              mode: activeScope.mode,
              displayLabel: activeScope.displayLabel,
              machineIds: activeScope.machineIds ?? null,
              machineCount: activeScope.machineCount ?? null
            }
          : null,
        threadId,
        applyScope: applyScopeToThread,
        fetchScope: fetchScopeForPreflight,
        applyFocus: applyFocusToThread,
        contextUnchanged: () =>
          activeThreadIdRef.current === threadId &&
          useAIChatState.getState().targetHint === hintAtEntry
      });
      if (
        !outcome.ok &&
        outcome.code !== 'context_changed' &&
        outcome.code !== 'in-flight' &&
        // Typed machine refusals use the inline alert beside the kept draft.
        !(context.entry === 'typed' && routingHint)
      ) {
        const machine = targetHint?.kind === 'machine';
        showNotification({
          color: 'yellow',
          message:
            context.entry === 'voice'
              ? machine
                ? t`Could not confirm the machine context — voice did not start.`
                : t`Could not confirm the target — voice did not start.`
              : machine
                ? t`Could not confirm the machine context — nothing was sent.`
                : t`Could not confirm the target — nothing was sent.`
        });
      }
      return outcome;
    },
    [
      targetHint,
      routingHint,
      scopeCapable,
      focusCapable,
      activeThreadShared,
      isApplyingScope,
      activeScope,
      applyScopeToThread,
      fetchScopeForPreflight,
      applyFocusToThread
    ]
  );
  // Registration is identity-checked in the store, so a StrictMode
  // double-mount cleanup can never drop the surviving registration and a
  // stale callback is replaced on every render through the ref below.
  const contextPreflightRef = useRef(contextPreflight);
  contextPreflightRef.current = contextPreflight;
  useEffect(
    () =>
      registerContextPreflight((context) =>
        contextPreflightRef.current(context)
      ),
    []
  );

  // Handle sending a message
  const handleSendMessage = useCallback(
    async (messageText?: string): Promise<boolean> => {
      const binding = draftBindingRef.current;
      const snapshot = binding.read();
      const workAtEntry = workFocusHint;
      const threadAtEntry = activeThreadIdRef.current;
      const sessionAtEntry = useAIChatState.getState().sessionGeneration;
      const fromComposer = messageText === undefined;
      const text = fromComposer ? snapshot.text : messageText;
      if (!text.trim()) return false;
      if (binding.readAttachments().some((item) => item.status !== 'ready')) {
        setSendNotice({
          binding,
          revision: snapshot.revision,
          message: t`Finish uploading or remove failed attachments before sending.`
        });
        return false;
      }
      if (
        isLoading ||
        isSyncing ||
        isApplyingScope ||
        sendPreparationRef.current !== null
      )
        return false;
      // Reserve synchronously, before preflight can yield to another Enter,
      // click or suggested follow-up. A stale finally cannot unlock a new send.
      const preparation = Symbol();
      sendPreparationRef.current = preparation;
      setIsPreparingSend(true);
      setSendNotice(null);
      const version = composerVersionRef.current;
      try {
        // C06: immutable text/files plus the draft revision and identity BEFORE
        // any await — only the exact submitted revision may ever be consumed.
        // Suggestions and question answers are their own messages. They never
        // implicitly submit or consume an unrelated typed draft or its files.
        const submittedFiles = fromComposer ? snapshot.files : [];
        const fileIds = submittedFiles.map((f) => f.file_id);
        const submittedRevision = snapshot.revision;
        // C01: capture the pending target-intent token BEFORE any await so a
        // late completion can only ever consume THIS send's own intent.
        const intentToken = useAIChatState.getState().targetIntentToken;
        // S2 + plan 3.1: the machine hint is now a SCOPE SEED, never message
        // text — the shared preflight commits it as the thread's explicit
        // assets scope before the first send. An explicit selection that
        // cannot be confirmed BLOCKS this send (fail closed, per-turn
        // authorization is still the real boundary); the draft and
        // attachments are preserved and the typed text always goes out
        // byte-identical when the send proceeds.
        const outcome = await contextPreflight({
          threadId: activeThreadIdRef.current,
          entry: 'typed'
        });
        if (!outcome.ok) {
          if (
            routingHint &&
            outcome.code !== 'context_changed' &&
            outcome.code !== 'in-flight'
          ) {
            setSendNotice({
              binding,
              revision: snapshot.revision,
              kind: 'scope',
              message: t`No message was sent. Your draft and attachments are kept. Retry sending, or remove the machine selection before sending without that context.`
            });
          }
          return false;
        }
        if (version !== composerVersionRef.current) return false;
        let workTurnFocus:
          | { receipt: string; workFocus: ChatWorkFocus }
          | undefined;
        if (workAtEntry) {
          // P02/P33: the server must acknowledge this exact Work at the version
          // the page showed before anything is sent. Every failure keeps the
          // draft and attachments and sends nothing; removing the Work is the
          // user's explicit choice to ask without it.
          if (
            !workFocusCapable ||
            (!!(workAtEntry.component || workAtEntry.evidence) &&
              !referenceFocusCapable) ||
            (workAtEntry.requestId !== undefined && !requestFocusCapable) ||
            activeThreadShared
          ) {
            setWorkFocusLaunchError('unsupported');
            return false;
          }
          let acknowledged: Awaited<ReturnType<typeof acknowledgeWorkFocus>>;
          try {
            acknowledged = await acknowledgeWorkFocus(
              workAtEntry.workOrderId,
              workAtEntry.lifecycleVersion,
              workAtEntry.requestId,
              workAtEntry.requestVersion,
              workAtEntry.component,
              workAtEntry.evidence
            );
          } catch {
            acknowledged = { ok: false, code: 'error' };
          }
          // Closing, switching or a new launch during the request voids it.
          if (
            version !== composerVersionRef.current ||
            useAIChatState.getState().workFocusHint !== workAtEntry
          )
            return false;
          if (!acknowledged.ok) {
            setWorkFocusLaunchError('refused');
            return false;
          }
          setWorkFocusLaunchError(null);
          const identity = acknowledged.acknowledgement.focus;
          workTurnFocus = {
            receipt: acknowledged.acknowledgement.receipt,
            workFocus: {
              identity,
              label: workAtEntry.label,
              returnPath: focusReturnPath(identity, workAtEntry.returnPath)
            }
          };
        }
        if (
          activeThreadIdRef.current !== threadAtEntry ||
          useAIChatState.getState().sessionGeneration !== sessionAtEntry ||
          useAIChatState.getState().workFocusHint !== workAtEntry
        )
          return false;
        // Closing, switching or a NEWER intent during the awaited preflight
        // cancels this prepared send: the newer intent and the draft survive.
        const currentToken = useAIChatState.getState().targetIntentToken;
        if (currentToken?.generation !== intentToken?.generation) return false;
        // C06: a draft edit (or a dropped/reset binding) during the awaited
        // preflight cancels this prepared send — the newer draft survives.
        if (!binding.isCurrent()) return false;
        if (binding.read().revision !== submittedRevision) {
          setSendNotice({
            binding,
            revision: binding.read().revision,
            message: t`Your draft changed while the target was being confirmed. Send it again when you are ready.`
          });
          return false;
        }
        let admitted = false;
        void sendMessage(
          text,
          fileIds.length > 0 ? fileIds : undefined,
          workTurnFocus
            ? { ...workTurnFocus, focusVersion: outcome.focusVersion }
            : outcome.focusVersion,
          () => {
            admitted = true;
            if (workAtEntry && workAtEntry.requestId === undefined)
              clearWorkFocus();
            // C01: compare-and-consume — a successful typed entry consumes exactly
            // its own pending machine/task/work-order intent, once.
            if (intentToken) useAIChatState.getState().clearHint(intentToken);
            // C06: consume only the matching submitted revision.
            if (fromComposer) binding.consume(submittedRevision);
          }
        ).catch(() => {
          if (
            !admitted &&
            binding.isCurrent() &&
            draftBindingRef.current === binding
          ) {
            setSendNotice({
              binding,
              revision: binding.read().revision,
              message: t`The message was not sent. Your draft has been kept.`
            });
          }
        });
        if (!admitted) {
          setSendNotice({
            binding,
            revision: binding.read().revision,
            message: t`The message was not sent. Your draft has been kept.`
          });
        }
        return admitted;
      } finally {
        if (sendPreparationRef.current === preparation) {
          sendPreparationRef.current = null;
          setIsPreparingSend(false);
        }
      }
    },
    [
      isLoading,
      isSyncing,
      isApplyingScope,
      contextPreflight,
      sendMessage,
      activeThreadShared,
      routingHint,
      workFocusHint,
      workFocusCapable,
      referenceFocusCapable,
      requestFocusCapable,
      acknowledgeWorkFocus,
      clearWorkFocus
    ]
  );

  const uploadAttachment = useCallback(
    async (binding: ChatDraftBinding, id: string) => {
      const attempt = binding.startUpload(id);
      if (!attempt) return;
      const result = await uploadFile(attempt.file, () =>
        binding.isUploadCurrent(attempt)
      );
      binding.finishUpload(attempt, result);
    },
    [uploadFile]
  );

  // Handle file selection
  const handleFileSelect = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files;
      if (!files || files.length === 0) return;

      const binding = draftBindingRef.current;
      // All selections become visible before the first network await. File
      // attempt identity is independent of text edits and current selection.
      const ids = binding.queueAttachments(Array.from(files));
      event.currentTarget.value = '';
      for (const id of ids) {
        await uploadAttachment(binding, id);
      }
    },
    [uploadAttachment]
  );

  // Remove an attached file
  const removeAttachedFile = useCallback((id: string) => {
    draftBindingRef.current.removeAttachment(id);
  }, []);

  // Selecting a thread reads its own RAM draft. A switch refused during an
  // active turn must not discard the current draft or its attachment queue.
  const handleSwitchThread = useCallback(
    (threadId: string) => {
      switchThread(threadId);
    },
    [switchThread]
  );

  const targetConversationStarterRef = useRef(
    (_target: AIChatTargetHint): boolean => false
  );
  targetConversationStarterRef.current = (target) => {
    const user = useUserState.getState();
    if (
      draftScope.host !== useLocalState.getState().getHost() ||
      draftScope.userId !== (user.isLoggedIn() ? user.userId() : undefined) ||
      draftScope.sessionGeneration !==
        useAIChatState.getState().sessionGeneration ||
      !draftBindingRef.current.isCurrent() ||
      isLoading ||
      isSyncing ||
      isApplyingScope ||
      sendPreparationRef.current !== null ||
      activeThreadDeletionPending ||
      useVoiceSessionState.getState().session !== null
    )
      return false;
    const previous = activeThreadIdRef.current;
    const next = createNewThread();
    if (next === previous) return false;
    // Preserve the old thread's draft. The new conversation gets its own
    // pending candidate, confirmed by the existing preflight before any send.
    // In particular, a refused target never widens the old analysis scope.
    useAIChatState.getState().openWithTargetHint(target);
    useAIChatState.getState().bindHint(next);
    useVoiceSurfaceState.getState().closeFullscreen();
    return true;
  };
  useEffect(
    () =>
      registerTargetConversationStarter((target) =>
        targetConversationStarterRef.current(target)
      ),
    []
  );

  const handleNewThread = useCallback(() => {
    createNewThread();
  }, [createNewThread]);

  // S2: a machine hint aimed at a shared (read-only) thread hops to a new
  // owned thread — the composer is disabled on shared transcripts, so the
  // ask could never be sent there.
  useEffect(() => {
    // C01: every contextual Ask (machine/task/work-order) prepares the owned
    // conversation retaining its intended canonical target; only a machine
    // target seeds the analysis scope (openWithTargetHint keeps that rule).
    const intendedTarget = pendingTargetHint;
    if (!opened || (!intendedTarget && !workFocusHint)) return;
    if (activeThreadShared) {
      const id = createNewThread();
      // createNewThread can synchronously clear the hints — re-open with the
      // target captured above so the prepared conversation keeps it.
      if (workFocusHint)
        useAIChatState
          .getState()
          .openWithWorkFocus(workFocusHint, returnFocusTarget);
      else if (intendedTarget)
        useAIChatState.getState().openWithTargetHint(intendedTarget);
      useAIChatState.getState().bindHint(id);
    } else {
      useAIChatState.getState().bindHint(activeThreadId);
    }
  }, [
    opened,
    pendingTargetHint,
    workFocusHint,
    returnFocusTarget,
    activeThreadShared,
    activeThreadId,
    createNewThread
  ]);

  const handleClearChat = useCallback(() => {
    // C06: explicit Clear drops this thread's private draft and ready
    // references (and kills older bindings) — sibling drafts survive.
    draftBindingRef.current.clear();
    clearChat();
  }, [clearChat]);

  const handleDeleteThread = useCallback(
    (threadId: string, forgetConfirmed = false) => {
      if (memoryThreadId === threadId) setMemoryThreadId(null);
      return deleteThread(threadId, forgetConfirmed);
    },
    [deleteThread, memoryThreadId]
  );

  // U1: Enter sends (Shift+Enter = newline) — never mid-IME-composition.
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (
        shouldSendOnEnter({
          key: event.key,
          shiftKey: event.shiftKey,
          isComposing: (event.nativeEvent as KeyboardEvent).isComposing,
          keyCode: event.keyCode
        })
      ) {
        event.preventDefault();
        handleSendMessage();
      }
    },
    [handleSendMessage]
  );

  // C3: one start-eligibility definition shared by the composer mic and the
  // keyboard shortcut (via useVoiceSurfaceState.requestStart). Ending or
  // muting an existing session is never blocked by this.
  const startBlockedReason = voiceStartBlockReason({
    sharedThread: activeThreadShared,
    deletionPending: activeThreadDeletionPending,
    applyingScope: isApplyingScope || isPreparingSend,
    turnInFlight: isLoading,
    syncing: isSyncing
  });
  useEffect(() => {
    useVoiceSurfaceState.getState().setStartBlockedReason(startBlockedReason);
  }, [startBlockedReason]);
  useEffect(
    () => () => {
      useVoiceSurfaceState.getState().setStartBlockedReason(null);
    },
    []
  );

  const voiceActive = !['unavailable', 'ready', 'error'].includes(voice.state);
  const voiceControlProps = {
    state: voice.state,
    error: voice.error,
    muted: voice.muted,
    webrtcPreview: voice.session?.webrtc_preview ?? true,
    startBlockedReason,
    // The single voice.start() entry path (requestStart → consent).
    onStart: () => void voice.start(),
    onEnd: () => void voice.end(),
    onCancel: () => void voice.cancel(),
    onToggleMute: voice.toggleMute,
    onConfirmTranscript: () => void voice.confirmPending(),
    onDiscardTranscript: voice.discardPending
  };

  const hasMessages = messages.length > 0;

  return (
    <>
      <AssistantSurface
        opened={opened}
        returnFocusTarget={returnFocusTarget}
        modal={geometry.modal}
        suspended={consentOpen || voiceFullscreen || evidenceOpen}
        width={drawerWidth}
        height={geometry.viewportHeight}
        top={geometry.viewportTop}
        left={geometry.viewportLeft}
        title={t`AI Assistant`}
        onClose={handleClose}
      >
        {!geometry.modal && (
          <Box
            className={workspaceStyles.separator}
            role='separator'
            aria-orientation='vertical'
            aria-label={t`Resize chat panel`}
            aria-valuenow={drawerWidth}
            aria-valuemin={ASSISTANT_MIN_WIDTH}
            aria-valuemax={geometry.maximum}
            tabIndex={0}
            onPointerDown={geometry.onPointerDown}
            onKeyDown={geometry.onKeyDown}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: 6,
              height: '100%',
              cursor: 'col-resize',
              touchAction: 'none',
              zIndex: 1
            }}
          />
        )}
        <Boundary label='AIChatDrawer'>
          <AssistantHeader
            syncing={isSyncing}
            busy={isLoading || isPreparingSend || activeThreadDeletionPending}
            expanded={geometry.expanded}
            canExpand={!narrow}
            onSync={() => void syncThreads()}
            onNew={handleNewThread}
            onClose={handleClose}
            onExpand={geometry.toggleExpanded}
            conversation={
              activeTab === 'chat' ? (
                <ThreadSelector
                  threads={threads}
                  sharedThreads={sharedThreads}
                  activeThreadId={activeThreadId}
                  onSelectThread={handleSwitchThread}
                  onNewThread={handleNewThread}
                  onDeleteThread={handleDeleteThread}
                  onPrepareDeletion={async () => {
                    setMemoryThreadId(null);
                    return prepareThreadDeletion();
                  }}
                  onDeletePage={deleteThreadPage}
                  onExport={exportTranscripts}
                  bulkDeletionDisabled={
                    Boolean(voice.session) || voice.transport !== 'off'
                  }
                  onRenameThread={renameThread}
                  onShareThread={(threadId, username, revoke) =>
                    revoke
                      ? revokeThreadShare(threadId, username)
                      : shareThread(threadId, username)
                  }
                  onInspectMemory={setMemoryThreadId}
                  disabled={
                    isLoading ||
                    isSyncing ||
                    isApplyingScope ||
                    Boolean(voice.session) ||
                    voice.transport === 'connecting'
                  }
                />
              ) : null
            }
            navigation={
              <Tabs
                value={activeTab}
                onChange={(v) => setActiveTab((v as AIChatDrawerTab) || 'chat')}
                variant='pills'
                styles={{
                  list: { flexWrap: 'nowrap', overflowX: 'auto' },
                  tab: { padding: '8px 10px', minHeight: 40 }
                }}
              >
                <Tabs.List>
                  <Tabs.Tab value='chat'>{t`Chat`}</Tabs.Tab>
                  <Tabs.Tab value='approvals'>
                    <Group gap={6} wrap='nowrap'>
                      <Text size='sm'>{t`Approvals`}</Text>
                      {/* C1: an attention DOT — never a numeric aggregate
                          (the server count is partial and can be
                          unavailable). */}
                      {needsAttention && (
                        <Badge
                          size='xs'
                          variant='filled'
                          color='red'
                          aria-label={t`Actions need review`}
                          data-testid='approvals-attention-indicator'
                        >
                          {'\u2022'}
                        </Badge>
                      )}
                    </Group>
                  </Tabs.Tab>
                  <Tabs.Tab value='history'>{t`History`}</Tabs.Tab>
                  <Tabs.Tab value='mail'>{t`Mail`}</Tabs.Tab>
                </Tabs.List>
              </Tabs>
            }
          />
          {activeTab === 'chat' && (
            <ConversationContextBar
              scope={activeScope}
              pending={
                (hintThreadId === null || hintThreadId === activeThreadId) &&
                !(hintRedundant && pendingTargetHint?.kind === 'machine')
                  ? pendingTargetHint
                  : undefined
              }
              routingHint={hintRedundant ? undefined : routingHint}
              dismiss={() => clearRoutingHint()}
            />
          )}

          {/* M2 PR 9: "What this chat remembers" — the only place the
            summary body renders (GR-16). */}
          <ThreadMemoryModal
            threadId={memoryThreadId}
            host={aiHost}
            opened={memoryThreadId !== null}
            onClose={() => setMemoryThreadId(null)}
          />

          {/* C3: the active-session safety strip stays reachable on every
            drawer tab; on Chat it renders near the composer instead. */}
          {!voiceFullscreen && voiceActive && activeTab !== 'chat' && (
            <Box px='md' py='xs'>
              <VoiceActiveStrip {...voiceControlProps} />
            </Box>
          )}
          {/* Main content area */}
          <ChatTranscript
            viewportRef={scrollAreaRef}
            active={
              opened &&
              activeTab === 'chat' &&
              !voiceFullscreen &&
              !evidenceOpen &&
              !consentOpen
            }
            busy={isLoading}
            threadId={activeThreadId}
          >
            {activeTab === 'chat' && (
              <Box p='md'>
                <LegacyChatStorageNotice />
                {/* C1: at most one unobtrusive navigation affordance — it
                  switches tabs and never acts on anything. */}
                {needsAttention && (
                  <Button
                    size='compact-sm'
                    variant='light'
                    mb='xs'
                    onClick={() => setActiveTab('approvals')}
                    data-testid='review-in-approvals'
                  >{t`Review in Approvals`}</Button>
                )}
                {hasEarlierMessages && (
                  <Button
                    variant='subtle'
                    fullWidth
                    loading={loadingEarlier}
                    disabled={isLoading}
                    onClick={() => {
                      const viewport = scrollAreaRef.current;
                      if (viewport)
                        earlierScrollRef.current = {
                          threadId: activeThreadId,
                          top: viewport.scrollTop,
                          height: viewport.scrollHeight
                        };
                      void loadEarlierMessages();
                    }}
                  >{t`Load earlier messages`}</Button>
                )}
                {/* Welcome message when no messages */}
                {!hasMessages && (
                  <Box ta='center' py='xl'>
                    <Box
                      mx='auto'
                      mb='md'
                      style={{
                        width: 64,
                        height: 64,
                        borderRadius: '50%',
                        background: `linear-gradient(135deg, ${theme.colors.blue[1]}, ${theme.colors.violet[1]})`,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center'
                      }}
                    >
                      <IconSparkles size={32} color={theme.colors.blue[5]} />
                    </Box>
                    <Text size='lg' fw={600} mb='xs'>
                      {t`How can I help?`}
                    </Text>
                    <Text size='sm' c='dimmed' maw={280} mx='auto' mb='lg'>
                      {t`Ask about assets, maintenance or inventory. Review sources and approvals before acting.`}
                    </Text>

                    {/* U1: semantic suggestion buttons (translated label AND
                      message; machine prompts while a machine hint exists). */}
                    <Group gap='xs' justify='center'>
                      {suggestionPrompts.map((suggestion) => (
                        <Button
                          key={suggestion.kind}
                          size='compact-sm'
                          variant='light'
                          radius='xl'
                          data-testid='ai-chat-suggestion'
                          disabled={
                            isSyncing ||
                            isPreparingSend ||
                            hasUnresolvedAttachments ||
                            activeThreadShared ||
                            activeThreadDeletionPending
                          }
                          onClick={() => handleSendMessage(suggestion.message)}
                        >
                          {suggestion.label}
                        </Button>
                      ))}
                    </Group>
                  </Box>
                )}

                {/* Message list */}
                {messages.map((message, index) => (
                  <ChatMessageItem
                    key={message.id}
                    message={message}
                    threadId={activeThreadId}
                    aiHost={aiHost}
                    onRegenerate={
                      // A regenerate is a NEW audited turn (fresh idempotency
                      // key) appended to the thread — never an overwrite.
                      message.role === 'assistant' &&
                      index === messages.length - 1
                        ? resendLastTurn
                        : undefined
                    }
                    questionArmed={
                      !!message.question &&
                      pendingQuestion?.interrupt_id ===
                        message.question.interrupt_id
                    }
                    questionAnsweredLocally={
                      !!message.question &&
                      answeredQuestionIds.has(message.question.interrupt_id)
                    }
                    questionResolution={questionResolutions.get(
                      message.question?.interrupt_id ?? ''
                    )}
                    onQuestionAnswer={(text) => handleSendMessage(text)}
                  />
                ))}

                {/* C1: action proposals and the review inbox live in
                  Approvals — never on the transcript tabs. */}

                {/* Error message */}
                {error && (
                  <Paper p='sm' radius='md' withBorder mb='md'>
                    <Text size='sm' c='red' role='alert'>
                      {error}
                    </Text>
                    {/* S1: a scope-version conflict keeps the bounced turn
                      for one-click resend after the refreshed scope. */}
                    {scopeConflict && (
                      <Button
                        size='compact-xs'
                        variant='light'
                        color='red'
                        mt={6}
                        onClick={resendLastTurn}
                        data-testid='ai-chat-scope-resend'
                      >
                        {t`Send again`}
                      </Button>
                    )}
                  </Paper>
                )}
              </Box>
            )}

            {activeTab === 'mail' && (
              <MailboxPanel onDraft={() => setActiveTab('approvals')} />
            )}
            {activeTab === 'approvals' && (
              <Box p='md'>
                {/* C1: the single focused decision card lives here (and in
                  the hands-free surface — never two at once). */}
                {!voiceFullscreen && (
                  <VoiceDecisionCard
                    key={voiceDecision?.decision_id ?? 'none'}
                  />
                )}
                <ChatActionProposalList
                  key={activeThreadId ?? 'new'}
                  {...proposals}
                />
                {/* C1: resolved actions moved here from History behind a
                  truthful local filter. */}
                <Text
                  size='sm'
                  fw={600}
                  mb={4}
                  data-testid='approval-review-inbox-heading'
                >{t`Review inbox`}</Text>
                <Group gap='xs' mb='md'>
                  <Button
                    size='compact-sm'
                    variant={approvalView === 'open' ? 'light' : 'subtle'}
                    onClick={() => setApprovalView('open')}
                    data-testid='approvals-filter-needs-review'
                  >{t`Needs review`}</Button>
                  <Button
                    size='compact-sm'
                    variant={approvalView === 'resolved' ? 'light' : 'subtle'}
                    onClick={() => setApprovalView('resolved')}
                    data-testid='approvals-filter-resolved'
                  >{t`Resolved`}</Button>
                </Group>
                {approvalView === 'open' ? (
                  <ApprovalInboxPanel
                    statuses={[
                      'pending',
                      'in_review',
                      'changes_requested',
                      'approved',
                      'executing'
                    ]}
                    emptyText={t`No approval requests waiting for review`}
                  />
                ) : (
                  <ApprovalInboxPanel
                    statuses={[
                      'succeeded',
                      'denied',
                      'failed',
                      'expired',
                      'canceled'
                    ]}
                    emptyText={t`No resolved approval requests yet`}
                  />
                )}
              </Box>
            )}

            {activeTab === 'history' && (
              <ThreadHistoryPanel
                threads={threads}
                activeThreadId={activeThreadId}
                searchThreads={searchThreads}
                hasMoreThreads={hasMoreThreads}
                loadingMoreThreads={loadingMoreThreads || isSyncing}
                onLoadMore={() => void loadMoreThreads()}
                onResume={(threadId) => {
                  switchThread(threadId);
                  setActiveTab('chat');
                }}
              />
            )}
          </ChatTranscript>

          {/* Composer and context remain reachable independently of the transcript. */}
          {activeTab === 'chat' && (
            <Box
              p='sm'
              data-testid='ai-chat-composer-region'
              style={{
                flexShrink: 1,
                minHeight: 100,
                maxHeight: '55%',
                overflowY: 'auto',
                borderTop: '1px solid var(--mantine-color-default-border)',
                background: 'var(--mantine-color-body)'
              }}
            >
              {/* U1: explicit jump control while the reader is scrolled
                away from the live edge. */}
              {showJumpLatest && (
                <Button
                  size='compact-sm'
                  variant='light'
                  radius='xl'
                  mb='xs'
                  leftSection={<IconArrowDown size={14} />}
                  onClick={jumpToLatest}
                  data-testid='ai-chat-jump-latest'
                >{t`Jump to latest`}</Button>
              )}

              <ChatAttachmentQueue
                attachments={attachments}
                disabled={
                  isLoading ||
                  isSyncing ||
                  activeThreadShared ||
                  activeThreadDeletionPending
                }
                onRetry={(id) =>
                  void uploadAttachment(draftBindingRef.current, id)
                }
                onRemove={removeAttachedFile}
              />
              {sendNotice?.binding === draftBinding &&
                sendNotice.revision === draftBinding.read().revision &&
                (sendNotice.kind === 'scope' ? (
                  routingHint && (
                    <Alert
                      color='red'
                      title={t`Machine context not confirmed`}
                      data-testid='ai-chat-send-notice'
                    >
                      <Text size='sm'>{sendNotice.message}</Text>
                    </Alert>
                  )
                ) : (
                  <Text
                    component='output'
                    aria-live='polite'
                    size='xs'
                    c='red'
                    mb='xs'
                    data-testid='ai-chat-send-notice'
                  >
                    {sendNotice.message}
                  </Text>
                ))}

              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type='file'
                multiple
                accept='.pdf,.png,.jpg,.jpeg,.xlsx,.csv,.docx'
                style={{ display: 'none' }}
                onChange={handleFileSelect}
              />

              {workFocusHint && (
                <style>{`
                #ai-chat-drawer { --mantine-color-dimmed: var(--aimms-work-dimmed); }
                #ai-chat-drawer .mantine-Alert-title,
                #ai-chat-drawer .mantine-Alert-message { color: var(--mantine-color-text); }
                @media (max-width: 47.99em) {
                  #ai-chat-drawer .mantine-ActionIcon-root,
                  #ai-chat-drawer .mantine-CloseButton-root,
                  #ai-chat-drawer a:not(:where(p, li, span, label, td, th, h1, h2, h3, h4, h5, h6) a) { min-width: 44px; min-height: 44px; }
                  #ai-chat-drawer a:not(:where(p, li, span, label, td, th, h1, h2, h3, h4, h5, h6) a) { display: inline-flex; align-items: center; }
                }
              `}</style>
              )}
              {workFocusHint && (
                <Group
                  gap='xs'
                  mb={4}
                  wrap='nowrap'
                  align='flex-start'
                  data-testid='ai-chat-work-focus'
                  style={{ minWidth: 0 }}
                >
                  <Box component='details' style={{ flex: 1, minWidth: 0 }}>
                    <Text
                      component='summary'
                      size='sm'
                      style={{ cursor: 'pointer' }}
                    >
                      {workFocusHint.component
                        ? t`Asking about reference position`
                        : workFocusHint.evidence
                          ? t`Asking about recorded evidence`
                          : workFocusHint.workOrderId != null
                            ? t`Work order ${workFocusHint.workOrderId}`
                            : t`Asking about work order`}
                      {workFocusHint.requestId
                        ? ` · ${t`Request ${workFocusHint.requestId}`}`
                        : ''}
                    </Text>
                    <Badge
                      variant='light'
                      color='teal'
                      radius='sm'
                      tt='none'
                      mt='xs'
                      styles={{
                        root: { height: 'auto', maxWidth: '100%' },
                        label: {
                          lineHeight: 'var(--mantine-line-height)',
                          whiteSpace: 'normal',
                          overflow: 'visible',
                          overflowWrap: 'anywhere'
                        }
                      }}
                    >
                      {workFocusHint.component
                        ? t`Asking about reference position`
                        : workFocusHint.evidence
                          ? t`Asking about recorded evidence`
                          : t`Asking about work order`}
                      : {workFocusHint.label}
                    </Badge>
                  </Box>
                  <ActionIcon
                    size='sm'
                    variant='transparent'
                    color='teal'
                    aria-label={
                      workFocusHint.component
                        ? t`Remove reference position`
                        : workFocusHint.evidence
                          ? t`Remove evidence`
                          : t`Remove work order`
                    }
                    onClick={clearWorkFocus}
                  >
                    <IconX size={14} />
                  </ActionIcon>
                </Group>
              )}
              {workFocusHint && workFocusLaunchError && (
                <Alert
                  color='red'
                  title={
                    workFocusHint.requestId !== undefined
                      ? t`Investigation request not confirmed`
                      : workFocusHint.component || workFocusHint.evidence
                        ? t`Reference not confirmed`
                        : t`Work order not confirmed`
                  }
                >
                  <Text size='sm'>
                    {workFocusHint.component || workFocusHint.evidence
                      ? t`No message was sent: the exact reference could not be confirmed. Your draft and attachments are kept. Reload the source if it has changed, retry, or remove the selection to ask without it.`
                      : workFocusHint.requestId !== undefined
                        ? t`No message was sent: this investigation request could not be confirmed at the shown revision. Your draft and attachments are kept. Retry sending, reload Diagnose if the request has changed, or remove the selection to ask without it.`
                        : workFocusLaunchError === 'unsupported'
                          ? t`No message was sent: this assistant cannot confirm a work order yet. Your draft and attachments are kept. Remove the work order to ask without it.`
                          : t`No message was sent: the work order could not be confirmed as shown. Your draft and attachments are kept. Retry sending, reload the work order if it has changed, or remove the work order to ask without it.`}
                  </Text>
                </Alert>
              )}
              <ChatComposer
                maxRows={
                  geometry.viewportHeight < 400
                    ? 1
                    : geometry.viewportHeight < 600
                      ? voiceActive
                        ? 2
                        : 3
                      : 6
                }
                inputRef={inputRef}
                value={inputValue}
                onChange={setInputValue}
                onKeyDown={handleKeyDown}
                placeholder={
                  activeThreadShared
                    ? t`Shared conversation — read-only`
                    : pendingQuestion
                      ? t`Answer the question above, or ask something else...`
                      : attachedFiles.length > 0
                        ? t`Add a message about attached files...`
                        : t`Type a message...`
                }
                disabled={
                  isLoading ||
                  (Boolean(workFocusHint) && isPreparingSend) ||
                  isSyncing ||
                  activeThreadShared ||
                  activeThreadDeletionPending
                }
                attachDisabled={
                  isLoading ||
                  (Boolean(workFocusHint) && isPreparingSend) ||
                  isUploading ||
                  isSyncing ||
                  activeThreadShared ||
                  activeThreadDeletionPending
                }
                uploading={isUploading}
                loading={isLoading}
                sendDisabled={
                  !inputValue.trim() ||
                  hasUnresolvedAttachments ||
                  isPreparingSend ||
                  isSyncing ||
                  activeThreadShared ||
                  activeThreadDeletionPending ||
                  isApplyingScope
                }
                onAttach={() => fileInputRef.current?.click()}
                onSend={() => void handleSendMessage()}
                onCancel={cancelRequest}
                voice={<VoiceComposerControl {...voiceControlProps} />}
              />

              {voiceActive && !voiceFullscreen && (
                <VoiceDock {...voiceControlProps} />
              )}
              {/* U1: a friendly page label — never the raw URL path. */}
              <Text size='xs' c='dimmed' data-testid='assistant-page-context'>
                {pageContextLabels[pageContextKind(location.pathname)]}
              </Text>
            </Box>
          )}
        </Boundary>
      </AssistantSurface>
    </>
  );
}
