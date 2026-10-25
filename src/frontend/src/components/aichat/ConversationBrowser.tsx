import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Box,
  Button,
  Group,
  Loader,
  Menu,
  Paper,
  ScrollArea,
  Stack,
  Text,
  TextInput,
  UnstyledButton
} from '@mantine/core';
import {
  IconBrain,
  IconChevronDown,
  IconEye,
  IconFileExport,
  IconMessagePlus,
  IconMessages,
  IconPencil,
  IconSearch,
  IconShare2,
  IconTrash
} from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type {
  OwnedDeletionPage,
  OwnedDeletionPlan
} from '../../functions/ownedThreadDeletion';
import type { TranscriptExportCounts } from '../../functions/transcriptExport';
import type { ChatThread } from '../../hooks/UseAIChat';
import workspaceStyles from './AssistantWorkspace.module.css';
import { DeleteOwnedThreadsModal } from './DeleteOwnedThreadsModal';
import { ExportThreadsModal } from './ExportThreadsModal';
import { type ThreadAction, ThreadActionsModal } from './ThreadActionsModal';
import type { ThreadDeleteResult } from './threadDeletion';

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
export function ConversationHistory({
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
        data-testid='search-ai-chat-history'
        aria-label={t`Search conversations`}
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
                  ? 'var(--mantine-primary-color-filled)'
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
export function ConversationBrowser({
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
            data-testid='select-ai-chat-thread'
            aria-label={t`Choose a conversation`}
            disabled={disabled}
            px='sm'
            py={6}
            style={{
              borderRadius: 'var(--mantine-radius-md)',
              border: '1px solid var(--mantine-color-default-border)',
              background: 'var(--mantine-color-body)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              width: '100%',
              minHeight: 36,
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
            data-testid='new-ai-chat-thread'
            aria-label={t`New conversation`}
            leftSection={<IconMessagePlus size={16} />}
            onClick={onNewThread}
            color='blue'
          >
            {t`New conversation`}
          </Menu.Item>

          <Menu.Item
            data-testid='export-owned-ai-chat-threads'
            aria-label={t`Export my saved conversations`}
            disabled={disabled}
            leftSection={<IconFileExport size={16} />}
            onClick={() => setExportOpen(true)}
          >{t`Export my saved conversations`}</Menu.Item>
          <Menu.Item
            data-testid='delete-owned-ai-chat-threads'
            aria-label={t`Delete my saved conversations`}
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
                      ? 'var(--mantine-primary-color-light)'
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
                      className={workspaceStyles.control}
                      data-testid={`rename-ai-chat-thread-${thread.id}`}
                      aria-label={t`Rename ${thread.title}`}
                      size={32}
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
                          className={workspaceStyles.control}
                          data-testid={`memory-ai-chat-thread-${thread.id}`}
                          aria-label={t`Inspect memory for ${thread.title}`}
                          title={t`What this chat remembers`}
                          size={32}
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
                        className={workspaceStyles.control}
                        data-testid={`share-ai-chat-thread-${thread.id}`}
                        aria-label={t`Share ${thread.title}`}
                        size={32}
                        variant='subtle'
                        color='gray'
                        disabled={disabled}
                        onClick={() => setAction({ kind: 'share', thread })}
                      >
                        <IconShare2 size={12} />
                      </ActionIcon>
                    )}
                    <ActionIcon
                      className={workspaceStyles.control}
                      data-testid={`delete-ai-chat-thread-${thread.id}`}
                      aria-label={t`Delete ${thread.title}`}
                      size={32}
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
                          ? 'var(--mantine-primary-color-light)'
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
