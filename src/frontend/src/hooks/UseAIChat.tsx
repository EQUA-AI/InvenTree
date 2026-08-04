import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '../App';
import { getCsrfCookie } from '../functions/auth';
import { useLocalState } from '../states/LocalState';
import { useUserState } from '../states/UserState';

// ===== Retry Configuration =====

/**
 * Configuration for retry logic
 */
interface RetryConfig {
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
  jitter: boolean;
}

const DEFAULT_RETRY_CONFIG: RetryConfig = {
  maxAttempts: 3,
  baseDelayMs: 1000,
  maxDelayMs: 10000,
  jitter: true
};

/**
 * Calculate delay with exponential backoff and optional jitter
 */
function calculateRetryDelay(
  attempt: number,
  config: RetryConfig,
  retryAfter?: number
): number {
  if (retryAfter) {
    return Math.min(retryAfter * 1000, config.maxDelayMs);
  }

  let delay = config.baseDelayMs * 2 ** attempt;
  delay = Math.min(delay, config.maxDelayMs);

  if (config.jitter) {
    const jitterRange = delay * 0.1;
    delay += (Math.random() * 2 - 1) * jitterRange;
  }

  return Math.max(100, delay);
}

/**
 * Check if an error is retryable
 */
function isRetryableError(error: unknown): boolean {
  if (error instanceof Response) {
    // Retry on 429 (rate limit), 500, 502, 503, 504
    return [429, 500, 502, 503, 504].includes(error.status);
  }

  if (error instanceof Error) {
    const message = error.message.toLowerCase();
    return (
      message.includes('network') ||
      message.includes('failed to fetch') ||
      message.includes('load failed') ||
      message.includes('timeout') ||
      message.includes('connection') ||
      message.includes('rate limit') ||
      message.includes('429') ||
      message.includes('503')
    );
  }

  return false;
}

/**
 * Extract Retry-After header value in seconds
 */
function extractRetryAfter(response: Response): number | undefined {
  const retryAfter = response.headers.get('Retry-After');
  if (retryAfter) {
    const seconds = Number.parseInt(retryAfter, 10);
    return Number.isNaN(seconds) ? undefined : seconds;
  }
  return undefined;
}

/**
 * Sleep for a specified number of milliseconds
 */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Request cancelled', 'AbortError'));
      return;
    }

    const onAbort = () => {
      clearTimeout(timeout);
      reject(new DOMException('Request cancelled', 'AbortError'));
    };
    const timeout = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/**
 * Resolve an AI endpoint against the configured InvenTree backend. In local
 * development the frontend and Django run on different ports, so relative
 * fetches would otherwise be sent to Vite instead of the authenticated API.
 */
function resolveBackendUrl(path: string, backendHost: string): string {
  return new URL(path, `${backendHost.replace(/\/$/, '')}/`).toString();
}

/**
 * Django requires the CSRF cookie value on every unsafe session-authenticated
 * fetch. Axios supplies this automatically, but the streaming and upload
 * transports use fetch directly.
 */
function csrfHeaders(): Record<string, string> {
  const token = getCsrfCookie();
  return token ? { 'X-CSRFToken': token } : {};
}

/** Generate one opaque turn key which is reused for every transport retry. */
function generateIdempotencyKey(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return `typed:${crypto.randomUUID()}`;
  }

  return `typed:${Date.now()}:${Math.random().toString(36).substring(2)}`;
}

/**
 * AG-UI Protocol Event Types
 * @see https://docs.ag-ui.com/concepts/events
 */
export enum AGUIEventType {
  // Lifecycle Events
  RUN_STARTED = 'RUN_STARTED',
  RUN_FINISHED = 'RUN_FINISHED',
  RUN_ERROR = 'RUN_ERROR',
  RUN_CANCELLED = 'RUN_CANCELLED',
  STEP_STARTED = 'STEP_STARTED',
  STEP_FINISHED = 'STEP_FINISHED',

  // Text Message Events
  TEXT_MESSAGE_START = 'TEXT_MESSAGE_START',
  TEXT_MESSAGE_CONTENT = 'TEXT_MESSAGE_CONTENT',
  TEXT_MESSAGE_END = 'TEXT_MESSAGE_END',
  TEXT_MESSAGE_CHUNK = 'TEXT_MESSAGE_CHUNK',

  // Tool Call Events
  TOOL_CALL_START = 'TOOL_CALL_START',
  TOOL_CALL_ARGS = 'TOOL_CALL_ARGS',
  TOOL_CALL_END = 'TOOL_CALL_END',
  TOOL_CALL_RESULT = 'TOOL_CALL_RESULT',
  TOOL_CALL_CHUNK = 'TOOL_CALL_CHUNK',

  // HITL Events
  HITL_REQUIRED = 'HITL_REQUIRED',
  HITL_APPROVED = 'HITL_APPROVED',
  HITL_REJECTED = 'HITL_REJECTED',
  HITL_TIMEOUT = 'HITL_TIMEOUT',

  // State Management Events
  STATE_SNAPSHOT = 'STATE_SNAPSHOT',
  STATE_DELTA = 'STATE_DELTA',
  MESSAGES_SNAPSHOT = 'MESSAGES_SNAPSHOT',

  // Special Events
  RAW = 'RAW',
  CUSTOM = 'CUSTOM'
}

/**
 * AG-UI Protocol Event interfaces
 */
export interface AGUIBaseEvent {
  type: AGUIEventType;
  timestamp?: string;
  rawEvent?: unknown;
}

export interface AGUIRunStartedEvent extends AGUIBaseEvent {
  type: AGUIEventType.RUN_STARTED;
  threadId: string;
  runId: string;
  parentRunId?: string;
  input?: unknown;
}

export interface AGUIRunFinishedEvent extends AGUIBaseEvent {
  type: AGUIEventType.RUN_FINISHED;
  threadId: string;
  runId: string;
  result?: unknown;
}

export interface AGUIRunErrorEvent extends AGUIBaseEvent {
  type: AGUIEventType.RUN_ERROR;
  message: string;
  code?: string;
}

export interface AGUITextMessageStartEvent extends AGUIBaseEvent {
  type: AGUIEventType.TEXT_MESSAGE_START;
  messageId: string;
  role: 'developer' | 'system' | 'assistant' | 'user' | 'tool';
}

