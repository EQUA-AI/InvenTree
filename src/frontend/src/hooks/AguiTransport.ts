/**
 * S50: the official @ag-ui/client transport for the chat drawer.
 *
 * One turn = one `runAguiTurn` call against the S49 `/agui` adapter. The
 * idempotency retry loop stays OUTSIDE this module (UseAIChat owns it, key
 * minted once per logical turn); this module owns the SDK wiring:
 * session-cookie credentials, CSRF, the Idempotency-Key mirror header, the
 * abort signal, and the mapping from spec + `aimms.*` CUSTOM events onto
 * narrow UI callbacks. It never imports UseAIChat (no cycles).
 */

import { HttpAgent } from '@ag-ui/client';
import type { AgentSubscriber } from '@ag-ui/client';
import type { AimmsCustomChannel } from '@lib/types/AimmsWire.generated';

/** Carries the backend failure class across the throw/catch plumbing. */
export class AguiRunError extends Error {
  failureClass?: string;
  localizedMessage?: string;
  /** S12: typed limiter Retry-After (seconds). */
  retryAfter?: number;

  constructor(
    message: string,
    failureClass?: string,
    localizedMessage?: string,
    retryAfter?: number
  ) {
    super(message);
    this.name = 'AguiRunError';
    this.failureClass = failureClass;
    this.localizedMessage = localizedMessage;
    this.retryAfter = retryAfter;
  }
}

/**
 * The /agui endpoint is absent (flag off / old backend): the caller flips
 * this session to the legacy wire and resends.
 */
export class AguiUnavailableError extends Error {
  constructor() {
    super('/agui unavailable');
    this.name = 'AguiUnavailableError';
  }
}

/** Narrow UI callbacks — UseAIChat maps these onto its existing setters. */
export interface AguiTurnCallbacks {
  onTextStart(messageId: string): void;
  onTextDelta(messageId: string, delta: string): void;
  /** MESSAGES_SNAPSHOT: wholesale bubble replace with the final text. */
  onMessagesSnapshot(content: string): void;
  onToolStart(entry: { id: string; name: string }): void;
  onToolEnd(id: string): void;
  onToolStatus(value: {
    toolCallId?: string;
    toolCallName?: string;
    status?: string;
    durationMs?: number;
  }): void;
  onQuestion(value: Record<string, unknown>): void;
  onEntities(entities: unknown[]): void;
  onMediaEvidence(entries: unknown[]): void;
  onProvenance(evidence: unknown[], confidence: string): void;
  /** S11: the consolidated evidence attachment (normalized hook-side). */
  onEvidenceAnalysis(value: unknown): void;
  /** S11: one content-free buffered-execution stage (closed enum). */
  onAnalysisProgress(stage: unknown): void;
  onProposalsRefresh(): void;
}

export interface AguiTurnOptions {
  url: string;
  threadId?: string;
  message: string;
  fileIds?: string[];
  idempotencyKey: string;
  /** S1: scope staleness detector; omitted when no version was observed. */
  expectedScopeVersion?: number;
  signal: AbortSignal;
  csrfToken?: string;
  callbacks: AguiTurnCallbacks;
}

/**
 * HttpAgent with the AIMMS transport concerns: session cookies, CSRF, the
 * Idempotency-Key mirror header (the body carries it in forwardedProps —
 * same belt-and-braces the legacy wire uses), and the caller's AbortSignal.
 */
class AimmsHttpAgent extends HttpAgent {
  private readonly extraHeaders: Record<string, string>;
  private readonly signal: AbortSignal;

  constructor(config: {
    url: string;
    threadId?: string;
    headers: Record<string, string>;
    signal: AbortSignal;
  }) {
    super({
      url: config.url,
      threadId: config.threadId,
      headers: config.headers
    });
    this.extraHeaders = config.headers;
    this.signal = config.signal;
  }

  protected override requestInit(
    input: Parameters<HttpAgent['requestInit']>[0]
  ): RequestInit {
    const base = super.requestInit(input);
    return {
      ...base,
      headers: {
        ...(base.headers as Record<string, string>),
        ...this.extraHeaders
      },
      credentials: 'include',
      signal: this.signal
    };
  }
}

