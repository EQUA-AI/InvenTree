/**
 * Scoped "Ask AIMMS" chat state (Feature #14).
 *
 * A thin governance layer for record-pinned conversations: the server
 * resolves the pinned record into a signed, short-lived context token, the
 * conversation binds to that context, and every tool call is re-authorized
 * server-side. This hook only carries opaque tokens and typed results — it
 * never asserts identity or scope from the browser.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useRef, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import { api } from '../App';

export interface ScopedChatContext {
  context_type: string;
  object_id: string;
  display_label: string;
  capabilities: string[];
  source_revision: string;
  as_of: string;
  snapshot: Record<string, unknown>;
  token: string;
  expires_in_s: number;
  tools: string[];
}

export interface ScopedConversation {
  id: string;
  context_type: string;
  object_id: string;
  title: string;
  status: string;
  ai_thread_id: string;
  last_context_revision: string;
  context_state?: 'authorized' | 'revoked';
  created_at?: string;
  updated_at?: string;
}

export interface ScopedToolEnvelope {
  tool: string;
  tool_version: string;
  authorized: boolean;
  error: string | null;
  as_of: string;
  source_revision?: string;
  result: Record<string, any> | null;
  citation_id: number | null;
  invocation_id?: number;
}

export interface ScopedCitation {
  id: number;
  turn_key: string;
  source_type: string;
  available: boolean;
  as_of: string;
  source_id?: string;
  source_revision?: string;
  locator?: Record<string, any>;
  excerpt_hash?: string;
}

export interface ScopedToolTraceRow {
  id: number;
  turn_key: string;
  tool: string;
  tool_version: string;
  arguments: Record<string, any>;
  authorization_result: 'allowed' | 'denied';
  duration_ms: number | null;
  created_at: string;
}

export interface ScopedChatTurn {
  turnKey: string;
  tool: string;
  envelope: ScopedToolEnvelope | null;
  error: string | null;
}

interface UseScopedChatOptions {
  contextType: string;
  objectId: string | number;
  enabled?: boolean;
}

function stableKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Resolve, pin, and converse about exactly one authorized record.
 */
export function useScopedChat({
  contextType,
  objectId,
  enabled = true
}: UseScopedChatOptions) {
  const queryClient = useQueryClient();
  const recordId = String(objectId);

  const [conversation, setConversation] = useState<ScopedConversation | null>(
    null
  );
  const [turns, setTurns] = useState<ScopedChatTurn[]>([]);
  const [busy, setBusy] = useState(false);

  // The token is opaque and short-lived; hold the freshest one only.
  const tokenRef = useRef<string | null>(null);

  const contextQuery = useQuery<ScopedChatContext>({
    queryKey: ['chat-context', contextType, recordId],
    enabled,
    retry: false,
    // Silent re-resolve well before the server-side TTL (default 15 min).
    refetchInterval: 5 * 60 * 1000,
    queryFn: async () => {
      const response = await api.post(
        apiUrl(ApiEndpoints.aichat_context_resolve),
        { context_type: contextType, object_id: recordId }
      );
      tokenRef.current = response.data.token;
      return response.data;
    }
  });

  const conversationsQuery = useQuery<ScopedConversation[]>({
    queryKey: ['scoped-conversations', contextType, recordId],
    enabled: enabled && !!contextQuery.data,
    retry: false,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.aichat_conversation_list),
        { params: { context_type: contextType, object_id: recordId } }
      );
      return response.data?.results ?? [];
    }
  });

  const openConversation =
    useCallback(async (): Promise<ScopedConversation> => {
      if (conversation && conversation.status === 'active') {
        return conversation;
      }
      const existing = (conversationsQuery.data ?? []).find(
        (row) => row.status === 'active'
      );
      if (existing) {
        setConversation(existing);
        return existing;
      }
      const token = tokenRef.current;
      if (!token) {
        throw new Error('CONTEXT_TOKEN_INVALID');
      }
      const response = await api.post(
        apiUrl(ApiEndpoints.aichat_conversation_list),
        { token }
      );
      const created: ScopedConversation = response.data;
      setConversation(created);
      await queryClient.invalidateQueries({
        queryKey: ['scoped-conversations', contextType, recordId]
      });
      return created;
    }, [
      conversation,
      conversationsQuery.data,
      queryClient,
      contextType,
      recordId
    ]);

  const invokeTool = useCallback(
    async (
      tool: string,
      toolArguments?: Record<string, unknown>
    ): Promise<ScopedChatTurn> => {
      const turnKey = stableKey();
      setBusy(true);
      try {
        const active = await openConversation();
        const response = await api.post(
          apiUrl(ApiEndpoints.aichat_conversation_tool_invoke, active.id),
          {
            token: tokenRef.current,
            tool,
            arguments: toolArguments ?? {},
            turn_key: turnKey
          }
        );
        const turn: ScopedChatTurn = {
          turnKey,
          tool,
          envelope: response.data,
          error: null
        };
        setTurns((current) => [...current, turn]);
        await queryClient.invalidateQueries({
          queryKey: ['chat-citations', active.id]
        });
        return turn;
      } catch (error: any) {
        const code: string =
          error?.response?.data?.error ?? 'CHAT_REQUEST_FAILED';
        // An expired token re-resolves silently while still authorized.
        if (
          code === 'CONTEXT_TOKEN_EXPIRED' ||
          code === 'CONTEXT_TOKEN_INVALID'
        ) {
          await contextQuery.refetch();
        }
        const turn: ScopedChatTurn = {
          turnKey,
          tool,
          envelope: null,
          error: code
        };
        setTurns((current) => [...current, turn]);
        return turn;
      } finally {
        setBusy(false);
      }
    },
    [openConversation, queryClient, contextQuery]
  );

  const citationsQuery = useQuery<ScopedCitation[]>({
    queryKey: ['chat-citations', conversation?.id ?? 'none'],
    enabled: enabled && !!conversation,
    retry: false,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.aichat_conversation_citations, conversation?.id)
      );
      return response.data?.results ?? [];
    }
  });

  const toolTraceQuery = useQuery<ScopedToolTraceRow[]>({
    queryKey: ['chat-tools', conversation?.id ?? 'none'],
    enabled: enabled && !!conversation,
    retry: false,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.aichat_conversation_tools, conversation?.id)
      );
      return response.data?.results ?? [];
    }
  });

  const capabilities = useMemo(
    () => contextQuery.data?.capabilities ?? [],
    [contextQuery.data]
  );

  const unavailable =
    contextQuery.isError ||
    (!contextQuery.isLoading && !contextQuery.data && enabled);

  return {
    context: contextQuery.data ?? null,
    contextQuery,
    unavailable,
    conversation,
    conversations: conversationsQuery.data ?? [],
    openConversation,
    invokeTool,
    turns,
    busy,
    capabilities,
    citations: citationsQuery.data ?? [],
    citationsQuery,
    toolTrace: toolTraceQuery.data ?? [],
    toolTraceQuery
  };
}
