/** Browser persistence is an expiring navigation index, never a transcript. */
export const CHAT_INDEX_PREFIX = 'aimms.chat.index:v2:';
export const LEGACY_CHAT_KEY = 'ai-chat-threads';
export const CHAT_INDEX_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const MAX_ENTRIES = 200;

export interface ChatIndexRow {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  cachedAt: number;
  deletionPending?: boolean;
}

export interface ChatIndex {
  version: 2;
  context: string | null;
  threads: ChatIndexRow[];
}

interface IndexCandidate {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  isPersisted?: boolean;
  shared?: boolean;
  deletionPending?: boolean;
  cachedAt?: number;
}

function browserStorage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

export function chatIndexKey(
  host: string,
  userId: number | undefined
): string | null {
  if (!Number.isSafeInteger(userId) || !userId || userId < 1) return null;
  try {
    const url = new URL(host);
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    // Host credentials, query strings and fragments never enter a storage key.
    const server = `${url.origin}${url.pathname.replace(/\/+$/, '')}`;
    return `${CHAT_INDEX_PREFIX}${encodeURIComponent(server)}:${userId}`;
  } catch {
    return null;
  }
}

function cleanRow(value: unknown, now: number): ChatIndexRow | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (
    typeof row.id !== 'string' ||
    !row.id ||
    row.id.length > 80 ||
    typeof row.title !== 'string' ||
    row.title.length > 255 ||
    typeof row.createdAt !== 'string' ||
    !Number.isFinite(Date.parse(row.createdAt)) ||
    typeof row.updatedAt !== 'string' ||
    !Number.isFinite(Date.parse(row.updatedAt)) ||
    typeof row.cachedAt !== 'number' ||
    !Number.isFinite(row.cachedAt) ||
    row.cachedAt > now ||
    now - row.cachedAt >= CHAT_INDEX_TTL_MS
  )
    return null;
  // Deliberate field projection: never spread untrusted storage/message objects.
  return {
    id: row.id,
    title: row.title,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    cachedAt: row.cachedAt,
    ...(row.deletionPending === true ? { deletionPending: true } : {})
  };
}

export function readChatIndex(
  key: string | null,
  storage = browserStorage(),
  now = Date.now()
): ChatIndex {
  const empty: ChatIndex = { version: 2, context: null, threads: [] };
  if (!key || !storage) return empty;
  try {
    const data = JSON.parse(storage.getItem(key) ?? 'null');
    if (data?.version !== 2 || !Array.isArray(data.threads)) {
      storage.removeItem(key);
      return empty;
    }
    const ids = new Set<string>();
    const threads: ChatIndexRow[] = [];
    for (const value of data.threads.slice(0, MAX_ENTRIES)) {
      const row = cleanRow(value, now);
      if (row && !ids.has(row.id)) {
        ids.add(row.id);
        threads.push(row);
      }
    }
    const index: ChatIndex = {
      version: 2,
      context:
        typeof data.context === 'string' && /^[a-f0-9]{64}$/.test(data.context)
          ? data.context
          : null,
      threads
    };
    // Expired rows and any unexpected body fields are physically removed.
    if (threads.length) storage.setItem(key, JSON.stringify(index));
    else storage.removeItem(key);
    return index;
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      /* Storage itself may be unavailable. */
    }
    return empty;
  }
}

export function writeChatIndex(
  key: string | null,
  rows: IndexCandidate[],
  context: string | null,
  storage = browserStorage(),
  now = Date.now()
): void {
  if (!key || !storage) return;
  try {
    const threads = rows
      .filter((row) => row.isPersisted && !row.shared)
      .map((row) =>
        cleanRow(
          {
            id: row.id,
            title: row.title.slice(0, 255),
            createdAt: row.createdAt,
            updatedAt: row.updatedAt,
            cachedAt: row.cachedAt,
            deletionPending: row.deletionPending
          },
          now
        )
      )
      .filter((row): row is ChatIndexRow => row !== null)
      .slice(0, MAX_ENTRIES);
    if (threads.length)
      storage.setItem(
        key,
        JSON.stringify({
          version: 2,
          context:
            typeof context === 'string' && /^[a-f0-9]{64}$/.test(context)
              ? context
              : null,
          threads
        })
      );
    else storage.removeItem(key);
  } catch {
    /* Unavailable/quota-limited storage must not prevent chat. */
  }
}

/** Logout/erasure/context changes remove only the versioned chat indices. */
export function clearChatIndices(storage = browserStorage()): void {
  if (!storage) return;
  try {
    const keys = Array.from({ length: storage.length }, (_, i) =>
      storage.key(i)
    );
    for (const key of keys)
      if (key?.startsWith(CHAT_INDEX_PREFIX)) storage.removeItem(key);
  } catch {
    /* Storage may be disabled by the browser. */
  }
}

/** The unscoped legacy archive is never parsed or hydrated into the live chat. */
export function readLegacyChatArchive(
  storage = browserStorage()
): string | null {
  try {
    return storage?.getItem(LEGACY_CHAT_KEY) ?? null;
  } catch {
    return null;
  }
}

export function removeLegacyChatArchive(storage = browserStorage()): boolean {
  try {
    storage?.removeItem(LEGACY_CHAT_KEY);
    return storage?.getItem(LEGACY_CHAT_KEY) == null;
  } catch {
    return false;
  }
}
