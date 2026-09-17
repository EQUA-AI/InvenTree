import { describe, expect, it, vi } from 'vitest';
import {
  CHAT_INDEX_TTL_MS,
  LEGACY_CHAT_KEY,
  chatIndexKey,
  chatInvalidationKey,
  clearChatIndices,
  publishChatInvalidation,
  readChatIndex,
  readLegacyChatArchive,
  removeLegacyChatArchive,
  writeChatIndex
} from './chatThreadCache';

class MemoryStorage implements Storage {
  private rows = new Map<string, string>();
  get length() {
    return this.rows.size;
  }
  clear() {
    this.rows.clear();
  }
  getItem(key: string) {
    return this.rows.get(key) ?? null;
  }
  key(index: number) {
    return [...this.rows.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.rows.delete(key);
  }
  setItem(key: string, value: string) {
    this.rows.set(key, value);
  }
}

const now = Date.parse('2026-09-17T18:00:00Z');
const key = chatIndexKey('https://example.test', 1)!;
const context = 'a'.repeat(64);
const row = {
  id: 'thread-fixture',
  title: 'Pump notes',
  isPersisted: true,
  createdAt: '2026-09-16T18:00:00Z',
  updatedAt: '2026-09-17T18:00:00Z',
  cachedAt: now
};

describe('chat navigation index', () => {
  it('sends repeated content-free invalidations without deleting the current index', () => {
    const storage = new MemoryStorage();
    writeChatIndex(
      key,
      [{ ...row, deletionPending: true }],
      context,
      storage,
      now
    );
    const before = storage.getItem(key);
    const writes = vi.spyOn(storage, 'setItem');
    publishChatInvalidation(key, storage);
    publishChatInvalidation(key, storage);
    expect(writes.mock.calls).toEqual([
      [chatInvalidationKey(key), 'changed'],
      [chatInvalidationKey(key), 'changed']
    ]);
    expect(storage.getItem(chatInvalidationKey(key)!)).toBeNull();
    expect(storage.getItem(key)).toBe(before);
    expect(
      chatInvalidationKey(chatIndexKey('https://example.test', 2))
    ).not.toBe(chatInvalidationKey(key));
    expect(chatInvalidationKey(null)).toBeNull();
  });
  it('stores only explicit metadata and excludes shared and unsaved conversations', () => {
    const storage = new MemoryStorage();
    const rich = {
      ...row,
      messages: [{ content: 'PRIVATE BODY' }],
      summary: 'PRIVATE SUMMARY',
      memory: { text: 'PRIVATE FACT' },
      citations: ['PRIVATE CITATION']
    };
    writeChatIndex(
      key,
      [
        rich,
        { ...rich, id: 'shared', shared: true },
        { ...rich, id: 'local', isPersisted: false }
      ],
      context,
      storage,
      now
    );
    const raw = storage.getItem(key)!;
    expect(raw).not.toContain('PRIVATE');
    expect(JSON.parse(raw).threads).toEqual([
      {
        id: row.id,
        title: row.title,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
        cachedAt: now
      }
    ]);
  });

  it('namespaces by server and user and omits URL credentials and query values', () => {
    expect(chatIndexKey('https://other.test', 1)).not.toBe(key);
    expect(chatIndexKey('https://example.test', 2)).not.toBe(key);
    expect(
      chatIndexKey('https://name:value@example.test/?token=private#fragment', 1)
    ).toBe(key);
    expect(chatIndexKey('https://example.test', undefined)).toBeNull();
    expect(chatIndexKey('not a URL', 1)).toBeNull();
  });

  it('expires rows at 30 days without extending TTL during unrelated writes', () => {
    const storage = new MemoryStorage();
    writeChatIndex(key, [row], context, storage, now);
    const old = readChatIndex(key, storage, now + CHAT_INDEX_TTL_MS - 1);
    expect(old.threads).toHaveLength(1);
    writeChatIndex(
      key,
      old.threads.map((entry) => ({ ...entry, isPersisted: true })),
      context,
      storage,
      now + CHAT_INDEX_TTL_MS
    );
    expect(storage.getItem(key)).toBeNull();
  });

  it('scrubs poisoned index fields, duplicate ids and invalid timestamps on read', () => {
    const storage = new MemoryStorage();
    storage.setItem(
      key,
      JSON.stringify({
        version: 2,
        context,
        messages: 'PRIVATE',
        threads: [
          { ...row, messages: ['PRIVATE'], summary: 'PRIVATE' },
          row,
          { ...row, id: 'future', cachedAt: now + 1 },
          { ...row, id: 'bad-date', createdAt: 'invalid' }
        ]
      })
    );
    expect(readChatIndex(key, storage, now).threads).toHaveLength(1);
    expect(storage.getItem(key)).not.toContain('PRIVATE');
  });

  it('removes malformed indices and tolerates disabled browser storage', () => {
    const storage = new MemoryStorage();
    storage.setItem(key, '{invalid');
    expect(readChatIndex(key, storage, now).threads).toEqual([]);
    expect(storage.getItem(key)).toBeNull();
    const unavailable = new Proxy(storage, {
      get() {
        throw new Error('disabled');
      }
    });
    expect(() =>
      writeChatIndex(key, [row], context, unavailable, now)
    ).not.toThrow();
    expect(readChatIndex(key, unavailable, now).threads).toEqual([]);
    expect(() => clearChatIndices(unavailable)).not.toThrow();
  });

  it('clears indices across users without deleting unresolved legacy history or other preferences', () => {
    const storage = new MemoryStorage();
    const legacy = '[{"messages":[{"content":"only remaining copy"}]}]';
    storage.setItem(LEGACY_CHAT_KEY, legacy);
    storage.setItem('theme', 'dark');
    writeChatIndex(key, [row], context, storage, now);
    writeChatIndex(
      chatIndexKey('https://other.test', 2),
      [row],
      context,
      storage,
      now
    );
    clearChatIndices(storage);
    expect(storage.length).toBe(2);
    expect(readLegacyChatArchive(storage)).toBe(legacy);
    expect(removeLegacyChatArchive(storage)).toBe(true);
    expect(storage.getItem('theme')).toBe('dark');
  });
});
