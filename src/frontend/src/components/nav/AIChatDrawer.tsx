import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  CopyButton,
  Drawer,
  Group,
  Menu,
  Paper,
  ScrollArea,
  Skeleton,
  Stack,
  Tabs,
  Text,
  Textarea,
  Tooltip,
  Transition,
  UnstyledButton,
  useMantineTheme
} from '@mantine/core';
import { useHotkeys, useLocalStorage } from '@mantine/hooks';
import {
  IconCheck,
  IconChevronDown,
  IconCopy,
  IconGripVertical,
  IconMessagePlus,
  IconMessages,
  IconPaperclip,
  IconPencil,
  IconPlayerStop,
  IconRefresh,
  IconRobot,
  IconSend,
  IconSparkles,
  IconThumbDown,
  IconThumbUp,
  IconTrash,
  IconUser,
  IconX
} from '@tabler/icons-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Boundary } from '@lib/components/Boundary';
import { api } from '../../App';
import {
  type ChatMessage,
  type ChatThread,
  type UploadedFile,
  useAIChat
} from '../../hooks/UseAIChat';
import { useVoiceLiveSession } from '../../hooks/useVoiceLiveSession';
import { useLocalState } from '../../states/LocalState';
import { ChatActionProposalList } from '../ai/ChatActionProposals';
import { HITLApprovalCard, HITLResultBanner } from '../ai/HITLApprovalModal';
import { VoiceContextBadge } from '../ai/VoiceContextBadge';
import { VoiceSessionControl } from '../ai/VoiceSessionControl';
import { VoiceTranscript } from '../ai/VoiceTranscript';
import { CitationList } from '../aichat/CitationList';
import { InlineMarkdown, MarkdownMessage } from '../aichat/MarkdownMessage';
import RiskRadarDrawerBadge from '../riskradar/RiskRadarDrawerBadge';

type AIChatDrawerTab = 'chat' | 'approvals' | 'history';

type ApprovalStatus =
  | 'pending'
  | 'in_review'
  | 'changes_requested'
  | 'approved'
  | 'executing'
  | 'succeeded'
  | 'denied'
  | 'failed'
  | 'expired'
  | 'canceled';

interface ApprovalListItem {
  id: string;
  status: ApprovalStatus;
  risk_tier: number;
  action_type: string;
  summary: string;
  created_at: string;
  updated_at: string;
}

interface ApprovalDetailItem extends ApprovalListItem {
  payload: Record<string, unknown>;
  expires_at?: string | null;
  viewed_confirmed_at?: string | null;
  deny_reason?: string | null;
  canceled_reason?: string | null;
}

function unwrapResults<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];

  if (data && typeof data === 'object' && 'results' in data) {
    const results = (data as { results?: unknown }).results;
    return Array.isArray(results) ? (results as T[]) : [];
  }

  return [];
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