export interface AGUITextMessageContentEvent extends AGUIBaseEvent {
  type: AGUIEventType.TEXT_MESSAGE_CONTENT;
  messageId: string;
  delta: string;
}

export interface AGUITextMessageEndEvent extends AGUIBaseEvent {
  type: AGUIEventType.TEXT_MESSAGE_END;
  messageId: string;
}

export interface AGUIToolCallStartEvent extends AGUIBaseEvent {
  type: AGUIEventType.TOOL_CALL_START;
  toolCallId: string;
  toolCallName: string;
  parentMessageId?: string;
}

export interface AGUIToolCallArgsEvent extends AGUIBaseEvent {
  type: AGUIEventType.TOOL_CALL_ARGS;
  toolCallId: string;
  delta: string;
}

export interface AGUIToolCallEndEvent extends AGUIBaseEvent {
  type: AGUIEventType.TOOL_CALL_END;
  toolCallId: string;
}

export interface AGUIToolCallResultEvent extends AGUIBaseEvent {
  type: AGUIEventType.TOOL_CALL_RESULT;
  messageId: string;
  toolCallId: string;
  content: string;
  role?: string;
}

/**
 * HITL (Human-in-the-Loop) event interfaces
 */
export interface AGUIHITLRequiredEvent extends AGUIBaseEvent {
  type: AGUIEventType.HITL_REQUIRED;
  action: string;
  details: Record<string, unknown>;
  timeout_seconds: number;
}

export interface AGUIHITLApprovedEvent extends AGUIBaseEvent {
  type: AGUIEventType.HITL_APPROVED;
  action: string;
  approver: string;
}

export interface AGUIHITLRejectedEvent extends AGUIBaseEvent {
  type: AGUIEventType.HITL_REJECTED;
  action: string;
  reason: string;
  rejecter: string;
}

export type AGUIEvent =
  | AGUIRunStartedEvent
  | AGUIRunFinishedEvent
  | AGUIRunErrorEvent
  | AGUITextMessageStartEvent
  | AGUITextMessageContentEvent
  | AGUITextMessageEndEvent
  | AGUIToolCallStartEvent
  | AGUIToolCallArgsEvent
  | AGUIToolCallEndEvent
  | AGUIToolCallResultEvent
  | AGUIHITLRequiredEvent
  | AGUIHITLApprovedEvent
  | AGUIHITLRejectedEvent
  | AGUIBaseEvent;

/**
 * HITL Request structure for UI
 */
export interface HITLRequest {
  id: string;
  action: string;
  title: string;
  description: string;
  details: Record<string, unknown>;
  items?: Array<{
    id: string;
    name: string;
    quantity: number;
    unitPrice?: number;
    total?: number;
    description?: string;
  }>;
  totalValue?: number;
  currency?: string;
  riskLevel: 'low' | 'medium' | 'high';
  timeoutSeconds: number;
  createdAt: Date;
  threadId: string;
}

/**
 * Uploaded file info returned from the server
 */
export interface UploadedFile {
  file_id: string;
  filename: string;
  size: number;
  content_type: string;
  thread_id: string;
}

/**
 * One revision-bound citation from the diagnosis rail's canonical response.
 * Server-authorized reads only; the shape mirrors ai.core EvidenceEntry.
 */
export interface DiagnosisEvidence {
  source_type: string;
  source_id: string;
  source_revision: string;
  as_of: string;
  authorization_class: string;
  claim: string;
  locator?: {
    field?: string | null;
    page?: number | null;
    chunk?: string | null;
  };
}

/**
 * Chat message structure
 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  toolCallId?: string;
  toolCallName?: string;
  /** Present only for diagnosis-rail answers: [] means visibly uncited. */
  evidence?: DiagnosisEvidence[];
  /** Model-declared confidence level accompanying `evidence`. */
  confidence?: string;
}

/**
 * Chat conversation/thread structure
 */
export interface ChatThread {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: Date;
  updatedAt: Date;
}

/**
 * Serializable thread for storage
 */
interface StoredThread {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
  isPersisted?: boolean;
}

/**
 * Server thread info from /threads endpoint
 */
interface ServerThreadInfo {
  thread_id: string;
  title: string;
  message_count: number;
  turn_count: number;
  summary: string;
  created_at: string | null;
  last_activity: string | null;
  is_persisted: boolean;
}

/**
 * Server thread sync response
 */
interface ThreadSyncResponse {
  threads: ServerThreadInfo[];
  sync_token: string | null;
  has_more: boolean;
}

/**
 * Server message format
 */
interface ServerMessage {
  id: string;
  role: string;
  content: string;
  timestamp: string;
  tool_name?: string;
  workflow_id?: string;
}

/**
 * AI Chat API configuration
 */
export interface AIChatConfig {
  /** API endpoint for chat completions */
  endpoint?: string;
  /** Enable streaming responses */
  streaming?: boolean;
  /** System prompt to set AI behavior */
  systemPrompt?: string;
  /** Maximum tokens in response */
  maxTokens?: number;
}

const DEFAULT_CONFIG: AIChatConfig = {
  endpoint: '/api/ai/chat/',
  streaming: true,
  systemPrompt: `You are a helpful AI assistant for AIMMS, an inventory management system.
You can help users with:
- Searching for parts, stock items, and orders
- Creating new parts, stock locations, and suppliers
- Understanding inventory workflows and best practices
- Automating repetitive tasks

Be concise, helpful, and proactive in suggesting actions.`,
  maxTokens: 2048
};

const STORAGE_KEY = 'ai-chat-threads';

/**
 * Load threads from localStorage
 */
function loadStoredThreads(): StoredThread[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : [];
  } catch {
    return [];
  }
}

/**
 * Save threads to localStorage
 */
function saveStoredThreads(threads: StoredThread[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(threads));
  } catch {
    console.error('Failed to save chat threads to localStorage');
  }
}

/**
 * Fetch threads from server
 */
async function fetchServerThreads(
  host: string
): Promise<ThreadSyncResponse | null> {
  try {
    const response = await fetch(
      `${host}/threads?include_persisted=true&limit=50`,
      {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json'
        },
        credentials: 'include'
      }
    );

    if (!response.ok) {
      console.error('Failed to fetch server threads:', response.status);
      return null;
    }

    return (await response.json()) as ThreadSyncResponse;
  } catch (error) {
    console.error('Error fetching server threads:', error);
    return null;
  }
}

