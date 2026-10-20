/**
 * VOICE-H06: summary-derived analysis-scope hydration.
 *
 * A thread-detail scope SUMMARY (ActiveScopeSummary) carries mode, version,
 * display label and editability but NO canonical machine ids. Reuse authority
 * must therefore come from the full authorized scope endpoint — never from
 * matching a display label. This module owns the hydration orchestration and
 * its request ordering:
 *
 * - the summary-derived scope is display-only (no `machineIds` key at all);
 * - hydration fetches the FULL scope payload through the caller-supplied
 *   authorized fetch and projects canonical ids;
 * - a failed / revoked / null response keeps the summary display WITHOUT
 *   ids (unconfirmed for reuse) — never label-derived authority;
 * - ordering: a read is fenced by any newer observation (a scope PUT
 *   acknowledgement, a newer summary, a newer read or a fresh authoritative
 *   denial) and by `isCurrent`, which the consuming hook wires to the
 *   captured thread, selection version, session and account key — so an
 *   awaited read can never overwrite a newer scope ack or another thread.
 *
 * Server authorization is unchanged: the module only reads through the same
 * GET endpoint the UI already uses and adds no wire or summary fields.
 */
import type {
  ActiveScopeSummary,
  ThreadScopePayload
} from '@lib/types/AimmsWire.generated';

import type { ActiveThreadScope } from './UseAIChat';

/** Display-only scope derived from a thread-detail summary (no ids, ever). */
export function summaryDerivedScope(
  summary: ActiveScopeSummary,
  shared: boolean
): ActiveThreadScope {
  return {
    mode: summary.mode,
    version: summary.version,
    displayLabel: summary.display_label,
    editable: !shared
  };
}

export interface HydrateScopeInput {
  threadId: string;
  shared: boolean;
  summary: ActiveScopeSummary;
  /** Thread + selection + session + account still current at completion. */
  isCurrent: () => boolean;
  /** The full authorized scope read (the hook's `fetchThreadScope`). */
  fetchScope: () => Promise<ThreadScopePayload | null>;
  /** The hook's canonical projection (`activeScopeFromPayload`). */
  project: (payload: ThreadScopePayload) => ActiveThreadScope;
}

export type ScopeHydrationResult =
  | { status: 'applied'; scope: ActiveThreadScope }
  | { status: 'stale'; scope: null };

export interface ScopeHydrationTracker {
  /** Fence every in-flight hydration read (newer scope observation/write). */
  fence: () => void;
  hydrate: (input: HydrateScopeInput) => Promise<ScopeHydrationResult>;
}

export function createScopeHydrationTracker(): ScopeHydrationTracker {
  // Request ordering, mirroring the thread-focus store: `requestSeq` numbers
  // every hydration read at INVOCATION time; `appliedSeq` is the newest
  // observation applied to the visible scope (summary ack, PUT ack, denial).
  let requestSeq = 0;
  let appliedSeq = 0;
  return {
    fence: () => {
      appliedSeq = ++requestSeq;
    },
    hydrate: async (input) => {
      const seq = ++requestSeq;
      const payload = await input.fetchScope();
      if (!input.isCurrent() || seq < appliedSeq) {
        return { status: 'stale', scope: null };
      }
      appliedSeq = Math.max(appliedSeq, seq);
      if (payload === null) {
        // Fresh authoritative denial (failed / revoked / unavailable): the
        // summary display stands but stays unconfirmed for reuse.
        return {
          status: 'applied',
          scope: summaryDerivedScope(input.summary, input.shared)
        };
      }
      return { status: 'applied', scope: input.project(payload) };
    }
  };
}