function ApprovalInboxPanel({
  statuses,
  emptyText
}: Readonly<{
  statuses: ApprovalStatus[];
  emptyText: string;
}>) {
  const theme = useMantineTheme();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<ApprovalListItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ApprovalDetailItem | null>(null);

  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const resp = await api.get('/api/approvals/', {
        params: {
          status: statuses.join(','),
          ordering: '-created_at'
        }
      });
      setItems(unwrapResults<ApprovalListItem>(resp.data));
    } catch (err: any) {
      setError(err?.message || t`Failed to load approvals`);
      setItems([]);
    } finally {
      setIsLoading(false);
    }
  }, [statuses]);

  const loadDetail = useCallback(async (approvalId: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const resp = await api.get(`/api/approvals/${approvalId}/`);
      setDetail(resp.data as ApprovalDetailItem);
    } catch (err: any) {
      setError(err?.message || t`Failed to load approval detail`);
      setDetail(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (selectedId) {
      loadDetail(selectedId);
    } else {
      setDetail(null);
    }
  }, [selectedId, loadDetail]);

  if (selectedId) {
    return (
      <Box p='md'>
        <Group justify='space-between' mb='sm'>
          <Button variant='subtle' onClick={() => setSelectedId(null)}>
            {t`Back`}
          </Button>
          <Button variant='subtle' onClick={() => loadDetail(selectedId)}>
            {t`Refresh`}
          </Button>
        </Group>

        {isLoading && (
          <Box>
            <Skeleton height={12} mb='xs' />
            <Skeleton height={12} mb='xs' />
            <Skeleton height={12} mb='xs' />
          </Box>
        )}

        {error && (
          <Paper p='sm' radius='md' bg='red.0' mb='md'>
            <Text size='xs' c='red.7'>
              {error}
            </Text>
          </Paper>
        )}

        {detail && (
          <Paper p='md' radius='md' withBorder>
            <Group justify='space-between' align='flex-start' mb='xs'>
              <Box style={{ flex: 1 }}>
                <Text fw={600} component='div'>
                  <InlineMarkdown content={detail.summary} />
                </Text>
                <Text size='xs' c='dimmed'>
                  {t`Tier`} {detail.risk_tier} • {detail.action_type} •{' '}
                  {detail.status}
                </Text>
              </Box>
              <Badge variant='light' color='blue'>
                {formatRelativeTimeFromISOString(detail.created_at)}
              </Badge>
            </Group>

            {/* Fallback renderer: show top-level payload fields in a field/value grid */}
            <Box mt='sm'>
              {Object.entries(detail.payload || {}).length === 0 ? (
                <Text size='sm' c='dimmed'>
                  {t`No details available`}
                </Text>
              ) : (
                <Box>
                  {Object.entries(detail.payload || {}).map(([key, value]) => {
                    const isPrimitive =
                      value === null ||
                      value === undefined ||
                      typeof value === 'string' ||
                      typeof value === 'number' ||
                      typeof value === 'boolean';

                    const renderedValue = isPrimitive
                      ? String(value)
                      : t`(complex value)`;

                    return (
                      <Group
                        key={key}
                        justify='space-between'
                        align='flex-start'
                        gap='md'
                        py={6}
                        style={{
                          borderBottom: '1px solid var(--mantine-color-gray-2)'
                        }}
                      >
                        <Text size='sm' fw={500} style={{ flex: '0 0 40%' }}>
                          {key}
                        </Text>
                        <Text size='sm' style={{ flex: 1, textAlign: 'right' }}>
                          {renderedValue}
                        </Text>
                      </Group>
                    );
                  })}
                </Box>
              )}

              {/* Optional developer detail (collapsed by default): raw JSON */}
              <Box mt='sm'>
                <Textarea
                  readOnly
                  autosize
                  minRows={4}
                  maxRows={10}
                  value={JSON.stringify(detail.payload ?? {}, null, 2)}
                  label={t`Developer details`}
                />
              </Box>
            </Box>
          </Paper>
        )}
      </Box>
    );
  }

  return (
    <Box p='md'>
      <Group justify='space-between' mb='sm'>
        <Text fw={600}>{t`Approvals`}</Text>
        <Button variant='subtle' onClick={loadList}>
          {t`Refresh`}
        </Button>
      </Group>

      {isLoading && (
        <Box>
          <Skeleton height={54} radius='md' mb='sm' />
          <Skeleton height={54} radius='md' mb='sm' />
          <Skeleton height={54} radius='md' mb='sm' />
        </Box>
      )}

      {error && (
        <Paper p='sm' radius='md' bg='red.0' mb='md'>
          <Text size='xs' c='red.7'>
            {error}
          </Text>
        </Paper>
      )}

      {!isLoading && !error && items.length === 0 && (
        <Paper p='md' radius='md' withBorder>
          <Text size='sm' c='dimmed'>
            {emptyText}
          </Text>
        </Paper>
      )}

      {!isLoading && items.length > 0 && (
        <Box>
          {items.map((item) => (
            <Paper
              key={item.id}
              p='sm'
              radius='md'
              withBorder
              mb='sm'
              style={{ cursor: 'pointer' }}
              onClick={() => setSelectedId(item.id)}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = theme.colors.blue[4];
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor =
                  'var(--mantine-color-gray-3)';
              }}
            >
              <Group justify='space-between' align='flex-start'>
                <Box style={{ flex: 1 }}>
                  <Text size='sm' fw={600} lineClamp={1} component='div'>
                    <InlineMarkdown content={item.summary} />
                  </Text>
                  <Text size='xs' c='dimmed'>
                    {t`Tier`} {item.risk_tier} • {item.action_type}
                  </Text>
                </Box>
                <Badge variant='light' color='gray'>
                  {formatRelativeTimeFromISOString(item.created_at)}
                </Badge>
              </Group>
            </Paper>
          ))}
        </Box>
      )}
    </Box>
  );
}