/**
 * Fetch a specific thread with messages from server
 */
async function fetchServerThread(
  threadId: string,
  host: string
): Promise<{
  messages: ChatMessage[];
  title: string;
  created_at: string;
  updated_at: string;
} | null> {
  try {
    const response = await fetch(
      `${host}/threads/${encodeURIComponent(threadId)}?include_messages=true&message_limit=50`,
      {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json'
        },
        credentials: 'include'
      }
    );

    if (!response.ok) {
      console.error('Failed to fetch server thread:', response.status);
      return null;
    }

    const data = await response.json();

    // Convert server messages to ChatMessage format
    const messages: ChatMessage[] = (data.messages || []).map(
      (m: ServerMessage) => ({
        id: m.id,
        role: m.role as ChatMessage['role'],
        content: m.content,
        timestamp: new Date(m.timestamp),
        toolCallName: m.tool_name
      })
    );

    return {
      messages,
      title: data.title || data.summary?.substring(0, 50) || 'Chat',
      created_at: data.created_at,
      updated_at: data.updated_at
    };
  } catch (error) {
    console.error('Error fetching server thread:', error);
    return null;
  }
}

/**
 * Delete a thread on the server
 */
async function deleteServerThread(
  threadId: string,
  host: string
): Promise<boolean> {
  try {
    const response = await fetch(
      `${host}/threads/${encodeURIComponent(threadId)}`,
      {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders()
        },
        credentials: 'include'
      }
    );
    return response.ok;
  } catch (error) {
    console.error('Error deleting server thread:', error);
    return false;
  }
}

/**
 * Update thread title on server
 */
async function updateServerThreadTitle(
  threadId: string,
  title: string,
  host: string
): Promise<boolean> {
  try {
    const response = await fetch(
      `${host}/threads/${encodeURIComponent(threadId)}?title=${encodeURIComponent(title)}`,
      {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders()
        },
        credentials: 'include'
      }
    );
    return response.ok;
  } catch (error) {
    console.error('Error updating server thread title:', error);
    return false;
  }
}

/**
 * Generate a unique message ID
 */
function generateMessageId(): string {
  return `msg_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
}

/**
 * Generate a unique thread ID
 */
function generateThreadId(): string {
  return `thread_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
}

/**
 * Generate a title from the first message
 */
function generateThreadTitle(message: string): string {
  const maxLength = 30;
  const trimmed = message.trim().replace(/\n/g, ' ');
  return trimmed.length > maxLength
    ? `${trimmed.substring(0, maxLength)}...`
    : trimmed;
}

/**
 * Format HITL action into user-friendly title
 */
function formatHITLTitle(
  action: string,
  details: Record<string, unknown>
): string {
  switch (action) {
    case 'create_purchase_order':
      return `Create Purchase Order${details.supplier ? ` - ${details.supplier}` : ''}`;
    case 'create_sales_order':
      return `Create Sales Order${details.customer ? ` - ${details.customer}` : ''}`;
    case 'create_build_order':
      return `Create Build Order${details.part_name ? ` - ${details.part_name}` : ''}`;
    case 'update_stock':
      return `Update Stock${details.location ? ` at ${details.location}` : ''}`;
    case 'delete_item':
      return `Delete ${details.item_type || 'Item'}`;
    case 'send_email':
      return `Send Email${details.recipient ? ` to ${details.recipient}` : ''}`;
    case 'bulk_operation':
      return `Bulk ${details.operation || 'Operation'} (${details.count || '?'} items)`;
    default:
      return action.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }
}

/**
 * Format HITL action into user-friendly description
 */
function formatHITLDescription(
  action: string,
  details: Record<string, unknown>
): string {
  const itemCount =
    (details.items as unknown[])?.length || details.line_count || 0;
  const totalValue = details.total_value as number;

  switch (action) {
    case 'create_purchase_order':
      return `Create a purchase order with ${itemCount} line items${totalValue ? ` totaling ${totalValue}` : ''}`;
    case 'create_sales_order':
      return `Create a sales order with ${itemCount} line items${totalValue ? ` totaling ${totalValue}` : ''}`;
    case 'create_build_order':
      return `Start a build order for ${details.quantity || 1} unit(s) of ${details.part_name || 'the part'}`;
    case 'update_stock':
      return `Update stock levels for ${itemCount || 1} item(s)`;
    case 'delete_item':
      return `Permanently delete ${details.item_name || 'this item'}. This cannot be undone.`;
    case 'send_email':
      return `Send an email to ${details.recipient || 'recipient'}`;
    case 'bulk_operation':
      return `Perform ${details.operation || 'operation'} on ${details.count || 'multiple'} items`;
    default:
      return (
        (details.description as string) || 'This action requires your approval'
      );
  }
}

/**
 * Determine risk level based on action type and details
 */
function determineRiskLevel(
  action: string,
  details: Record<string, unknown>
): 'low' | 'medium' | 'high' {
  // High risk actions
  if (action === 'delete_item' || action === 'bulk_operation') {
    return 'high';
  }

  // Check value thresholds
  const totalValue = details.total_value as number;
  if (totalValue !== undefined) {
    if (totalValue > 10000) return 'high';
    if (totalValue > 1000) return 'medium';
  }

  // Check item count
  const itemCount =
    (details.items as unknown[])?.length || (details.count as number) || 0;
  if (itemCount > 50) return 'high';
  if (itemCount > 10) return 'medium';

  // Default based on action type
  switch (action) {
    case 'create_purchase_order':
    case 'create_sales_order':
      return 'medium';
    case 'send_email':
    case 'external_api':
      return 'medium';
    default:
      return 'low';
  }
}

/**
 * Send HITL approval to server
 */
async function sendHITLApproval(
  threadId: string,
  requestId: string,
  approved: boolean,
  reason: string,
  host: string
): Promise<boolean> {
  try {
    const response = await fetch(`${host}/hitl/respond`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...csrfHeaders()
      },
      credentials: 'include',
      body: JSON.stringify({
        thread_id: threadId,
        request_id: requestId,
        approved,
        reason
      })
    });
    return response.ok;
  } catch (error) {
    console.error('Error sending HITL response:', error);
    return false;
  }
}

