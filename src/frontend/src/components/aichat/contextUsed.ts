/**
 * M2 PR 9 (plan 9.11 / GR-16): the Context used record, client side.
 *
 * ONE record, three envelopes: the legacy SSE `STATE_DELTA {kind:
 * "context_used"}`, the AG-UI `aimms.contextUsed` CUSTOM channel, and the
 * persisted message `context_used` field on thread reload. Every ingest
 * path calls `normalizeContextUsed`, which is an allow-list: it copies ids,
 * counts and closed-enum codes into a fresh object and drops everything
 * else. A text field that reaches the wire by mistake never reaches React
 * state, let alone the screen (GR-16: the disclosure is content-free).
 *
 * Memory rows (`summary`, `preferences_used`, `facts_used`) are projected by
 * the server to the thread owner only; a grantee's record simply lacks them
 * and the normalizer records that absence as `null` rather than inventing
 * a zero.
 */

/** Upper bound on any code-like string we keep (corpus names, slots...). */
const MAX_CODE_LENGTH = 64;
/** Upper bound on the number of entries kept per map (defensive). */
const MAX_ENTRIES = 32;

export interface ContextUsedCorpus {
  corpus: string;
  state: string;
  n: number;
}

export interface ContextUsedTruncation {
  slot: string;
  dropped: number;
}

export interface ContextUsedRecord {
  /** Recent turns replayed into the prompt vs. how many were available. */
  recentTurns: { used: number; available: number } | null;
  /** Summary watermark; null when no summary was used (or not projected). */
  summary: { throughSequence: number } | null;
  /** True when the server projected the summary key at all (owner view). */
  summaryProjected: boolean;
  /** Owner-only memory counts; null when the projection withheld them. */
  preferencesUsed: number | null;
  factsUsed: number | null;
  /** Retrieval ledger: one row per corpus consulted, state + hit count. */
  corpora: ContextUsedCorpus[];
  /** Topology availability code. */
  topology: string | null;
  /** Budget truncation per slot (the only visible cause of absence). */
  truncation: ContextUsedTruncation[];
  /** Retrieval plan codes (closed vocabularies server side). */
  taskIntent: string | null;
  memoryTypes: string[];
  degradeReason: string | null;
  /** Number of retrieval envelopes in the snapshot, when reported. */
  retrievalEnvelopes: number | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asCount(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Math.max(0, Math.trunc(value));
}

function asCode(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  return trimmed.slice(0, MAX_CODE_LENGTH);
}

/**
 * Allow-list normalizer. Returns null only when the input is not an object
 * at all; an object with none of the known keys still normalizes (to an
 * "used nothing" record) so the disclosure can render "no summary".
 */
export function normalizeContextUsed(raw: unknown): ContextUsedRecord | null {
  if (!isRecord(raw)) return null;

  const recentRaw = raw.recent_turns;
  const recentTurns = isRecord(recentRaw)
    ? {
        used: asCount(recentRaw.used) ?? 0,
        available: asCount(recentRaw.available) ?? 0
      }
    : null;

  const summaryProjected = 'summary' in raw;
  let summary: ContextUsedRecord['summary'] = null;
  if (isRecord(raw.summary)) {
    const through = asCount(raw.summary.through_sequence);
    summary = through === null ? null : { throughSequence: through };
  }

  const corpora: ContextUsedCorpus[] = [];
  if (isRecord(raw.corpora)) {
    for (const [corpus, entry] of Object.entries(raw.corpora)) {
      if (corpora.length >= MAX_ENTRIES) break;
      const code = asCode(corpus);
      if (!code || !isRecord(entry)) continue;
      corpora.push({
        corpus: code,
        state: asCode(entry.state) ?? 'unknown',
        n: asCount(entry.n) ?? 0
      });
    }
  }

  const truncation: ContextUsedTruncation[] = [];
  if (isRecord(raw.truncation)) {
    for (const [slot, dropped] of Object.entries(raw.truncation)) {
      if (truncation.length >= MAX_ENTRIES) break;
      const code = asCode(slot);
      const count = asCount(dropped);
      if (!code || count === null || count <= 0) continue;
      truncation.push({ slot: code, dropped: count });
    }
  }

  // `degrade_reason` is a closed enum whose "not degraded" member is the
  // literal string 'none' (DegradeReason.NONE server side) — never null.
  // Like the summary 'none' sentinel above, it normalizes to null so the
  // disclosure paints a Degraded row only for an actual reason code.
  const degradeCode = asCode(raw.degrade_reason);
  const degradeReason =
    degradeCode && degradeCode !== 'none' ? degradeCode : null;

  const plan = isRecord(raw.retrieval_plan) ? raw.retrieval_plan : {};
  const memoryTypes = Array.isArray(plan.memory_types)
    ? plan.memory_types
        .map(asCode)
        .filter((code): code is string => code !== null)
        .slice(0, MAX_ENTRIES)
    : [];

  return {
    recentTurns,
    summary,
    summaryProjected,
    preferencesUsed: asCount(raw.preferences_used),
    factsUsed: asCount(raw.facts_used),
    corpora,
    topology: asCode(raw.topology),
    truncation,
    taskIntent: asCode(plan.task_intent),
    memoryTypes,
    degradeReason,
    retrievalEnvelopes: asCount(raw.retrieval_envelopes)
  };
}