/**
 * Thread selector dropdown component
 */
function ThreadSelector({
  threads,
  activeThreadId,
  onSelectThread,
  onNewThread,
  onDeleteThread,
  onRenameThread,
  disabled = false
}: Readonly<{
  threads: ChatThread[];
  activeThreadId: string;
  onSelectThread: (threadId: string) => void;
  onNewThread: () => void;
  onDeleteThread: (threadId: string) => void;
  onRenameThread: (threadId: string, title: string) => void;
  disabled?: boolean;
}>) {
  const theme = useMantineTheme();
  const activeThread = threads.find((t) => t.id === activeThreadId);

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
    <Menu shadow='md' width={280} position='bottom-start'>
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
          <IconChevronDown size={14} style={{ flexShrink: 0, opacity: 0.5 }} />
        </UnstyledButton>
      </Menu.Target>

      <Menu.Dropdown>
        <Menu.Label>{t`Conversations`}</Menu.Label>

        {/* New chat option */}
        <Menu.Item
          aria-label='new-ai-chat-thread'
          leftSection={<IconMessagePlus size={16} />}
          onClick={onNewThread}
          color='blue'
        >
          {t`New conversation`}
        </Menu.Item>

        {threads.length > 0 && <Menu.Divider />}

        {/* Thread list */}
        <ScrollArea.Autosize mah={300}>
          {threads.map((thread) => (
            <Menu.Item
              key={thread.id}
              onClick={() => onSelectThread(thread.id)}
              rightSection={
                <Group gap={2} wrap='nowrap'>
                  <ActionIcon
                    aria-label={`rename-ai-chat-thread-${thread.id}`}
                    size='xs'
                    variant='subtle'
                    color='gray'
                    disabled={disabled}
                    onClick={(e) => {
                      e.stopPropagation();
                      const title = window.prompt(
                        t`Rename conversation`,
                        thread.title
                      );
                      if (title?.trim()) {
                        onRenameThread(thread.id, title.trim());
                      }
                    }}
                  >
                    <IconPencil size={12} />
                  </ActionIcon>
                  <ActionIcon
                    aria-label={`delete-ai-chat-thread-${thread.id}`}
                    size='xs'
                    variant='subtle'
                    color='red'
                    disabled={disabled}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteThread(thread.id);
                    }}
                  >
                    <IconTrash size={12} />
                  </ActionIcon>
                </Group>
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
                  {formatTime(thread.updatedAt)}
                </Text>
              </Box>
            </Menu.Item>
          ))}
        </ScrollArea.Autosize>

        {threads.length === 0 && (
          <Text size='xs' c='dimmed' ta='center' py='sm'>
            {t`No previous conversations`}
          </Text>
        )}
      </Menu.Dropdown>
    </Menu>
  );
}

/**
 * AI Chat toggle button component - displays in the header
 * CopilotKit-style sparkle icon button
 */