/**
 * Custom hook for AI Chat functionality
 * Designed to integrate with Azure AI Foundry backend
 * Supports multiple conversation threads
 */
export function useAIChat(config: AIChatConfig = {}) {
  const mergedConfig = { ...DEFAULT_CONFIG, ...config };

  // Get current user from InvenTree auth state
  const user = useUserState();
  const isLoggedIn = user.isLoggedIn();
  const backendHost = useLocalState((state) => state.getHost());

  // State for stored threads (persisted to localStorage)
  const [storedThreads, setStoredThreads] =
    useState<StoredThread[]>(loadStoredThreads);

  const [activeThreadId, setActiveThreadId] = useState<string>(() => {
    // Initialize with the most recent thread or create a new one
    const threads = loadStoredThreads();
    if (threads.length > 0) {
      return threads[0].id;
    }
    return generateThreadId();
  });

  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    // Load messages from active thread
    const threads = loadStoredThreads();
    const activeThread = threads.find(
      (t: StoredThread) => t.id === activeThreadId
    );
    return activeThread?.messages || [];
  });

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSyncTime, setLastSyncTime] = useState<Date | null>(null);

  // HITL (Human-in-the-Loop) state
  const [pendingHITL, setPendingHITL] = useState<HITLRequest | null>(null);
  const [hitlResult, setHitlResult] = useState<{
    approved: boolean;
    action: string;
  } | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const syncInProgressRef = useRef(false);
  const storedThreadsRef = useRef(storedThreads);
  const activeThreadIdRef = useRef(activeThreadId);
  storedThreadsRef.current = storedThreads;
  activeThreadIdRef.current = activeThreadId;

  // Get absolute chat and API URLs from the configured Django backend.
  const chatEndpoint = resolveBackendUrl(
    mergedConfig.endpoint || DEFAULT_CONFIG.endpoint!,
    backendHost
  ).replace(/\/$/, '');
  const aiHost = chatEndpoint.replace(/\/chat\/?$/, '');

  /**
   * Sync threads with the server
   * Merges server threads with local threads, server wins on conflicts
   */
  const syncThreads = useCallback(async () => {
    if (syncInProgressRef.current || !isLoggedIn) {
      return;
    }

    syncInProgressRef.current = true;
    setIsSyncing(true);

    try {
      const serverData = await fetchServerThreads(aiHost);

      if (!serverData) {
        return;
      }

      const localThreads = storedThreadsRef.current;
      const serverIds = new Set(
        serverData.threads.map((thread) => thread.thread_id)
      );
      const mergedThreads: StoredThread[] = serverData.threads.map(
        (serverThread) => ({
          id: serverThread.thread_id,
          title: serverThread.title || serverThread.summary || 'Chat',
          // A successful server sync makes durable history authoritative. The
          // detail endpoint supplies messages when this thread becomes active.
          messages: [],
          createdAt: serverThread.created_at || new Date().toISOString(),
          updatedAt: serverThread.last_activity || new Date().toISOString(),
          isPersisted: true
        })
      );

      // Preserve only genuinely local legacy conversations. Threads which
      // were previously known to be durable but disappeared from the server
      // are removed rather than resurrected from stale localStorage.
      for (const localThread of localThreads) {
        if (!serverIds.has(localThread.id) && !localThread.isPersisted) {
          mergedThreads.push(localThread);
        }
      }

      mergedThreads.sort(
        (a, b) =>
          new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
      );

      let nextActiveThreadId = activeThreadIdRef.current;
      if (!mergedThreads.some((thread) => thread.id === nextActiveThreadId)) {
        nextActiveThreadId = mergedThreads[0]?.id || generateThreadId();
        activeThreadIdRef.current = nextActiveThreadId;
        setActiveThreadId(nextActiveThreadId);
      }

      storedThreadsRef.current = mergedThreads;
      saveStoredThreads(mergedThreads);
      setStoredThreads(mergedThreads);

      if (serverIds.has(nextActiveThreadId)) {
        const serverThread = await fetchServerThread(
          nextActiveThreadId,
          aiHost
        );
        if (serverThread) {
          const withAuthoritativeMessages = mergedThreads.map((thread) =>
            thread.id === nextActiveThreadId
              ? {
                  ...thread,
                  title: serverThread.title || thread.title,
                  messages: serverThread.messages,
                  createdAt: serverThread.created_at || thread.createdAt,
                  updatedAt: serverThread.updated_at || thread.updatedAt
                }
              : thread
          );
          storedThreadsRef.current = withAuthoritativeMessages;
          saveStoredThreads(withAuthoritativeMessages);
          setStoredThreads(withAuthoritativeMessages);
          if (activeThreadIdRef.current === nextActiveThreadId) {
            setMessages(serverThread.messages);
          }
        } else if (activeThreadIdRef.current === nextActiveThreadId) {
          setMessages([]);
        }
      } else {
        const localActive = mergedThreads.find(
          (thread) => thread.id === nextActiveThreadId
        );
        if (activeThreadIdRef.current === nextActiveThreadId) {
          setMessages(localActive?.messages || []);
        }
      }

      setLastSyncTime(new Date());
    } catch (error) {
      console.error('Error syncing threads:', error);
    } finally {
      setIsSyncing(false);
      syncInProgressRef.current = false;
    }
  }, [isLoggedIn, aiHost]);

  /**
   * Load thread messages from server if not available locally
   */
  const loadThreadFromServer = useCallback(
    async (threadId: string): Promise<ChatMessage[] | null> => {
      const serverData = await fetchServerThread(threadId, aiHost);
      if (serverData) {
        // Update stored thread with messages
        setStoredThreads((prev) => {
          const idx = prev.findIndex((t) => t.id === threadId);
          if (idx >= 0) {
            const updated = [...prev];
            updated[idx] = {
              ...updated[idx],
              messages: serverData.messages,
              title: serverData.title || updated[idx].title,
              createdAt: serverData.created_at || updated[idx].createdAt,
              updatedAt: serverData.updated_at || updated[idx].updatedAt
            };
            saveStoredThreads(updated);
            storedThreadsRef.current = updated;
            return updated;
          }
          return prev;
        });
        return serverData.messages;
      }
      return null;
    },
    [aiHost]
  );

  // Sync on mount and when user changes
  useEffect(() => {
    if (isLoggedIn) {
      syncThreads();
    }
  }, [isLoggedIn]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Get all threads sorted by most recent
   */
  const threads: ChatThread[] = storedThreads.map((t: StoredThread) => ({
    ...t,
    createdAt: new Date(t.createdAt),
    updatedAt: new Date(t.updatedAt)
  }));

  /**
   * Save current messages to the active thread
   */
  const saveCurrentThread = useCallback(
    (currentMessages: ChatMessage[], title?: string, markPersisted = false) => {
      setStoredThreads((prev: StoredThread[]) => {
        const existingIndex = prev.findIndex(
          (t: StoredThread) => t.id === activeThreadId
        );
        const now = new Date().toISOString();

        const updatedThread: StoredThread = {
          id: activeThreadId,
          title:
            title ||
            prev[existingIndex]?.title ||
            (currentMessages[0]
              ? generateThreadTitle(currentMessages[0].content)
              : 'New Chat'),
          messages: currentMessages,
          createdAt: prev[existingIndex]?.createdAt || now,
          updatedAt: now,
          isPersisted:
            markPersisted || prev[existingIndex]?.isPersisted || false
        };

        let newThreads: StoredThread[];
        if (existingIndex >= 0) {
          // Update existing thread and move to top
          newThreads = [...prev];
          newThreads.splice(existingIndex, 1);
          newThreads = [updatedThread, ...newThreads];
        } else {
          // Add new thread at the top
          newThreads = [updatedThread, ...prev];
        }

        // Persist to localStorage
        saveStoredThreads(newThreads);
        storedThreadsRef.current = newThreads;
        return newThreads;
      });
    },
    [activeThreadId]
  );

  /**
   * Switch to a different thread
   * Loads messages from server if not available locally
   */
  const switchThread = useCallback(
    async (threadId: string) => {
      // Save current thread before switching
      if (messages.length > 0) {
        saveCurrentThread(messages);
      }

      const thread = storedThreads.find((t: StoredThread) => t.id === threadId);

      if (thread) {
        activeThreadIdRef.current = threadId;
        setActiveThreadId(threadId);
        setError(null);

        // If thread has no messages locally but is persisted, load from server
        if (thread.messages.length === 0 && thread.isPersisted) {
          setIsLoading(true);
          try {
            const serverMessages = await loadThreadFromServer(threadId);
            if (serverMessages) {
              setMessages(serverMessages);
            } else {
              setMessages([]);
            }
          } catch (err) {
            console.error('Failed to load thread from server:', err);
            setMessages([]);
          } finally {
            setIsLoading(false);
          }
        } else {
          setMessages(thread.messages);
        }
      }
    },
    [messages, storedThreads, saveCurrentThread, loadThreadFromServer]
  );

  /**
   * Create a new thread and switch to it
   */
  const createNewThread = useCallback(() => {
    // Save current thread before creating new one
    if (messages.length > 0) {
      saveCurrentThread(messages);
    }

    const newId = generateThreadId();
    activeThreadIdRef.current = newId;
    setActiveThreadId(newId);
    setMessages([]);
    setError(null);
    return newId;
  }, [messages, saveCurrentThread]);

  /**
   * Delete a thread (both locally and on server)
   */
  const deleteThread = useCallback(
    async (threadId: string) => {
      const durable = storedThreadsRef.current.find(
        (thread) => thread.id === threadId
      )?.isPersisted;
      if (durable && !(await deleteServerThread(threadId, aiHost))) {
        setError('Failed to delete conversation from the server');
        return;
      }

      // Then delete locally
      setStoredThreads((prev: StoredThread[]) => {
        const newThreads = prev.filter((t: StoredThread) => t.id !== threadId);
        saveStoredThreads(newThreads);
        storedThreadsRef.current = newThreads;
        return newThreads;
      });

      // If deleting active thread, switch to another or create new
      if (threadId === activeThreadId) {
        const remaining = storedThreads.filter(
          (t: StoredThread) => t.id !== threadId
        );
        if (remaining.length > 0) {
          activeThreadIdRef.current = remaining[0].id;
          setActiveThreadId(remaining[0].id);
          setMessages(remaining[0].messages);
        } else {
          const newId = generateThreadId();
          activeThreadIdRef.current = newId;
          setActiveThreadId(newId);
          setMessages([]);
        }
      }
    },
    [activeThreadId, storedThreads, aiHost]
  );

  /**
   * Rename a thread (both locally and on server)
   */
  const renameThread = useCallback(
    async (threadId: string, newTitle: string) => {
      const durable = storedThreadsRef.current.find(
        (thread) => thread.id === threadId
      )?.isPersisted;
      if (
        durable &&
        !(await updateServerThreadTitle(threadId, newTitle, aiHost))
      ) {
        setError('Failed to rename conversation on the server');
        return;
      }

      setStoredThreads((prev: StoredThread[]) => {
        const newThreads = prev.map((t: StoredThread) =>
          t.id === threadId
            ? { ...t, title: newTitle, updatedAt: new Date().toISOString() }
            : t
        );
        saveStoredThreads(newThreads);
        storedThreadsRef.current = newThreads;
        return newThreads;
      });
    },
    [aiHost]
  );

  /**
   * Add a message to the chat
   */
  const addMessage = useCallback(
    (role: ChatMessage['role'], content: string): ChatMessage => {
      const message: ChatMessage = {
        id: generateMessageId(),
        role,
        content,
        timestamp: new Date()
      };
      setMessages((prev) => [...prev, message]);
      return message;
    },
    []
  );

  /**
   * Update a message's content (used for streaming)
   */
  const updateMessage = useCallback(
    (messageId: string, content: string) => {
      setMessages((prev) => {
        const updated = prev.map((msg) =>
          msg.id === messageId ? { ...msg, content, isStreaming: false } : msg
        );
        // Cancellation and terminal errors are part of the visible history and
        // must survive closing or reloading the drawer.
        saveCurrentThread(updated);
        return updated;
      });
    },
    [saveCurrentThread]
  );

  /** Drop partial output before replaying the same turn after a failure. */
  const resetStreamingMessage = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === messageId ? { ...msg, content: '', isStreaming: true } : msg
      )
    );
  }, []);

  /**
   * Append content to a streaming message
   */
  const appendToMessage = useCallback((messageId: string, chunk: string) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === messageId ? { ...msg, content: msg.content + chunk } : msg
      )
    );
  }, []);

  /** Attach diagnosis-rail provenance (citations + declared confidence). */
  const attachProvenance = useCallback(
    (messageId: string, evidence: DiagnosisEvidence[], confidence: string) => {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === messageId ? { ...msg, evidence, confidence } : msg
        )
      );
    },
    []
  );

  /**
   * Send a message and get AI response
   * Handles AG-UI protocol events from Microsoft Agent Framework
   */
  const sendMessage = useCallback(
    async (userContent: string, fileIds?: string[]) => {
      if (!userContent.trim() || isLoading) return;

      setError(null);

      // Add user message
      addMessage('user', userContent.trim());

      // Create placeholder for assistant response
      const assistantMessage: ChatMessage = {
        id: generateMessageId(),
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        isStreaming: true
      };
      setMessages((prev) => {
        const updated = [...prev, assistantMessage];
        // Save thread with new messages (auto-generates title from first message)
        saveCurrentThread(
          updated,
          messages.length === 0 ? generateThreadTitle(userContent) : undefined
        );
        return updated;
      });

      setIsLoading(true);
      abortControllerRef.current = new AbortController();

      // Track message IDs from AG-UI events
      const messageIdMap = new Map<string, string>();
      // This key identifies the logical turn, not an individual fetch. It is
      // deliberately created outside the retry loop.
      const idempotencyKey = generateIdempotencyKey();

      try {
        // Browser-supplied identity, authentication state, prompt policy, and
        // workflow hints are not authority. The mounted Django boundary
        // derives all trusted turn context from the authenticated session.
        const payload = {
          message: userContent.trim(),
          thread_id: activeThreadId,
          file_ids: fileIds && fileIds.length > 0 ? fileIds : undefined,
          idempotency_key: idempotencyKey
        };

        // Use streaming endpoint for real-time AG-UI events
        const streamingEndpoint = mergedConfig.streaming
          ? `${chatEndpoint.replace(/\/$/, '')}/stream`
          : chatEndpoint;

        if (mergedConfig.streaming) {
          // Streaming response handling with AG-UI protocol
          // Includes retry logic for transient failures
          let retryAttempt = 0;
          const retryConfig = DEFAULT_RETRY_CONFIG;

          while (retryAttempt < retryConfig.maxAttempts) {
            try {
              if (retryAttempt > 0) {
                // A replay can return the complete durable result. Remove any
                // partial output from the failed connection before replaying
                // so content is never duplicated.
                resetStreamingMessage(assistantMessage.id);
              }
              messageIdMap.clear();

              const response = await fetch(streamingEndpoint!, {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  Accept: 'text/event-stream',
                  'Idempotency-Key': idempotencyKey,
                  ...csrfHeaders()
                },
                body: JSON.stringify(payload),
                signal: abortControllerRef.current.signal,
                credentials: 'include'
              });

              // Check for rate limiting
              if (response.status === 429) {
                if (retryAttempt >= retryConfig.maxAttempts - 1) {
                  throw new Error('HTTP error! status: 429');
                }
                const retryAfter = extractRetryAfter(response);
                const delay = calculateRetryDelay(
                  retryAttempt,
                  retryConfig,
                  retryAfter
                );
                console.warn(
                  `[AI Chat] Rate limited. Retrying in ${delay}ms (attempt ${retryAttempt + 1}/${retryConfig.maxAttempts})`
                );
                await sleep(delay, abortControllerRef.current.signal);
                retryAttempt++;
                continue;
              }

              if (!response.ok) {
                const error = new Error(
                  `HTTP error! status: ${response.status}`
                );
                if (
                  isRetryableError(response) &&
                  retryAttempt < retryConfig.maxAttempts - 1
                ) {
                  const delay = calculateRetryDelay(retryAttempt, retryConfig);
                  console.warn(
                    `[AI Chat] Retryable error ${response.status}. Retrying in ${delay}ms (attempt ${retryAttempt + 1}/${retryConfig.maxAttempts})`
                  );
                  await sleep(delay, abortControllerRef.current.signal);
                  retryAttempt++;
                  continue;
                }
                throw error;
              }

              // Success - process the stream
              const reader = response.body?.getReader();
              const decoder = new TextDecoder();

              if (reader) {
                let buffer = '';

                while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;

                  const chunk = decoder.decode(value, { stream: true });
                  buffer += chunk;

                  // Parse SSE events (AG-UI uses Server-Sent Events)
                  const lines = buffer.split('\n');
                  buffer = lines.pop() || '';

                  for (const line of lines) {
                    if (!line.trim() || line.startsWith(':')) continue;

                    if (line.startsWith('data: ')) {
                      const data = line.slice(6).trim();
                      if (data === '[DONE]') continue;

                      try {
                        const event = JSON.parse(data) as AGUIEvent;

                        // Handle AG-UI protocol events
                        switch (event.type) {
                          case AGUIEventType.RUN_STARTED: {
                            const runEvent = event as AGUIRunStartedEvent;
                            console.debug(
                              '[AG-UI] Run started:',
                              runEvent.runId,
                              'Thread:',
                              runEvent.threadId
                            );
                            break;
                          }

                          case AGUIEventType.TEXT_MESSAGE_START: {
                            const msgStartEvent =
                              event as AGUITextMessageStartEvent;
                            if (msgStartEvent.role === 'assistant') {
                              messageIdMap.set(
                                msgStartEvent.messageId,
                                assistantMessage.id
                              );
                            }
                            break;
                          }

                          case AGUIEventType.TEXT_MESSAGE_CONTENT: {
                            const contentEvent =
                              event as AGUITextMessageContentEvent;
                            const localMsgId =
                              messageIdMap.get(contentEvent.messageId) ||
                              assistantMessage.id;
                            if (contentEvent.delta) {
                              appendToMessage(localMsgId, contentEvent.delta);
                            }
                            break;
                          }

                          case AGUIEventType.TEXT_MESSAGE_END: {
                            // Message complete, will be finalized on RUN_FINISHED
                            break;
                          }

                          case AGUIEventType.TEXT_MESSAGE_CHUNK: {
                            // Convenience event - auto-expands to Start → Content → End
                            const chunkEvent = event as unknown as {
                              messageId?: string;
                              role?: string;
                              delta?: string;
                            };
                            if (
                              chunkEvent.messageId &&
                              !messageIdMap.has(chunkEvent.messageId)
                            ) {
                              messageIdMap.set(
                                chunkEvent.messageId,
                                assistantMessage.id
                              );
                            }
                            if (chunkEvent.delta) {
                              appendToMessage(
                                assistantMessage.id,
                                chunkEvent.delta
                              );
                            }
                            break;
                          }

                          case AGUIEventType.TOOL_CALL_START: {
                            const toolEvent = event as AGUIToolCallStartEvent;
                            console.debug(
                              '[AG-UI] Tool call started:',
                              toolEvent.toolCallName
                            );
                            // Optionally show tool call in UI
                            appendToMessage(
                              assistantMessage.id,
                              `\n🔧 Calling: ${toolEvent.toolCallName}...\n`
                            );
                            break;
                          }

                          case AGUIEventType.TOOL_CALL_RESULT: {
                            const resultEvent =
                              event as AGUIToolCallResultEvent;
                            console.debug(
                              '[AG-UI] Tool call result:',
                              resultEvent.toolCallId
                            );
                            break;
                          }

                          case AGUIEventType.RUN_FINISHED: {
                            const finishEvent = event as AGUIRunFinishedEvent;
                            console.debug(
                              '[AG-UI] Run finished:',
                              finishEvent.runId
                            );
                            break;
                          }

                          case AGUIEventType.STATE_DELTA: {
                            // Diagnosis-rail provenance: citations + declared
                            // confidence attach to the answer so an uncited
                            // diagnosis is visibly different from a cited one.
                            const deltaEvent = event as unknown as {
                              kind?: string;
                              confidence?: string;
                              evidence?: DiagnosisEvidence[];
                            };
                            if (deltaEvent.kind === 'diagnosis_provenance') {
                              attachProvenance(
                                assistantMessage.id,
                                Array.isArray(deltaEvent.evidence)
                                  ? deltaEvent.evidence
                                  : [],
                                String(deltaEvent.confidence ?? '')
                              );
                            }
                            break;
                          }

                          case AGUIEventType.RUN_ERROR: {
                            const errorEvent = event as AGUIRunErrorEvent;
                            throw new Error(
                              errorEvent.message || 'Agent run failed'
                            );
                          }

                          case AGUIEventType.RUN_CANCELLED:
                            throw new DOMException(
                              'Message cancelled',
                              'AbortError'
                            );

                          case AGUIEventType.HITL_REQUIRED: {
                            // Human-in-the-loop approval required
                            const hitlEvent = event as AGUIHITLRequiredEvent;
                            console.debug(
                              '[AG-UI] HITL required:',
                              hitlEvent.action
                            );

                            // Parse action details to create user-friendly request
                            const details = hitlEvent.details || {};
                            const hitlRequest: HITLRequest = {
                              id: `hitl_${Date.now()}`,
                              action: hitlEvent.action,
                              title: formatHITLTitle(hitlEvent.action, details),
                              description: formatHITLDescription(
                                hitlEvent.action,
                                details
                              ),
                              details,
                              items: details.items as HITLRequest['items'],
                              totalValue: details.total_value as number,
                              currency: (details.currency as string) || 'USD',
                              riskLevel: determineRiskLevel(
                                hitlEvent.action,
                                details
                              ),
                              timeoutSeconds: hitlEvent.timeout_seconds || 300,
                              createdAt: new Date(),
                              threadId: activeThreadId
                            };

                            setPendingHITL(hitlRequest);
                            appendToMessage(
                              assistantMessage.id,
                              '\n⏳ Waiting for your approval...\n'
                            );
                            break;
                          }

                          case AGUIEventType.HITL_APPROVED: {
                            const approvedEvent =
                              event as AGUIHITLApprovedEvent;
                            console.debug(
                              '[AG-UI] HITL approved:',
                              approvedEvent.action
                            );
                            setPendingHITL(null);
                            setHitlResult({
                              approved: true,
                              action: approvedEvent.action
                            });
                            appendToMessage(
                              assistantMessage.id,
                              `\n✅ Approved: ${approvedEvent.action}\n`
                            );
                            break;
                          }

                          case AGUIEventType.HITL_REJECTED: {
                            const rejectedEvent =
                              event as AGUIHITLRejectedEvent;
                            console.debug(
                              '[AG-UI] HITL rejected:',
                              rejectedEvent.action
                            );
                            setPendingHITL(null);
                            setHitlResult({
                              approved: false,
                              action: rejectedEvent.action
                            });
                            appendToMessage(
                              assistantMessage.id,
                              `\n❌ Rejected: ${rejectedEvent.action}\n`
                            );
                            break;
                          }

                          case AGUIEventType.STEP_STARTED:
                          case AGUIEventType.STEP_FINISHED:
                            // Progress events - could be used for UI feedback
                            break;

                          default:
                            // Handle legacy/fallback formats (OpenAI-style)
                            const legacyContent =
                              (event as any).choices?.[0]?.delta?.content ||
                              (event as any).choices?.[0]?.message?.content ||
                              (event as any).delta?.content ||
                              (event as any).content ||
                              '';
                            if (legacyContent) {
                              appendToMessage(
                                assistantMessage.id,
                                legacyContent
                              );
                            }
                            break;
                        }
                      } catch (eventError) {
                        if (data.startsWith('{')) {
                          throw eventError;
                        }
                        // Non-JSON data line, might be plain text content
                        if (data) {
                          appendToMessage(assistantMessage.id, data);
                        }
                      }
                    } else if (line.startsWith('event: ')) {
                    }
                  }
                }

                // Process any remaining buffer content
                if (buffer.trim() && buffer.startsWith('data: ')) {
                  const data = buffer.slice(6).trim();
                  if (data && data !== '[DONE]') {
                    try {
                      const event = JSON.parse(data);
                      if (
                        event.type === AGUIEventType.TEXT_MESSAGE_CONTENT &&
                        event.delta
                      ) {
                        appendToMessage(assistantMessage.id, event.delta);
                      } else if (event.type === AGUIEventType.RUN_ERROR) {
                        throw new Error(event.message || 'Agent run failed');
                      } else if (event.type === AGUIEventType.RUN_CANCELLED) {
                        throw new DOMException(
                          'Message cancelled',
                          'AbortError'
                        );
                      }
                    } catch (eventError) {
                      if (data.startsWith('{')) {
                        throw eventError;
                      }
                      // Plain text fallback
                      appendToMessage(assistantMessage.id, data);
                    }
                  }
                }
              }

              // Mark streaming as complete and save
              setMessages((prev) => {
                const updated = prev.map((msg) =>
                  msg.id === assistantMessage.id
                    ? { ...msg, isStreaming: false }
                    : msg
                );
                saveCurrentThread(updated, undefined, true);
                return updated;
              });

              // Success - break out of retry loop
              break;
            } catch (streamError: unknown) {
              // Handle stream errors with retry
              const error =
                streamError instanceof Error
                  ? streamError
                  : new Error(String(streamError));

              if (error.name === 'AbortError') {
                // User cancelled - don't retry
                throw error;
              }

              if (
                isRetryableError(error) &&
                retryAttempt < retryConfig.maxAttempts - 1
              ) {
                const delay = calculateRetryDelay(retryAttempt, retryConfig);
                console.warn(
                  `[AI Chat] Stream error: ${error.message}. Retrying in ${delay}ms (attempt ${retryAttempt + 1}/${retryConfig.maxAttempts})`
                );
                resetStreamingMessage(assistantMessage.id);
                await sleep(delay, abortControllerRef.current.signal);
                retryAttempt++;
                continue;
              }

              // Non-retryable or max retries exceeded
              throw error;
            }
          } // end retry while loop
        } else {
          // Non-streaming response - use regular /chat endpoint
          const response = await api.post(chatEndpoint, payload, {
            signal: abortControllerRef.current.signal,
            withCredentials: true,
            headers: {
              'Idempotency-Key': idempotencyKey,
              ...csrfHeaders()
            }
          });

          const data = response.data;
          // Handle OrchestratorAgent response format
          const content =
            data.message ||
            data.content ||
            data.result?.content ||
            data.choices?.[0]?.message?.content ||
            'No response received.';
          setMessages((prev) => {
            const updated = prev.map((msg) =>
              msg.id === assistantMessage.id
                ? { ...msg, content, isStreaming: false }
                : msg
            );
            saveCurrentThread(updated, undefined, true);
            return updated;
          });
        }
      } catch (err: any) {
        if (err.name === 'AbortError') {
          // Request was cancelled
          updateMessage(assistantMessage.id, '(Message cancelled)');
        } else {
          const errorMsg = err.message || 'Failed to get AI response';
          setError(errorMsg);
          updateMessage(
            assistantMessage.id,
            `Sorry, I encountered an error: ${errorMsg}`
          );
        }
      } finally {
        setIsLoading(false);
        abortControllerRef.current = null;
      }
    },
    [
      isLoading,
      activeThreadId,
      mergedConfig,
      messages,
      addMessage,
      updateMessage,
      appendToMessage,
      attachProvenance,
      saveCurrentThread,
      resetStreamingMessage,
      chatEndpoint
    ]
  );

  /**
   * Upload a file and return its metadata
   */
  const uploadFile = useCallback(
    async (file: File): Promise<UploadedFile | null> => {
      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('thread_id', activeThreadId);

        const response = await fetch(`${aiHost}/upload`, {
          method: 'POST',
          body: formData,
          credentials: 'include',
          headers: csrfHeaders()
        });

        if (!response.ok) {
          const errBody = await response.json().catch(() => ({}));
          throw new Error(
            errBody.detail || `Upload failed: ${response.status}`
          );
        }

        return (await response.json()) as UploadedFile;
      } catch (err) {
        console.error('File upload failed:', err);
        setError(err instanceof Error ? err.message : 'File upload failed');
        return null;
      }
    },
    [activeThreadId, aiHost]
  );

  /**
   * Cancel ongoing request
   */
  const cancelRequest = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }, []);

  /**
   * Clear current chat and start a new conversation
   */
  const clearChat = useCallback(() => {
    cancelRequest();
    createNewThread();
  }, [cancelRequest, createNewThread]);

  /**
   * Remove a specific message
   */
  const removeMessage = useCallback((messageId: string) => {
    setMessages((prev) => prev.filter((msg) => msg.id !== messageId));
  }, []);

  /**
   * Handle HITL approval
   */
  const approveHITL = useCallback(
    async (requestId: string, comment?: string) => {
      if (!pendingHITL) return;

      const success = await sendHITLApproval(
        activeThreadId,
        requestId,
        true,
        comment || '',
        aiHost
      );

      if (success) {
        setHitlResult({ approved: true, action: pendingHITL.action });
        setPendingHITL(null);
      }
    },
    [pendingHITL, activeThreadId, aiHost]
  );

  /**
   * Handle HITL rejection
   */
  const rejectHITL = useCallback(
    async (requestId: string, reason: string) => {
      if (!pendingHITL) return;

      const success = await sendHITLApproval(
        activeThreadId,
        requestId,
        false,
        reason,
        aiHost
      );

      if (success) {
        setHitlResult({ approved: false, action: pendingHITL.action });
        setPendingHITL(null);
      }
    },
    [pendingHITL, activeThreadId, aiHost]
  );

  /**
   * Dismiss HITL request without action
   */
  const dismissHITL = useCallback(() => {
    setPendingHITL(null);
  }, []);

  /**
   * Clear HITL result banner
   */
  const clearHITLResult = useCallback(() => {
    setHitlResult(null);
  }, []);

  return {
    // Current thread state
    messages,
    isLoading,
    error,
    activeThreadId,

    // Message actions
    sendMessage,
    cancelRequest,
    addMessage,
    removeMessage,
    uploadFile,

    // Thread management
    threads,
    switchThread,
    createNewThread,
    deleteThread,
    renameThread,
    clearChat,

    // Sync functionality
    isSyncing,
    lastSyncTime,
    syncThreads,

    // HITL (Human-in-the-Loop)
    pendingHITL,
    hitlResult,
    approveHITL,
    rejectHITL,
    dismissHITL,
    clearHITLResult
  };
}
