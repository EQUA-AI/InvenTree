import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  CopyButton,
  Group,
  Loader,
  Menu,
  Paper,
  ScrollArea,
  Stack,
  Tabs,
  Text,
  TextInput,
  Textarea,
  Tooltip,
  Transition,
  UnstyledButton,
  useComputedColorScheme,
  useMantineTheme
} from '@mantine/core';
import { useLocalStorage, useMediaQuery } from '@mantine/hooks';
import { showNotification } from '@mantine/notifications';
import {
  IconArrowDown,
  IconBrain,
  IconCheck,
  IconChevronDown,
  IconCopy,
  IconEye,
  IconFileExport,
  IconGripVertical,
  IconMessagePlus,
  IconMessages,
  IconPaperclip,
  IconPencil,
  IconPlayerStop,
  IconRefresh,
  IconRobot,
  IconSearch,
  IconSend,
  IconShare2,
  IconSparkles,
  IconThumbDown,
  IconThumbUp,
  IconTrash,
  IconUser,
  IconX
} from '@tabler/icons-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState
} from 'react';
import { Link, useLocation } from 'react-router-dom';

import { Boundary } from '@lib/components/Boundary';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../App';
import {
  CHAT_INDEX_PREFIX,
  chatIndexKey,
  chatInvalidationKey
} from '../../functions/chatThreadCache';
import type {
  OwnedDeletionPage,
  OwnedDeletionPlan
} from '../../functions/ownedThreadDeletion';
import type { TranscriptExportCounts } from '../../functions/transcriptExport';
import {
  type ChatMessage,
  type ChatThread,
  type QuestionPayload,
  type QuestionResolution,
  type UploadedFile,
  useAIChat
} from '../../hooks/UseAIChat';
import { useChatProposals } from '../../hooks/useChatProposals';
import { useVoiceLiveSession } from '../../hooks/useVoiceLiveSession';
import { useAIChatState } from '../../states/AIChatState';
import { useEvidenceViewerState } from '../../states/EvidenceViewerState';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import { useVoiceDecisionState } from '../../states/VoiceDecisionState';
import {
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

/**
 * Test marker for the real scroll viewport. Spread into `viewportProps`
 * (the spread bypasses excess-attribute typing; `style` anchors the
 * weak-type check).
 */
const VIEWPORT_MARKERS = {
  'data-testid': 'ai-chat-messages-viewport',
  style: {}
};
import { ApprovalInboxPanel } from '../ai/ApprovalInboxPanel';
import { ChatActionProposalList } from '../ai/ChatActionProposals';
import { MailboxPanel } from '../ai/MailboxPanel';
import { QuestionCard } from '../ai/QuestionCard';
import { VoiceDecisionCard } from '../ai/VoiceDecisionCard';
import { VoiceTranscript } from '../ai/VoiceTranscript';
import {
  VoiceActiveStrip,
  VoiceComposerControl
} from '../ai/voice/VoiceComposerControl';
import { VoiceExperienceControls } from '../ai/voice/VoiceExperienceControls';
import { VOICE_SHORTCUT } from '../ai/voice/voiceShortcuts';
import { voiceStartBlockReason } from '../ai/voice/voiceStartEligibility';
import { AnalysisContextLine } from '../aichat/ActiveScopeBanner';
import { AssistantSurface } from '../aichat/AssistantSurface';
import { CitationList } from '../aichat/CitationList';
import { ClaimEvidence } from '../aichat/ClaimEvidence';
import { ContextUsedDisclosure } from '../aichat/ContextUsedDisclosure';
import { DeleteOwnedThreadsModal } from '../aichat/DeleteOwnedThreadsModal';
import { EntityChips } from '../aichat/EntityChips';
import { EvidenceChips } from '../aichat/EvidenceChips';
import { ExportThreadsModal } from '../aichat/ExportThreadsModal';
import { LegacyChatStorageNotice } from '../aichat/LegacyChatStorageNotice';
import { MarkdownMessage } from '../aichat/MarkdownMessage';
import { RetrievalCoverage } from '../aichat/RetrievalCoverage';
import {
  type ThreadAction,
  ThreadActionsModal
} from '../aichat/ThreadActionsModal';
import { ThreadMemoryModal } from '../aichat/ThreadMemoryModal';
import type { EvidenceAnalysisAttachment } from '../aichat/evidenceAnalysis';
import { composeAnswerMarkdown } from '../aichat/evidenceFormat';
import { hintIsRedundant } from '../aichat/scopeContext';
import type { ThreadDeleteResult } from '../aichat/threadDeletion';
import RiskRadarDrawerBadge from '../riskradar/RiskRadarDrawerBadge';

type AIChatDrawerTab = 'chat' | 'approvals' | 'history' | 'mail';

function formatToolDuration(durationMs?: number): string | null {
  if (durationMs === undefined || !Number.isFinite(durationMs)) return null;
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
  return `${(durationMs / 1000).toFixed(1)} s`;
}

function formatRelativeTimeFromISOString(iso: string): string {
  const date = new Date(iso);

  if (!Number.isFinite(date.getTime())) {
    return t`Unknown`;
  }

  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (minutes < 1) return t`Just now`;
  if (minutes < 60) return t`${minutes}m ago`;
  if (hours < 24) return t`${hours}h ago`;
  return t`${days}d ago`;
}

/**
 * Conversation history with server-side search and resume (S20 A8).
 *
 * Search runs over the durable aichat ledger inside the caller's own
 * owner/scope boundary — the server can only ever return the viewer's
 * threads. Clicking a row resumes that thread in the chat tab.
 */
function ThreadHistoryPanel({
  threads,
  activeThreadId,
  searchThreads,
  onResume,
  hasMoreThreads,
  loadingMoreThreads,
  onLoadMore
}: Readonly<{
  threads: { id: string; title: string; updatedAt: Date }[];
  activeThreadId: string | null;
  searchThreads: (
    query: string,
    cursor?: string | null
  ) => Promise<{ threads: ChatThread[]; nextCursor: string | null }>;
  onResume: (threadId: string) => void;
  hasMoreThreads: boolean;
  loadingMoreThreads: boolean;
  onLoadMore: () => void;
}>) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<
    { id: string; title: string; updatedAt: Date }[] | null
  >(null);
  const [searching, setSearching] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [searchError, setSearchError] = useState(false);
  const queryRef = useRef(query);
  const searchVersionRef = useRef(0);
  queryRef.current = query;

  useEffect(() => {
    const version = ++searchVersionRef.current;
    const term = query.trim();
    setResults(null);
    setNextCursor(null);
    setSearchError(false);
    if (!term) {
      setSearching(false);
      return;
    }
    setSearching(true);
    let live = true;
    const handle = setTimeout(() => {
      searchThreads(term)
        .then((page) => {
          if (live && version === searchVersionRef.current) {
            setResults(page.threads);
            setNextCursor(page.nextCursor);
          }
        })
        .catch(() => {
          if (live) setSearchError(true);
        })
        .finally(() => {
          if (live) setSearching(false);
        });
    }, 300);
    return () => {
      live = false;
      clearTimeout(handle);
    };
  }, [query, searchThreads]);

  const loadMoreSearch = async () => {
    if (!nextCursor || searching) return;
    const version = searchVersionRef.current;
    const term = query;
    setSearching(true);
    setSearchError(false);
    try {
      const page = await searchThreads(term, nextCursor);
      if (version !== searchVersionRef.current || queryRef.current !== term)
        return;
      setResults((prev) => {
        const ids = new Set((prev ?? []).map((thread) => thread.id));
        return [
          ...(prev ?? []),
          ...page.threads.filter((thread) => !ids.has(thread.id))
        ];
      });
      setNextCursor(page.nextCursor);
    } catch {
      if (version === searchVersionRef.current) setSearchError(true);
    } finally {
      if (version === searchVersionRef.current) setSearching(false);
    }
  };

  const rows = query.trim() ? (results ?? []) : threads;

  return (
    <Stack gap='xs' data-testid='ai-chat-thread-history'>
      <TextInput
        placeholder={t`Search conversations...`}
        value={query}
        onChange={(event) => setQuery(event.currentTarget.value)}
        leftSection={<IconSearch size={14} />}
        rightSection={searching ? <Loader size='xs' /> : undefined}
        aria-label='search-ai-chat-history'
      />
      {searchError && (
        <Alert color='red'>{t`Conversation search failed. Edit the search or retry loading more results.`}</Alert>
      )}
      {!searching && !searchError && rows.length === 0 && (
        <Text size='sm' c='dimmed' ta='center' py='md'>
          {results !== null
            ? t`No conversations match`
            : t`No conversations yet`}
        </Text>
      )}
      {rows.map((thread) => (
        <UnstyledButton
          key={thread.id}
          onClick={() => onResume(thread.id)}
          data-testid='ai-chat-history-row'
        >
          <Paper
            p='xs'
            radius='md'
            withBorder
            style={{
              borderColor:
                thread.id === activeThreadId
                  ? 'var(--mantine-color-blue-4)'
                  : undefined
            }}
          >
            <Group justify='space-between' wrap='nowrap'>
              <Text size='sm' truncate style={{ flex: 1 }}>
                {thread.title || t`Chat`}
              </Text>
              <Text size='xs' c='dimmed' style={{ whiteSpace: 'nowrap' }}>
                {formatRelativeTimeFromISOString(
                  thread.updatedAt.toISOString()
                )}
              </Text>
            </Group>
          </Paper>
        </UnstyledButton>
      ))}
      {(query.trim() ? Boolean(nextCursor) : hasMoreThreads) && (
        <Button
          variant='subtle'
          loading={query.trim() ? searching : loadingMoreThreads}
          onClick={() => {
            if (query.trim()) void loadMoreSearch();
            else onLoadMore();
          }}
        >{t`Load more conversations`}</Button>
      )}
    </Stack>
  );
}