export function AIChatButton({
  onClick
}: Readonly<{
  onClick: () => void;
}>) {
  return (
    <Tooltip position='bottom-end' label={t`AI Assistant`}>
      <ActionIcon
        onClick={onClick}
        variant='subtle'
        size='lg'
        radius='xl'
        aria-label='open-ai-chat'
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
  onRegenerate
}: Readonly<{
  content: string;
  messageId: string;
  threadId: string | null;
  onRegenerate?: () => void;
}>) {
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);

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
      <CopyButton value={content}>
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
function ChatMessageItem({
  message,
  threadId
}: Readonly<{
  message: ChatMessage;
  threadId: string | null;
}>) {
  const theme = useMantineTheme();
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

          {/* Message bubble */}
          <Paper
            p='sm'
            radius='lg'
            style={{
              backgroundColor: isUser
                ? theme.colors.blue[6]
                : theme.colors.gray[0],
              color: isUser ? 'white' : theme.colors.dark[7],
              maxWidth: '85%',
              borderTopRightRadius: isUser ? 4 : undefined,
              borderTopLeftRadius: !isUser ? 4 : undefined,
              boxShadow: isUser
                ? '0 2px 8px rgba(59, 130, 246, 0.25)'
                : '0 1px 3px rgba(0, 0, 0, 0.08)'
            }}
          >
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
              ) : (
                <MarkdownMessage content={message.content} />
              )
            ) : (
              message.isStreaming && <TypingIndicator />
            )}
            {/* Diagnosis-rail provenance (S10): a cited answer shows its
                sources; an uncited one is visibly flagged, never implied. */}
            {!isUser &&
              !message.isStreaming &&
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
                        id: index,
                        turn_key: message.id,
                        source_type: entry.source_type,
                        available: true,
                        as_of: entry.as_of,
                        source_id: entry.source_id,
                        source_revision: entry.source_revision,
                        locator: entry.locator?.field
                          ? { tool: entry.locator.field }
                          : undefined
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
export function AIChatDrawer({
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
    threads,
    sendMessage,
    clearChat,
    cancelRequest,
    switchThread,
    createNewThread,
    deleteThread,
    renameThread,
    isSyncing,
    syncThreads,
    // HITL (Human-in-the-Loop) approval
    pendingHITL,
    hitlResult,
    approveHITL,
    rejectHITL,
    dismissHITL,
    clearHITLResult,
    uploadFile
  } = useAIChat();

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
      // Typed and voice turns share one server history; resync so the
      // drawer renders the converged conversation.
      void syncThreads();
      if (turn.thread_id && turn.thread_id !== activeThreadId) {
        switchThread(turn.thread_id);
      }
    }
  });
  const handleClose = useCallback(() => {
    void voice.end();
    onClose();
  }, [onClose, voice.end]);

  const [activeTab, setActiveTab] = useLocalStorage<AIChatDrawerTab>({
    key: 'ai-chat-drawer-active-tab',
    defaultValue: 'chat'
  });

  const [pendingApprovalCount, setPendingApprovalCount] = useState<number>(0);
  const refreshPendingApprovalCount = useCallback(async () => {
    try {
      const resp = await api.get('/api/approvals/count/', {
        params: {
          status: 'pending'
        }
      });
      const count = Number((resp.data as any)?.count);
      setPendingApprovalCount(Number.isFinite(count) ? count : 0);
    } catch {
      setPendingApprovalCount(0);
    }
  }, []);

  const [inputValue, setInputValue] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<UploadedFile[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const activeThreadIdRef = useRef(activeThreadId);
  const previousThreadIdRef = useRef(activeThreadId);
  activeThreadIdRef.current = activeThreadId;

  useEffect(() => {
    if (previousThreadIdRef.current !== activeThreadId) {
      setAttachedFiles([]);
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
      const newWidth = Math.min(
        MAX_WIDTH,
        Math.max(MIN_WIDTH, resizeStartWidth.current + delta)
      );
      setDrawerWidth(newWidth);
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
  }, [setDrawerWidth]);

  // Suggestion chips
  const suggestions = [
    { label: t`Search parts`, message: 'Search for parts in inventory' },
    { label: t`Create order`, message: 'Help me create a purchase order' },
    { label: t`Low stock`, message: 'Show me low stock items' }
  ];

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (scrollAreaRef.current) {
      const scrollContainer = scrollAreaRef.current.querySelector(
        '[data-radix-scroll-area-viewport]'
      );
      if (scrollContainer) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight;
      }
    }
  }, [messages]);

  // Focus input when drawer opens
  useEffect(() => {
    if (opened && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [opened]);

  // Poll approvals badge count every 30s while drawer is open
  useEffect(() => {
    if (!opened) return;

    refreshPendingApprovalCount();
    const id = setInterval(() => {
      refreshPendingApprovalCount();
    }, 30_000);

    return () => clearInterval(id);
  }, [opened, refreshPendingApprovalCount]);

  // Handle sending a message
  const handleSendMessage = useCallback(
    (messageText?: string) => {
      const text = messageText || inputValue;
      if (!text.trim() || isLoading) return;
      const fileIds = attachedFiles.map((f) => f.file_id);
      sendMessage(text, fileIds.length > 0 ? fileIds : undefined);
      setInputValue('');
      setAttachedFiles([]);
    },
    [inputValue, isLoading, sendMessage, attachedFiles]
  );

  // Handle file selection
  const handleFileSelect = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files;
      if (!files || files.length === 0) return;

      setIsUploading(true);
      const uploadThreadId = activeThreadId;
      try {
        for (const file of Array.from(files)) {
          const result = await uploadFile(file);
          if (result && activeThreadIdRef.current === uploadThreadId) {
            setAttachedFiles((prev) => [...prev, result]);
          }
        }
      } finally {
        setIsUploading(false);
        // Reset file input so the same file can be selected again
        if (fileInputRef.current) {
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

  // Uploads are bound to the thread which created them. Never carry an
  // attachment into another conversation.
  const handleSwitchThread = useCallback(
    (threadId: string) => {
      setAttachedFiles([]);
      switchThread(threadId);
    },
    [switchThread]
  );

  const handleNewThread = useCallback(() => {
    setAttachedFiles([]);
    createNewThread();
  }, [createNewThread]);

  const handleClearChat = useCallback(() => {
    setAttachedFiles([]);
    clearChat();
  }, [clearChat]);

  const handleDeleteThread = useCallback(
    (threadId: string) => {
      setAttachedFiles([]);
      deleteThread(threadId);
    },
    [deleteThread]
  );

  // Handle Enter key to send message
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        handleSendMessage();
      }
    },
    [handleSendMessage]
  );

  // Keyboard shortcut to close drawer
  useHotkeys([['Escape', handleClose]]);

  const hasMessages = messages.length > 0;

  return (
    <Drawer
      opened={opened}
      size={drawerWidth}
      position='right'
      onClose={handleClose}
      withCloseButton={false}
      closeOnClickOutside={false}
      trapFocus={false}
      lockScroll={false}
      withOverlay={false}
      transitionProps={{ transition: 'slide-left', duration: 250 }}
      styles={{
        content: {
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--mantine-color-body)',
          position: 'relative'
        },
        body: {
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          padding: 0,
          overflow: 'hidden'
        }
      }}
    >
      {/* Resize handle on the left edge */}
      <Box
        onMouseDown={handleResizeMouseDown}
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
                      animation: isSyncing ? 'spin 1s linear infinite' : 'none'
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
            <Group justify='space-between' wrap='nowrap'>
              <Tabs
                value={activeTab}
                onChange={(v) => setActiveTab((v as AIChatDrawerTab) || 'chat')}
                variant='pills'
              >
                <Tabs.List>
                  <Tabs.Tab value='chat'>{t`Chat`}</Tabs.Tab>
                  <Tabs.Tab value='approvals'>
                    <Group gap={6} wrap='nowrap'>
                      <Text size='sm'>{t`Approvals`}</Text>
                      {pendingApprovalCount > 0 && (
                        <Badge size='xs' variant='filled' color='red'>
                          {pendingApprovalCount}
                        </Badge>
                      )}
                    </Group>
                  </Tabs.Tab>
                  <Tabs.Tab value='history'>{t`History`}</Tabs.Tab>
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
                activeThreadId={activeThreadId}
                onSelectThread={handleSwitchThread}
                onNewThread={handleNewThread}
                onDeleteThread={handleDeleteThread}
                onRenameThread={renameThread}
                disabled={isLoading}
              />
            </Box>
          )}
        </Box>

        {/* Main content area */}
        <ScrollArea
          style={{ flex: 1 }}
          offsetScrollbars
          scrollbarSize={6}
          ref={scrollAreaRef}
        >
          {activeTab === 'chat' && (
            <Box p='md'>
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

                  {/* Suggestion chips */}
                  <Group gap='xs' justify='center'>
                    {suggestions.map((suggestion, index) => (
                      <Paper
                        key={index}
                        px='sm'
                        py='xs'
                        radius='xl'
                        withBorder
                        style={{
                          cursor: 'pointer',
                          transition: 'all 0.2s ease',
                          borderColor: 'var(--mantine-color-gray-3)'
                        }}
                        onClick={() => handleSendMessage(suggestion.message)}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.borderColor =
                            theme.colors.blue[4];
                          e.currentTarget.style.backgroundColor =
                            theme.colors.blue[0];
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.borderColor =
                            'var(--mantine-color-gray-3)';
                          e.currentTarget.style.backgroundColor = '';
                        }}
                      >
                        <Text size='xs' fw={500}>
                          {suggestion.label}
                        </Text>
                      </Paper>
                    ))}
                  </Group>
                </Box>
              )}

              {/* Message list */}
              {messages.map((message) => (
                <ChatMessageItem
                  key={message.id}
                  message={message}
                  threadId={activeThreadId}
                />
              ))}

              {/* HITL Result Banner - shows approval/rejection confirmation */}
              {hitlResult && (
                <HITLResultBanner
                  approved={hitlResult.approved}
                  action={hitlResult.action}
                  onDismiss={clearHITLResult}
                />
              )}

              {/* HITL Approval Card - shows when AI requests human approval */}
              {pendingHITL && (
                <HITLApprovalCard
                  request={pendingHITL}
                  onApprove={(requestId) => approveHITL(requestId)}
                  onReject={(requestId, reason) =>
                    rejectHITL(requestId, reason)
                  }
                  onDismiss={() => dismissHITL()}
                />
              )}

              {/* Error message */}
              {error && (
                <Paper p='sm' radius='md' bg='red.0' mb='md'>
                  <Text size='xs' c='red.7'>
                    {error}
                  </Text>
                </Paper>
              )}
            </Box>
          )}

          {activeTab === 'approvals' && (
            <>
              <ChatActionProposalList />
              <ApprovalInboxPanel
                statuses={[
                  'pending',
                  'in_review',
                  'changes_requested',
                  'approved',
                  'executing'
                ]}
                emptyText={t`No actions waiting for review`}
              />
            </>
          )}

          {activeTab === 'history' && (
            <ApprovalInboxPanel
              statuses={[
                'succeeded',
                'denied',
                'failed',
                'expired',
                'canceled'
              ]}
              emptyText={t`No resolved actions yet`}
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

            <Group gap='xs' mb={6} wrap='nowrap'>
              <VoiceSessionControl
                state={voice.state}
                error={voice.error}
                muted={voice.muted}
                webrtcPreview={voice.session?.webrtc_preview ?? true}
                onStart={() => void voice.start()}
                onEnd={() => void voice.end()}
                onCancel={() => void voice.cancel()}
                onToggleMute={voice.toggleMute}
                onConfirmTranscript={() => void voice.confirmPending()}
                onDiscardTranscript={voice.discardPending}
              />
              <VoiceContextBadge
                threadId={voice.session?.thread_id ?? null}
                scoped={false}
              />
            </Group>
            <VoiceTranscript
              partial={voice.partial}
              listening={voice.state === 'listening'}
              pendingConfirm={voice.pendingConfirm}
            />
            <Paper
              radius='xl'
              p='xs'
              withBorder
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
                    disabled={isLoading || isUploading}
                    loading={isUploading}
                  >
                    <IconPaperclip size={18} />
                  </ActionIcon>
                </Tooltip>
                <Textarea
                  ref={inputRef}
                  placeholder={
                    attachedFiles.length > 0
                      ? t`Add a message about attached files...`
                      : t`Type a message...`
                  }
                  value={inputValue}
                  onChange={(e) => setInputValue(e.currentTarget.value)}
                  onKeyDown={handleKeyDown}
                  autosize
                  minRows={1}
                  maxRows={4}
                  disabled={isLoading}
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
                <Group gap={4}>
                  {isLoading ? (
                    <Tooltip label={t`Stop generating`} withArrow>
                      <ActionIcon
                        aria-label='cancel-ai-chat-turn'
                        size='lg'
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
                        size='lg'
                        radius='xl'
                        variant='filled'
                        color='blue'
                        onClick={() => handleSendMessage()}
                        disabled={!inputValue.trim()}
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

            {/* Footer text */}
            <Text size='xs' c='dimmed' ta='center' mt='xs'>
              {t`AI may make mistakes. Verify important information.`}
            </Text>
          </Box>
        )}
      </Boundary>
    </Drawer>
  );
}