function dispatchCustom(
  name: string,
  value: unknown,
  callbacks: AguiTurnCallbacks,
  stash: { failureClass?: string; localizedMessage?: string }
): void {
  const channel = name as AimmsCustomChannel;
  switch (channel) {
    case 'aimms.toolStatus': {
      const status = (value ?? {}) as {
        toolCallId?: string;
        toolCallName?: string;
        status?: string;
        durationMs?: number;
      };
      callbacks.onToolStatus(status);
      break;
    }
    case 'aimms.question':
      callbacks.onQuestion((value ?? {}) as Record<string, unknown>);
      break;
    case 'aimms.entities': {
      const entities = (value as { entities?: unknown[] })?.entities;
      callbacks.onEntities(Array.isArray(entities) ? entities : []);
      break;
    }
    case 'aimms.mediaEvidence': {
      const entries = (value as { media_evidence?: unknown[] })?.media_evidence;
      callbacks.onMediaEvidence(Array.isArray(entries) ? entries : []);
      break;
    }
    case 'aimms.provenance': {
      const detail = (value ?? {}) as {
        confidence?: unknown;
        evidence?: unknown[];
      };
      callbacks.onProvenance(
        Array.isArray(detail.evidence) ? detail.evidence : [],
        String(detail.confidence ?? '')
      );
      break;
    }
    case 'aimms.evidenceAnalysis':
      // Raw pass-through: normalization happens hook-side, exactly like
      // onEntities filtering — one normalizer for every envelope.
      callbacks.onEvidenceAnalysis(value ?? {});
      break;
    case 'aimms.analysisProgress': {
      const detail = (value ?? {}) as { stage?: unknown };
      callbacks.onAnalysisProgress(detail.stage);
      break;
    }
    case 'aimms.proposalsRefresh':
      callbacks.onProposalsRefresh();
      break;
    case 'aimms.error': {
      // Stashed for the spec RUN_ERROR that follows (which terminates the
      // run); the CUSTOM carries what zod would strip from RUN_ERROR.
      const detail = (value ?? {}) as {
        failureClass?: string;
        localizedMessage?: string;
      };
      stash.failureClass = detail.failureClass ?? undefined;
      stash.localizedMessage = detail.localizedMessage ?? undefined;
      break;
    }
    case 'aimms.stateDelta':
    case 'aimms.hitl':
    case 'aimms.custom':
      // Known channels with no UI consumer yet — deliberate no-ops.
      break;
    default:
      // Unknown channel from a newer backend: drop, never render.
      break;
  }
}

/**
 * Run one turn over /agui. Resolves when the run finishes; throws
 * AguiRunError (typed failure), DOMException AbortError (cancel), or
 * AguiUnavailableError (endpoint absent → caller falls back to legacy).
 */