/**
 * Thread selector dropdown component
 */
function ThreadSelector({
  threads,
  sharedThreads = [],
  activeThreadId,
  onSelectThread,
  onNewThread,
  onDeleteThread,
  onPrepareDeletion,
  onExport,
  onDeletePage,
  bulkDeletionDisabled,
  onRenameThread,
  onShareThread,
  onInspectMemory,
  disabled = false
}: Readonly<{
  threads: ChatThread[];
  sharedThreads?: ChatThread[];
  activeThreadId: string;
  onSelectThread: (threadId: string) => void;
  onNewThread: () => void;
  onDeleteThread: (threadId: string) => Promise<ThreadDeleteResult>;
  bulkDeletionDisabled: boolean;
  onPrepareDeletion: () => Promise<OwnedDeletionPlan>;
  onExport: (signal: AbortSignal) => Promise<TranscriptExportCounts>;
  onDeletePage: (
    plan: OwnedDeletionPlan,
    cursor: string | null
  ) => Promise<OwnedDeletionPage>;
  onRenameThread: (threadId: string, title: string) => Promise<boolean>;
  onShareThread?: (
    threadId: string,
    username: string,
    revoke: boolean
  ) => Promise<{ ok: boolean }>;
  /**
   * M2 PR 9 (GR-16): "What this chat remembers" — offered on owned,
   * persisted rows only; shared rows never get the affordance.
   */
  onInspectMemory?: (threadId: string) => void;
  disabled?: boolean;
}>) {
  const theme = useMantineTheme();
  const [action, setAction] = useState<ThreadAction | null>(null);
  const [deleteAllOpen, setDeleteAllOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const activeThread =
    threads.find((t) => t.id === activeThreadId) ??
    sharedThreads.find((t) => t.id === activeThreadId);

  // Format relative time
  const formatTime = (date: Date) => {
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return t`Just now`;
    if (minutes < 60) return t`${minutes}m ago`;
    if (hours < 24) return t`${hours}h ago`;
    return t`${days}d ago`;
  };

  return (
    <>
      {/* Keep the menu inside the mobile dialog's non-inert subtree. */}
      <Menu
        shadow='md'
        width={280}
        position='bottom-start'
        withinPortal={false}
      >
        <Menu.Target>
          <UnstyledButton
            aria-label='select-ai-chat-thread'
            disabled={disabled}
            px='sm'
            py={6}
            style={{
              borderRadius: 'var(--mantine-radius-md)',
              border: '1px solid var(--mantine-color-gray-3)',
              background: 'var(--mantine-color-body)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              maxWidth: 200,
              transition: 'border-color 0.2s ease'
            }}
          >
            <IconMessages size={16} style={{ flexShrink: 0 }} />
            <Text size='sm' truncate style={{ flex: 1 }}>
              {activeThread?.title || t`New Chat`}
            </Text>
            <IconChevronDown
              size={14}
              style={{ flexShrink: 0, opacity: 0.5 }}
            />
          </UnstyledButton>
        </Menu.Target>

        <Menu.Dropdown>
          <Menu.Label>{t`Conversations`}</Menu.Label>
          <Menu.Item
            component={Link}
            to='/memory/'
            leftSection={<IconBrain size={16} />}
          >{t`What AIMMS remembers`}</Menu.Item>

          {/* New chat option */}
          <Menu.Item
            aria-label='new-ai-chat-thread'
            leftSection={<IconMessagePlus size={16} />}
            onClick={onNewThread}
            color='blue'
          >
            {t`New conversation`}
          </Menu.Item>

          <Menu.Item
            aria-label='export-owned-ai-chat-threads'
            disabled={disabled}
            leftSection={<IconFileExport size={16} />}
            onClick={() => setExportOpen(true)}
          >{t`Export my saved conversations`}</Menu.Item>
          <Menu.Item
            aria-label='delete-owned-ai-chat-threads'
            color='red'
            disabled={disabled || bulkDeletionDisabled}
            leftSection={<IconTrash size={16} />}
            onClick={() => setDeleteAllOpen(true)}
          >{t`Delete my saved conversations`}</Menu.Item>

          {threads.length > 0 && <Menu.Divider />}

          {/* Thread list */}
          <ScrollArea.Autosize mah={300}>
            {threads.map((thread) => (
              <Box
                key={thread.id}
                px='sm'
                py={6}
                style={{
                  backgroundColor:
                    thread.id === activeThreadId
                      ? theme.colors.blue[0]
                      : undefined
                }}
              >
                <Group justify='space-between' wrap='nowrap' gap={8}>
                  <Menu.Item
                    onClick={() => onSelectThread(thread.id)}
                    style={{ flex: 1, minWidth: 0, textAlign: 'left' }}
                  >
                    <Box>
                      <Text
                        size='sm'
                        truncate
                        fw={thread.id === activeThreadId ? 600 : 400}
                      >
                        {thread.title}
                      </Text>
                      <Text size='xs' c='dimmed'>
                        {formatTime(thread.updatedAt)}
                      </Text>
                    </Box>
                  </Menu.Item>
                  <Group gap={2} wrap='nowrap'>
                    <ActionIcon
                      aria-label={`rename-ai-chat-thread-${thread.id}`}
                      size='xs'
                      variant='subtle'
                      color='gray'
                      disabled={disabled}
                      onClick={() => setAction({ kind: 'rename', thread })}
                    >
                      <IconPencil size={12} />
                    </ActionIcon>
                    {onInspectMemory &&
                      thread.isPersisted &&
                      !thread.shared && (
                        <ActionIcon
                          aria-label={`memory-ai-chat-thread-${thread.id}`}
                          title={t`What this chat remembers`}
                          size='xs'
                          variant='subtle'
                          color='gray'
                          disabled={disabled}
                          onClick={() => onInspectMemory(thread.id)}
                        >
                          <IconBrain size={12} />
                        </ActionIcon>
                      )}
                    {onShareThread && (
                      <ActionIcon
                        aria-label={`share-ai-chat-thread-${thread.id}`}
                        size='xs'
                        variant='subtle'
                        color='gray'
                        disabled={disabled}
                        onClick={() => setAction({ kind: 'share', thread })}
                      >
                        <IconShare2 size={12} />
                      </ActionIcon>
                    )}
                    <ActionIcon
                      aria-label={`delete-ai-chat-thread-${thread.id}`}
                      size='xs'
                      variant='subtle'
                      color='red'
                      disabled={disabled}
                      onClick={() => setAction({ kind: 'delete', thread })}
                    >
                      <IconTrash size={12} />
                    </ActionIcon>
                  </Group>
                </Group>
              </Box>
            ))}
          </ScrollArea.Autosize>

          {sharedThreads.length > 0 && (
            <>
              <Menu.Divider />
              <Menu.Label>{t`Shared with me`}</Menu.Label>
              <ScrollArea.Autosize mah={160}>
                {sharedThreads.map((thread) => (
                  <Menu.Item
                    key={thread.id}
                    data-testid={`shared-thread-${thread.id}`}
                    onClick={() => onSelectThread(thread.id)}
                    rightSection={
                      <IconEye size={12} style={{ opacity: 0.6 }} />
                    }
                    style={{
                      backgroundColor:
                        thread.id === activeThreadId
                          ? theme.colors.blue[0]
                          : undefined
                    }}
                  >
                    <Box>
                      <Text
                        size='sm'
                        truncate
                        fw={thread.id === activeThreadId ? 600 : 400}
                      >
                        {thread.title}
                      </Text>
                      <Text size='xs' c='dimmed'>
                        {t`Read-only`}
                      </Text>
                    </Box>
                  </Menu.Item>
                ))}
              </ScrollArea.Autosize>
            </>
          )}

          {threads.length === 0 && sharedThreads.length === 0 && (
            <Text size='xs' c='dimmed' ta='center' py='sm'>
              {t`No previous conversations`}
            </Text>
          )}
        </Menu.Dropdown>
      </Menu>
      {exportOpen && (
        <ExportThreadsModal
          onExport={onExport}
          onClose={() => setExportOpen(false)}
        />
      )}
      {deleteAllOpen && (
        <DeleteOwnedThreadsModal
          onPrepare={onPrepareDeletion}
          onDeletePage={onDeletePage}
          onClose={() => setDeleteAllOpen(false)}
        />
      )}
      {action && (
        <ThreadActionsModal
          key={`${action.kind}:${action.thread.id}`}
          action={action}
          onClose={() => setAction(null)}
          onDelete={onDeleteThread}
          onRename={onRenameThread}
          onShare={onShareThread}
        />
      )}
    </>
  );
}

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
        aria-label='open-ai-chat'
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
 * Animated typing indicator - 3 bouncing dots
 */
function TypingIndicator() {
  return (
    <Group gap={4} align='center'>
      {[0, 1, 2].map((i) => (
        <Box
          key={i}
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            backgroundColor: 'var(--mantine-color-blue-5)',
            animation: `copilotBounce 1.4s ease-in-out ${i * 0.16}s infinite`
          }}
        />
      ))}
      <style>{`
        @keyframes copilotBounce {
          0%, 80%, 100% { transform: translateY(0); }
          40% { transform: translateY(-6px); }
        }
      `}</style>
    </Group>
  );
}

/**
 * Message action buttons (copy, thumbs up/down)
 */
function MessageActions({
  content,
  messageId,
  threadId,
  evidenceAnalysis,
  onRegenerate
}: Readonly<{
  content: string;
  messageId: string;
  threadId: string | null;
  evidenceAnalysis?: EvidenceAnalysisAttachment;
  onRegenerate?: () => void;
}>) {
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);
  // S11 (Q28): copy/export carry scope + as-of + coverage + citations,
  // composed from the persisted manifest ONLY — live copy, reload copy and
  // export are one function over one payload. The feedback SHA-256 below
  // deliberately keeps hashing the RAW content (content-addressed ledger).
  const exportable = evidenceAnalysis
    ? composeAnswerMarkdown(content, evidenceAnalysis)
    : content;

  const download = () => {
    const blob = new Blob([exportable], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `aimms-answer-${(threadId ?? 'thread').slice(0, 15)}-${messageId.slice(0, 12)}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  // Persist the verdict to the durable ledger. Freshly streamed messages
  // carry client-generated ids that never exist server-side, so the exact
  // rated content's SHA-256 rides along and the server attributes by content
  // when the id is unknown. Toggling a thumb off retracts the verdict —
  // "no verdict" is a legitimate latest state.
  const rate = (rating: 'up' | 'down') => {
    const next = feedback === rating ? null : rating;
    setFeedback(next);
    if (!threadId) {
      return;
    }
    void (async () => {
      try {
        const bytes = new TextEncoder().encode(content);
        const digest = await crypto.subtle.digest('SHA-256', bytes);
        const contentSha256 = Array.from(new Uint8Array(digest))
          .map((byte) => byte.toString(16).padStart(2, '0'))
          .join('');
        await api.post('/api/aichat/feedback/', {
          thread_id: threadId,
          message_id: messageId,
          rating: next ?? 'none',
          content_sha256: contentSha256
        });
      } catch {
        // The optimistic state stays; the ledger simply missed one verdict.
        console.debug('feedback not recorded');
      }
    })();
  };

  return (
    <Group
      gap={8}
      mt='xs'
      style={{
        opacity: 0.6,
        transition: 'opacity 0.2s ease'
      }}
      className='message-actions'
    >
      <CopyButton value={exportable}>
        {({ copied, copy }) => (
          <Tooltip label={copied ? t`Copied!` : t`Copy`} withArrow>
            <ActionIcon
              aria-label='copy-ai-chat-message'
              size='xs'
              variant='subtle'
              color={copied ? 'teal' : 'gray'}
              onClick={copy}
            >
              {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
            </ActionIcon>
          </Tooltip>
        )}
      </CopyButton>
      <Tooltip label={t`Good response`} withArrow>
        <ActionIcon
          aria-label='rate-ai-chat-message-good'
          size='xs'
          variant='subtle'
          color={feedback === 'up' ? 'blue' : 'gray'}
          onClick={() => rate('up')}
        >
          <IconThumbUp size={14} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label={t`Bad response`} withArrow>
        <ActionIcon
          aria-label='rate-ai-chat-message-bad'
          size='xs'
          variant='subtle'
          color={feedback === 'down' ? 'red' : 'gray'}
          onClick={() => rate('down')}
        >
          <IconThumbDown size={14} />
        </ActionIcon>
      </Tooltip>
      {evidenceAnalysis && (
        <Tooltip label={t`Export as Markdown`} withArrow>
          <ActionIcon
            aria-label='export-ai-chat-message'
            size='xs'
            variant='subtle'
            color='gray'
            onClick={download}
          >
            <IconFileExport size={14} />
          </ActionIcon>
        </Tooltip>
      )}
      {onRegenerate && (
        <Tooltip label={t`Regenerate`} withArrow>
          <ActionIcon
            aria-label='regenerate-ai-chat-message'
            size='xs'
            variant='subtle'
            color='gray'
            onClick={onRegenerate}
          >
            <IconRefresh size={14} />
          </ActionIcon>
        </Tooltip>
      )}
      <style>{`
        .message-actions:hover { opacity: 1 !important; }
      `}</style>
    </Group>
  );
}

/**
 * Single chat message component - CopilotKit style
 */
/** S11 (§8.8): the six DISTINCT no-data states; the client never converts
 *  an empty result into "no records exist" on its own. */
function noDataReasonLabel(
  reason: NonNullable<EvidenceAnalysisAttachment['no_data_reason']>,
  populationCount: number | null
): string {
  switch (reason) {
    case 'complete_population_no_matches':
      return populationCount != null
        ? t`No matching records among the ${populationCount} evaluated.`
        : t`No matching records in the fully evaluated population.`;
    case 'outside_active_selection':
      return t`This falls outside the active scope selection.`;
    case 'unauthorized_or_unavailable':
      return t`Source not available.`;
    case 'retrieval_failure':
      return t`The search could not be completed.`;
    case 'unresolved_applicability':
      return t`Could not determine which documents apply.`;
    case 'incomplete_coverage':
      return t`Coverage was incomplete; no conclusion was drawn.`;
  }
}

/** S11: the CLOSED progress-stage enum, mapped client-side to localized
 *  strings — server free text can never paint here. */
function progressStageLabel(stage: string): string {
  switch (stage) {
    case 'confirming_scope':
      return t`Confirming scope`;
    case 'reviewing_records':
      return t`Reviewing records`;
    case 'validating_evidence':
      return t`Validating evidence`;
    default:
      return t`Working`;
  }
}

function ChatMessageItem({
  message,
  threadId,
  aiHost,
  questionArmed,
  questionAnsweredLocally,
  questionResolution,
  onQuestionAnswer,
  onRegenerate
}: Readonly<{
  message: ChatMessage;
  threadId: string | null;
  aiHost: string;
  questionArmed?: boolean;
  questionAnsweredLocally?: boolean;
  questionResolution?: QuestionResolution;
  onQuestionAnswer?: (text: string) => void;
  onRegenerate?: () => void;
}>) {
  const theme = useMantineTheme();
  const colorScheme = useComputedColorScheme('light');
  const isUser = message.role === 'user';

  return (
    <Transition mounted transition='fade' duration={200}>
      {(styles) => (
        <Box
          style={{
            ...styles,
            display: 'flex',
            flexDirection: 'column',
            alignItems: isUser ? 'flex-end' : 'flex-start',
            marginBottom: 'var(--mantine-spacing-md)'
          }}
        >
          {/* Avatar and label for assistant */}
          {!isUser && (
            <Group gap='xs' mb={4}>
              <Box
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: '50%',
                  background: `linear-gradient(135deg, ${theme.colors.blue[5]}, ${theme.colors.violet[5]})`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center'
                }}
              >
                <IconRobot size={16} color='white' />
              </Box>
              <Text size='xs' c='dimmed' fw={500}>
                {t`AI Assistant`}
              </Text>
            </Group>
          )}

          {/* Message bubble (U1: theme-aware colors — no hardcoded
            light-only grays; user bubbles keep WCAG-safe white on blue) */}
          <Paper
            p='sm'
            radius='lg'
            style={{
              backgroundColor: isUser
                ? theme.colors.blue[6]
                : colorScheme === 'dark'
                  ? theme.colors.dark[6]
                  : theme.colors.gray[0],
              color: isUser ? 'white' : undefined,
              maxWidth: '85%',
              borderTopRightRadius: isUser ? 4 : undefined,
              borderTopLeftRadius: !isUser ? 4 : undefined,
              boxShadow: isUser
                ? '0 2px 8px rgba(59, 130, 246, 0.25)'
                : '0 1px 3px rgba(0, 0, 0, 0.08)'
            }}
          >
            {!isUser && (message.toolActivity?.length ?? 0) > 0 && (
              <Group gap={6} mb={6} wrap='wrap'>
                {message.toolActivity?.map((activity) => {
                  const duration = formatToolDuration(activity.durationMs);
                  return (
                    <Tooltip
                      key={activity.id}
                      label={
                        duration
                          ? `${activity.name} completed in ${duration}`
                          : activity.name
                      }
                      disabled={!duration}
                    >
                      <Badge
                        size='xs'
                        variant='light'
                        color={
                          activity.status === 'running'
                            ? 'blue'
                            : activity.status === 'ok'
                              ? 'teal'
                              : activity.status === 'denied'
                                ? 'yellow'
                                : 'red'
                        }
                        leftSection={
                          activity.status === 'running' ? (
                            <Loader size={8} />
                          ) : undefined
                        }
                      >
                        {activity.name}
                        {duration ? ` (${duration})` : ''}
                      </Badge>
                    </Tooltip>
                  );
                })}
              </Group>
            )}
            {message.content ? (
              isUser ? (
                <Text
                  size='sm'
                  style={{
                    whiteSpace: 'pre-wrap',
                    lineHeight: 1.6,
                    wordBreak: 'break-word'
                  }}
                >
                  {message.content}
                </Text>
              ) : message.question ? null : (
                <MarkdownMessage content={message.content} />
              )
            ) : message.isStreaming && message.progressStage ? (
              /* S11: buffered execution shows only the safe generated
                 stages (plus cancel); no provisional text ever streams. */
              <Group gap={6} wrap='nowrap'>
                <Loader size='xs' />
                <Text
                  size='xs'
                  c='dimmed'
                  aria-live='polite'
                  data-testid='analysis-progress'
                >
                  {progressStageLabel(message.progressStage)}
                </Text>
              </Group>
            ) : (
              message.isStreaming && !message.question && <TypingIndicator />
            )}
            {/* Structured question card (S22/S23): message-anchored so the
                frozen card survives reload; the singleton governs armedness. */}
            {!isUser && message.question && (
              <QuestionCard
                payload={message.question}
                armed={!!questionArmed}
                answeredExternally={!!questionAnsweredLocally}
                resolution={questionResolution}
                onAnswer={(text) => onQuestionAnswer?.(text)}
              />
            )}
            {/* Server-observed entity chips (S28): navigate to the records
                this turn was actually about. */}
            {!isUser && !message.isStreaming && message.entities && (
              <EntityChips entities={message.entities} />
            )}
            {/* R4: server-verified media evidence (photos / video segments). */}
            {!isUser && !message.isStreaming && message.mediaEvidence && (
              <EvidenceChips items={message.mediaEvidence} />
            )}
            {/* M2 PR 9 (GR-16): the content-free Context used record —
                ids and counts only, collapsed by default. */}
            {!isUser && !message.isStreaming && message.contextUsed && (
              <ContextUsedDisclosure record={message.contextUsed} />
            )}
            {/* S11: the v2 evidence-analysis block — partial banner,
                distinct no-data state, coverage, and claim-level evidence.
                Confidence is NEVER rendered here, even if redundantly sent. */}
            {!isUser && !message.isStreaming && message.evidenceAnalysis && (
              <Stack gap={6} mt={8}>
                {message.evidenceAnalysis.response_state === 'partial' && (
                  <Alert
                    color='yellow'
                    variant='light'
                    p='xs'
                    data-testid='analysis-partial'
                  >
                    <Text size='xs'>
                      {t`Partial answer — some checks did not finish.`}
                      {message.evidenceAnalysis.incomplete_reasons.length
                        ? ` (${message.evidenceAnalysis.incomplete_reasons
                            .map((reason) => reason.facet)
                            .join(', ')})`
                        : ''}
                    </Text>
                  </Alert>
                )}
                {message.evidenceAnalysis.no_data_reason && (
                  <Text
                    size='xs'
                    c='dimmed'
                    data-testid={`no-data-${message.evidenceAnalysis.no_data_reason}`}
                  >
                    {noDataReasonLabel(
                      message.evidenceAnalysis.no_data_reason,
                      message.evidenceAnalysis.coverage?.population_count ??
                        null
                    )}
                  </Text>
                )}
                {message.evidenceAnalysis.coverage && (
                  <RetrievalCoverage
                    coverage={message.evidenceAnalysis.coverage}
                  />
                )}
                {threadId && (
                  <ClaimEvidence
                    attachment={message.evidenceAnalysis}
                    host={aiHost}
                    threadId={threadId}
                    messageId={message.id}
                  />
                )}
              </Stack>
            )}
            {/* Diagnosis-rail provenance (S10): a cited answer shows its
                sources; an uncited one is visibly flagged, never implied.
                v1 only — a v2 attachment supersedes it entirely. */}
            {!isUser &&
              !message.isStreaming &&
              !message.evidenceAnalysis &&
              message.evidence !== undefined && (
                <Stack gap={4} mt={8}>
                  {message.confidence && (
                    <Badge
                      size='sm'
                      variant='outline'
                      color='gray'
                      w='fit-content'
                    >
                      {t`Declared confidence: ${message.confidence}`}
                    </Badge>
                  )}
                  {message.evidence.length > 0 ? (
                    <CitationList
                      citations={message.evidence.map((entry, index) => ({
                        // v1 adapter: ordinals ARE array order here (the
                        // diagnosis rail has no server manifest).
                        ordinal: index + 1,
                        sourceType: entry.source_type,
                        available: true,
                        asOf: entry.as_of,
                        sourceTitle: entry.locator?.field ?? entry.source_type,
                        sourceId: entry.source_id,
                        sourceRevision: entry.source_revision
                      }))}
                    />
                  ) : (
                    <Text size='xs' c='orange' data-testid='diagnosis-uncited'>
                      {t`No cited sources — not grounded in machine data.`}
                    </Text>
                  )}
                </Stack>
              )}
          </Paper>

          {/* Avatar for user */}
          {isUser && (
            <Group gap='xs' mt={4} style={{ flexDirection: 'row-reverse' }}>
              <Box
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: '50%',
                  backgroundColor: theme.colors.gray[3],
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center'
                }}
              >
                <IconUser size={16} color={theme.colors.gray[7]} />
              </Box>
              <Text size='xs' c='dimmed' fw={500}>
                {t`You`}
              </Text>
            </Group>
          )}

          {/* Action buttons for assistant messages */}
          {!isUser && !message.isStreaming && message.content && (
            <Box ml={36}>
              <MessageActions
                content={message.content}
                messageId={message.id}
                threadId={threadId}
                evidenceAnalysis={message.evidenceAnalysis}
                onRegenerate={onRegenerate}
              />
            </Box>
          )}
        </Box>
      )}
    </Transition>
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
  const voice = useVoiceLiveSession({
    host: voiceHost,
    enabled: true,
    threadId: activeThreadId ?? undefined,
    onTurnResult: (turn) => {
      useVoiceDecisionState.getState().applyTurn(turn.session_id, turn);
      // Typed and voice turns share one server history; resync so the
      // drawer renders the converged conversation.
      void syncThreads();
      if (turn.thread_id && turn.thread_id !== activeThreadId) {
        switchThread(turn.thread_id);
      }
      // S22/S23: voice turns deliver the pending question on the REST
      // response (no SSE QUESTION event on this rail). Arm the singleton so
      // the card renders clickable instead of permanently frozen, and
      // disarm when a turn resolves it.
      armQuestion((turn.pending_question as QuestionPayload | null) ?? null);
    }
  });
  const handleClose = useCallback(() => {
    void voice.end();
    useVoiceSurfaceState.getState().closeConsent();
    clearRoutingHint();
    onClose();
  }, [onClose, voice.end, clearRoutingHint]);
  const voiceFullscreen = useVoiceSurfaceState((state) => state.fullscreen);
  const evidenceOpen = useEvidenceViewerState((state) => state.item !== null);
  const consentOpen = useVoiceSurfaceState((state) => state.consent);
  const narrow = useMediaQuery('(max-width: 48em)');
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

  const [inputValue, setInputValue] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<UploadedFile[]>([]);
  // M2 PR 9: the thread whose memory modal is open (owner-only surface).
  const [memoryThreadId, setMemoryThreadId] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
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

  useEffect(() => {
    if (previousThreadIdRef.current !== activeThreadId) {
      composerVersionRef.current += 1;
      setInputValue('');
      setAttachedFiles([]);
      setIsUploading(false);
      setIsApplyingScope(false);
      setMemoryThreadId(null);
      previousThreadIdRef.current = activeThreadId;
    }
  }, [activeThreadId]);

  // Resizable drawer width (persisted in localStorage)
  const MIN_WIDTH = 340;
  const MAX_WIDTH = 900;
  const DEFAULT_WIDTH = 440;
  const [drawerWidth, setDrawerWidth] = useLocalStorage<number>({
    key: 'ai-chat-drawer-width',
    defaultValue: DEFAULT_WIDTH
  });
  const isResizing = useRef(false);
  const resizeStartX = useRef(0);
  const resizeStartWidth = useRef(DEFAULT_WIDTH);
  // U1: never let the panel exceed the viewport.
  const clampDrawerWidth = useCallback(
    (width: number) =>
      Math.min(
        Math.max(MIN_WIDTH, width),
        Math.max(MIN_WIDTH, window.innerWidth - 32)
      ),
    []
  );

  // Mouse handlers for resize drag
  const handleResizeMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      isResizing.current = true;
      resizeStartX.current = e.clientX;
      resizeStartWidth.current = drawerWidth;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [drawerWidth]
  );

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return;
      // Dragging left = wider (since panel is on the right)
      const delta = resizeStartX.current - e.clientX;
      setDrawerWidth(clampDrawerWidth(resizeStartWidth.current + delta));
    };

    const handleMouseUp = () => {
      if (isResizing.current) {
        isResizing.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [setDrawerWidth, clampDrawerWidth]);

  // U1: keyboard resizing alternative to the mouse-only drag handle.
  const handleResizeKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      const step = 24;
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        setDrawerWidth((width) => clampDrawerWidth(width + step));
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        setDrawerWidth((width) => clampDrawerWidth(width - step));
      } else if (event.key === 'Home') {
        event.preventDefault();
        setDrawerWidth(clampDrawerWidth(MIN_WIDTH));
      } else if (event.key === 'End') {
        event.preventDefault();
        setDrawerWidth(clampDrawerWidth(MAX_WIDTH));
      }
    },
    [setDrawerWidth, clampDrawerWidth]
  );

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

  // Focus input when drawer opens
  useEffect(() => {
    if (opened && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [opened]);

  // U1: consume the Ask/scan intent ONCE — select Chat and focus the
  // composer. Never an effect that keeps forcing the tab.
  useEffect(() => {
    if (opened && chatOpenIntent === 'chat') {
      useAIChatState.getState().consumeChatOpenIntent();
      setActiveTab('chat');
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [opened, chatOpenIntent, setActiveTab]);

  // S2: a scope PUT seeded by the routing hint is in flight before a send.
  const [isApplyingScope, setIsApplyingScope] = useState(false);

  // Handle sending a message
  const handleSendMessage = useCallback(
    async (messageText?: string) => {
      const text = messageText || inputValue;
      if (!text.trim() || isLoading || isSyncing || isApplyingScope) return;
      const version = composerVersionRef.current;
      const fileIds = attachedFiles.map((f) => f.file_id);
      // S2: the machine hint is now a SCOPE SEED, never message text — it
      // becomes a server-side explicit-assets scope on this thread before
      // the first send. Failure narrows nothing, so the send proceeds
      // unscoped with a visible notice (per-turn authorization is the real
      // boundary); the typed text always goes out byte-identical.
      if (routingHint && scopeCapable && !activeThreadShared) {
        setIsApplyingScope(true);
        try {
          const result = await setThreadScope({
            mode: 'explicit_assets',
            machine_ids: [routingHint.machineId],
            display_label: routingHint.machineName.slice(0, 120)
          });
          if (!result.ok) {
            showNotification({
              color: 'yellow',
              message: t`Could not set the machine scope — sending without it.`
            });
          }
        } finally {
          if (version === composerVersionRef.current) setIsApplyingScope(false);
        }
      }
      if (version !== composerVersionRef.current) return;
      // Closing or switching during the scope request cancels this send intent.
      // The hint is bound to one thread and is cleared by those transitions.
      if (routingHint && useAIChatState.getState().routingHint !== routingHint)
        return;
      sendMessage(text, fileIds.length > 0 ? fileIds : undefined);
      if (routingHint) {
        clearRoutingHint();
      }
      setInputValue('');
      setAttachedFiles([]);
    },
    [
      inputValue,
      isLoading,
      isSyncing,
      isApplyingScope,
      sendMessage,
      setThreadScope,
      scopeCapable,
      activeThreadShared,
      attachedFiles,
      routingHint,
      clearRoutingHint
    ]
  );

  // Handle file selection
  const handleFileSelect = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files;
      if (!files || files.length === 0) return;

      setIsUploading(true);
      const uploadThreadId = activeThreadId;
      const version = composerVersionRef.current;
      try {
        for (const file of Array.from(files)) {
          if (
            version !== composerVersionRef.current ||
            activeThreadIdRef.current !== uploadThreadId
          )
            break;
          const result = await uploadFile(file);
          if (
            result &&
            version === composerVersionRef.current &&
            activeThreadIdRef.current === uploadThreadId
          ) {
            setAttachedFiles((prev) => [...prev, result]);
          }
        }
      } finally {
        if (version === composerVersionRef.current) setIsUploading(false);
        // Reset file input so the same file can be selected again
        if (version === composerVersionRef.current && fileInputRef.current) {
          fileInputRef.current.value = '';
        }
      }
    },
    [activeThreadId, uploadFile]
  );

  // Remove an attached file
  const removeAttachedFile = useCallback((fileId: string) => {
    setAttachedFiles((prev) => prev.filter((f) => f.file_id !== fileId));
  }, []);

  // Actual selection changes clear the composer in the effect above. A switch
  // refused during an active turn must not silently discard attachments.
  const handleSwitchThread = useCallback(
    (threadId: string) => {
      switchThread(threadId);
    },
    [switchThread]
  );

  const handleNewThread = useCallback(() => {
    createNewThread();
  }, [createNewThread]);

  // S2: a machine hint aimed at a shared (read-only) thread hops to a new
  // owned thread — the composer is disabled on shared transcripts, so the
  // ask could never be sent there.
  useEffect(() => {
    if (!opened || !routingHint) return;
    if (activeThreadShared) {
      const id = createNewThread();
      useAIChatState.getState().openWithHint(routingHint);
      useAIChatState.getState().bindHint(id);
    } else {
      useAIChatState.getState().bindHint(activeThreadId);
    }
  }, [
    opened,
    routingHint,
    activeThreadShared,
    activeThreadId,
    createNewThread
  ]);

  const handleClearChat = useCallback(() => {
    setAttachedFiles([]);
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
    applyingScope: isApplyingScope,
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
        modal={Boolean(narrow)}
        suspended={
          consentOpen ||
          voiceFullscreen ||
          evidenceOpen ||
          memoryThreadId !== null
        }
        width={drawerWidth}
        title={t`AI Assistant`}
        onClose={handleClose}
      >
        {/* Resize handle on the left edge (mouse drag + keyboard, U1) */}
        <Box
          // biome-ignore lint/a11y/useSemanticElements: focusable window splitter — a real <hr> can't be dragged or keyboard-resized; this keeps the geometry and the ARIA separator values
          onMouseDown={handleResizeMouseDown}
          role='separator'
          aria-orientation='vertical'
          aria-label={t`Resize chat panel`}
          aria-valuenow={drawerWidth}
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={MAX_WIDTH}
          tabIndex={0}
          onKeyDown={handleResizeKeyDown}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: 6,
            height: '100%',
            cursor: 'col-resize',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'background 0.15s ease'
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--mantine-color-blue-1)';
          }}
          onMouseLeave={(e) => {
            if (!isResizing.current) {
              e.currentTarget.style.background = '';
            }
          }}
        >
          <IconGripVertical
            size={12}
            style={{ opacity: 0.4, pointerEvents: 'none' }}
          />
        </Box>
        <Boundary label='AIChatDrawer'>
          {/* Header */}
          <Box
            p='md'
            style={{
              borderBottom: '1px solid var(--mantine-color-gray-2)',
              background: 'var(--mantine-color-body)'
            }}
          >
            <Group justify='space-between' wrap='nowrap'>
              <Group gap='sm'>
                <Box
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: '50%',
                    background: `linear-gradient(135deg, ${theme.colors.blue[5]}, ${theme.colors.violet[5]})`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                  }}
                >
                  <IconSparkles size={20} color='white' />
                </Box>
                <Box>
                  <Text fw={600} size='md'>
                    {t`AI Assistant`}
                  </Text>
                  <Text size='xs' c='dimmed'>
                    {t`Powered by AIMMS AI`}
                  </Text>
                </Box>
              </Group>
              <Group gap='xs'>
                {/* Sync button */}
                <Tooltip
                  label={isSyncing ? t`Syncing...` : t`Sync conversations`}
                  withArrow
                >
                  <ActionIcon
                    aria-label='sync-ai-chat-threads'
                    variant='subtle'
                    color='gray'
                    radius='xl'
                    onClick={() => syncThreads()}
                    loading={isSyncing}
                    disabled={isSyncing}
                  >
                    <IconRefresh
                      size={18}
                      style={{
                        animation: isSyncing
                          ? 'spin 1s linear infinite'
                          : 'none'
                      }}
                    />
                  </ActionIcon>
                </Tooltip>
                {hasMessages && (
                  <Tooltip label={t`New conversation`} withArrow>
                    <ActionIcon
                      variant='subtle'
                      color='gray'
                      radius='xl'
                      onClick={handleClearChat}
                      aria-label='new-ai-chat-thread'
                      disabled={isLoading}
                    >
                      <IconMessagePlus size={18} />
                    </ActionIcon>
                  </Tooltip>
                )}
                <Tooltip label={t`Close`} withArrow>
                  <ActionIcon
                    aria-label='close-ai-chat'
                    variant='subtle'
                    color='gray'
                    radius='xl'
                    onClick={handleClose}
                  >
                    <IconX size={18} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Group>

            {/* Sync indicator */}
            {isSyncing && (
              <Text size='xs' c='dimmed' ta='center' mt='xs'>
                {t`Syncing conversations with server...`}
              </Text>
            )}

            {/* Drawer tab strip (Chat / Approvals / History) */}
            <Box mt='sm'>
              <Group justify='space-between' wrap='wrap'>
                <Tabs
                  value={activeTab}
                  onChange={(v) =>
                    setActiveTab((v as AIChatDrawerTab) || 'chat')
                  }
                  variant='pills'
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
                <RiskRadarDrawerBadge />
              </Group>
            </Box>

            {/* Thread selector */}
            {activeTab === 'chat' && (
              <Box mt='sm'>
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
              </Box>
            )}
          </Box>

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
          <ScrollArea
            style={{ flex: 1 }}
            offsetScrollbars
            scrollbarSize={6}
            viewportRef={scrollAreaRef}
            data-testid='ai-chat-messages'
            viewportProps={{ ...VIEWPORT_MARKERS }}
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
                      {t`Hi! 👋 How can I help?`}
                    </Text>
                    <Text size='sm' c='dimmed' maw={280} mx='auto' mb='lg'>
                      {t`I can help you search for parts, create orders, and automate tasks in AIMMS.`}
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
                          aria-disabled={isSyncing}
                          disabled={isSyncing}
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
                  <Paper p='sm' radius='md' bg='red.0' mb='md'>
                    <Text size='xs' c='red.7'>
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
                <ChatActionProposalList {...proposals} />
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
          </ScrollArea>

          {/* Input area - CopilotKit style (Chat tab only) */}
          {activeTab === 'chat' && (
            <Box
              p='md'
              style={{
                borderTop: '1px solid var(--mantine-color-gray-2)',
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

              {/* Attached file chips */}
              {attachedFiles.length > 0 && (
                <Group gap='xs' mb='xs' wrap='wrap'>
                  {attachedFiles.map((f) => (
                    <Badge
                      key={f.file_id}
                      variant='light'
                      color='blue'
                      size='sm'
                      rightSection={
                        <ActionIcon
                          aria-label={`remove-ai-chat-attachment-${f.file_id}`}
                          size='xs'
                          variant='transparent'
                          color='blue'
                          onClick={() => removeAttachedFile(f.file_id)}
                        >
                          <IconX size={12} />
                        </ActionIcon>
                      }
                    >
                      {f.filename.length > 20
                        ? `${f.filename.slice(0, 17)}...`
                        : f.filename}
                    </Badge>
                  ))}
                </Group>
              )}

              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type='file'
                multiple
                accept='.pdf,.png,.jpg,.jpeg,.xlsx,.csv,.docx'
                style={{ display: 'none' }}
                onChange={handleFileSelect}
              />

              {routingHint && !hintRedundant && (
                <Group gap='xs' mb={4} data-testid='ai-chat-routing-hint'>
                  <Badge
                    variant='light'
                    color='blue'
                    rightSection={
                      <ActionIcon
                        size='xs'
                        variant='transparent'
                        color='blue'
                        aria-label='dismiss-routing-hint'
                        onClick={clearRoutingHint}
                      >
                        <IconX size={10} />
                      </ActionIcon>
                    }
                  >
                    {t`Asking about`}: {routingHint.machineName}
                  </Badge>
                </Group>
              )}
              {/* C2: the truthful, read-only analysis context — reports
                server-confirmed scope only, with no menu attached. */}
              <AnalysisContextLine scope={activeScope} />

              <Paper
                radius='xl'
                p='xs'
                withBorder
                data-testid='ai-chat-composer'
                data-voice-surface
                style={{
                  borderColor: 'var(--mantine-color-gray-3)',
                  transition: 'border-color 0.2s ease, box-shadow 0.2s ease'
                }}
              >
                <Group gap='xs' align='flex-end' wrap='nowrap'>
                  <Tooltip label={t`Attach file`} withArrow>
                    <ActionIcon
                      aria-label='attach-ai-chat-file'
                      size='lg'
                      radius='xl'
                      variant='subtle'
                      color='gray'
                      onClick={() => fileInputRef.current?.click()}
                      disabled={
                        isLoading ||
                        isUploading ||
                        isSyncing ||
                        activeThreadShared ||
                        activeThreadDeletionPending
                      }
                      loading={isUploading}
                    >
                      <IconPaperclip size={18} />
                    </ActionIcon>
                  </Tooltip>
                  <Textarea
                    ref={inputRef}
                    aria-label={t`Message`}
                    placeholder={
                      activeThreadShared
                        ? t`Shared conversation — read-only`
                        : pendingQuestion
                          ? t`Answer the question above, or ask something else...`
                          : attachedFiles.length > 0
                            ? t`Add a message about attached files...`
                            : t`Type a message...`
                    }
                    value={inputValue}
                    onChange={(e) => setInputValue(e.currentTarget.value)}
                    onKeyDown={handleKeyDown}
                    autosize
                    minRows={1}
                    maxRows={4}
                    disabled={
                      isLoading ||
                      isSyncing ||
                      activeThreadShared ||
                      activeThreadDeletionPending
                    }
                    styles={{
                      input: {
                        border: 'none',
                        background: 'transparent',
                        padding: '8px 12px',
                        fontSize: '14px',
                        '&:focus': {
                          outline: 'none'
                        }
                      },
                      wrapper: {
                        flex: 1
                      }
                    }}
                    style={{ flex: 1 }}
                  />
                  {/* C3: the mic START action sits inside the composer,
                    adjacent to text entry — one shared eligibility path. */}
                  <VoiceComposerControl {...voiceControlProps} />
                  <Group gap={4}>
                    {isLoading ? (
                      <Tooltip label={t`Stop generating`} withArrow>
                        <ActionIcon
                          aria-label='cancel-ai-chat-turn'
                          size={44}
                          radius='xl'
                          variant='filled'
                          color='red'
                          onClick={cancelRequest}
                        >
                          <IconPlayerStop size={18} />
                        </ActionIcon>
                      </Tooltip>
                    ) : (
                      <Tooltip label={t`Send message`} withArrow>
                        <ActionIcon
                          aria-label='send-ai-chat-message'
                          size={44}
                          radius='xl'
                          variant='filled'
                          color='blue'
                          onClick={() => handleSendMessage()}
                          disabled={
                            !inputValue.trim() ||
                            isSyncing ||
                            activeThreadShared ||
                            activeThreadDeletionPending ||
                            isApplyingScope
                          }
                          style={{
                            transition: 'transform 0.2s ease',
                            transform: inputValue.trim()
                              ? 'scale(1)'
                              : 'scale(0.95)'
                          }}
                        >
                          <IconSend size={18} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </Group>
                </Group>
              </Paper>

              {/* C3: transcript review stays visible outside the one-line
                input row. */}
              <VoiceTranscript
                partial={voice.partial}
                listening={voice.state === 'listening'}
                pendingConfirm={voice.pendingConfirm}
                holdPrompt={voice.holdPrompt}
              />
              {/* C3: the active-session strip, next to the composer on Chat
                (other tabs get it below the header). */}
              {voiceActive && !voiceFullscreen && (
                <Box mt='xs'>
                  <VoiceActiveStrip {...voiceControlProps} />
                </Box>
              )}
              {voice.session && !voiceFullscreen && <VoiceExperienceControls />}
              {/* U1: a friendly page label — never the raw URL path. */}
              <Text size='xs' c='dimmed' data-testid='assistant-page-context'>
                {pageContextLabels[pageContextKind(location.pathname)]}
              </Text>

              {/* Footer text */}
              <Text size='xs' c='dimmed' ta='center' mt='xs'>
                {t`AI may make mistakes. Verify important information.`}
              </Text>
            </Box>
          )}
        </Boundary>
      </AssistantSurface>
    </>
  );
}