export async function runAguiTurn(options: AguiTurnOptions): Promise<void> {
  const headers: Record<string, string> = {
    'Idempotency-Key': options.idempotencyKey
  };
  if (options.csrfToken) {
    headers['X-CSRFToken'] = options.csrfToken;
  }

  const agent = new AimmsHttpAgent({
    url: options.url,
    threadId: options.threadId,
    headers,
    signal: options.signal
  });
  agent.setMessages([
    {
      id: `user_${options.idempotencyKey}`,
      role: 'user',
      content: options.message
    }
  ]);

  const stash: { failureClass?: string; localizedMessage?: string } = {};
  let fatal: AguiRunError | DOMException | null = null;

  const subscriber: AgentSubscriber = {
    onTextMessageStartEvent: ({ event }) => {
      options.callbacks.onTextStart(event.messageId);
    },
    onTextMessageContentEvent: ({ event }) => {
      options.callbacks.onTextDelta(event.messageId, event.delta);
    },
    onMessagesSnapshotEvent: ({ event }) => {
      const assistant = [...(event.messages ?? [])]
        .reverse()
        .find((entry) => entry.role === 'assistant');
      if (assistant && typeof assistant.content === 'string') {
        options.callbacks.onMessagesSnapshot(assistant.content);
      }
    },
    onToolCallStartEvent: ({ event }) => {
      options.callbacks.onToolStart({
        id: event.toolCallId,
        name: event.toolCallName
      });
    },
    onToolCallEndEvent: ({ event }) => {
      options.callbacks.onToolEnd(event.toolCallId);
    },
    onCustomEvent: ({ event }) => {
      dispatchCustom(event.name, event.value, options.callbacks, stash);
    },
    onRunErrorEvent: ({ event }) => {
      // Two cancel shapes: the server's RUN_CANCELLED translation
      // (code=run_cancelled) and the SDK's SYNTHETIC local RUN_ERROR
      // (code='abort') minted when the caller's AbortSignal killed the
      // fetch — 0.0.57 converts the AbortError instead of rejecting.
      if (
        event.code === 'run_cancelled' ||
        event.code === 'abort' ||
        options.signal.aborted
      ) {
        fatal = new DOMException('Message cancelled', 'AbortError');
        return;
      }
      fatal = new AguiRunError(
        event.message || 'Agent run failed',
        stash.failureClass,
        stash.localizedMessage
      );
    }
  };

  try {
    await agent.runAgent(
      {
        forwardedProps: {
          idempotencyKey: options.idempotencyKey,
          fileIds: options.fileIds,
          expectedScopeVersion: options.expectedScopeVersion
        }
      },
      subscriber
    );
  } catch (error: unknown) {
    if (fatal) {
      throw fatal;
    }
    if (options.signal.aborted) {
      throw new DOMException('Message cancelled', 'AbortError');
    }
    const status =
      (error as { response?: { status?: number }; status?: number }) ?? {};
    const httpStatus = status.response?.status ?? status.status;
    if (httpStatus === 404 || httpStatus === 405) {
      throw new AguiUnavailableError();
    }
    const message = error instanceof Error ? error.message : String(error);
    if (
      /\b(404|405)\b/.test(message) &&
      /not found|method not allowed|status/i.test(message)
    ) {
      throw new AguiUnavailableError();
    }
    // Retry parity with the legacy wire (it retried Response 429/5xx by
    // status). The SDK rejects with Error('HTTP <status>: <body>') — map
    // retryable statuses to a message isRetryableError recognizes, and
    // NEVER let the raw response body reach error copy.
    const resolvedStatus =
      httpStatus ?? Number(/^HTTP (\d{3}):/.exec(message)?.[1] ?? Number.NaN);
    // S1: the scope-version conflict is a typed, non-retryable outcome —
    // carry it as a failureClass so the hook's conflict UX can catch it.
    if (resolvedStatus === 409 && /scope_version_conflict/.test(message)) {
      throw new AguiRunError(
        'The conversation scope changed.',
        'scope_version_conflict'
      );
    }
    // S12: extract the typed limiter code from the SDK's rejection message
    // BEFORE flattening — a spent budget or an enforce-mode store outage
    // must surface as its typed class, never as a generic retryable. The
    // raw body still never reaches error copy (constant messages only).
    if (resolvedStatus === 429 && /token_budget_exhausted/.test(message)) {
      throw new AguiRunError(
        'Daily AI usage limit reached.',
        'token_budget_exhausted',
        undefined,
        Number(/"retry_after"\s*:\s*(\d+)/.exec(message)?.[1] ?? Number.NaN) ||
          undefined
      );
    }
    if (resolvedStatus === 503 && /quota_store_unavailable/.test(message)) {
      throw new AguiRunError(
        'AI usage controls are unavailable.',
        'quota_store_unavailable'
      );
    }
    if (resolvedStatus === 503 && /ai_capacity_busy/.test(message)) {
      throw new AguiRunError(
        'The AI service is at capacity.',
        'ai_capacity_busy',
        undefined,
        Number(/"retry_after"\s*:\s*(\d+)/.exec(message)?.[1] ?? Number.NaN) ||
          undefined
      );
    }
    if ([429, 500, 502, 503, 504].includes(resolvedStatus)) {
      throw new Error(`network error: HTTP ${resolvedStatus} (retryable)`);
    }
    if (!Number.isNaN(resolvedStatus)) {
      throw new Error(`HTTP error ${resolvedStatus}`);
    }
    throw error instanceof Error ? error : new Error(message);
  }
  if (fatal) {
    // The SDK can resolve after a RUN_ERROR when the stream closed cleanly;
    // the recorded fatal still terminates the logical turn.
    throw fatal;
  }
}
