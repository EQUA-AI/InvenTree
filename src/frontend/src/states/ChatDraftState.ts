/**
 * C06: RAM-only chat composer drafts.
 *
 * Text and ready `UploadedFile` references live per canonical
 * host/user/sessionGeneration/thread identity — the same canonical key the
 * thread index uses (`chatIndexKey`), never a label-only or thread-only key.
 * Nothing is persisted: no localStorage/sessionStorage/IndexedDB, no storage
 * middleware, no logging, no refresh-survival promise and no new dependencies.
 * A reload or an identity/session change starts empty by design.
 */
import { chatIndexKey } from '../functions/chatThreadCache';
import type { UploadedFile } from '../hooks/UseAIChat';

/** Host + user + session generation: the private draft namespace. */
export interface ChatDraftScope {
  host: string;
  userId: number | undefined;
  sessionGeneration: number;
}

/** A draft namespace plus the canonical thread id the draft belongs to. */
export interface ChatDraftIdentity extends ChatDraftScope {
  threadId: string;
}

/** Immutable draft contents plus the revision they were written under. */
export interface ChatDraftSnapshot {
  text: string;
  files: UploadedFile[];
  revision: number;
}

/** File contents stay in RAM only, including failed files retained for Retry. */
export type ChatDraftAttachment =
  | { id: string; status: 'ready'; uploaded: UploadedFile }
  | {
      id: string;
      status: 'queued' | 'uploading' | 'failed';
      file: File;
      attempt: number;
    };

export interface ChatUploadAttempt {
  id: string;
  attempt: number;
  file: File;
}

/** One thread's draft handle inside a captured scope. */
export interface ChatDraftBinding {
  readonly identity: ChatDraftIdentity;
  /** False once a reset/retire invalidated this binding — writes become no-ops. */
  isCurrent(): boolean;
  read(): ChatDraftSnapshot;
  writeText(text: string): number;
  writeFiles(files: UploadedFile[]): number;
  readAttachments(): ChatDraftAttachment[];
  queueAttachments(files: File[]): string[];
  startUpload(id: string): ChatUploadAttempt | null;
  isUploadCurrent(attempt: ChatUploadAttempt): boolean;
  finishUpload(
    attempt: ChatUploadAttempt,
    uploaded: UploadedFile | null
  ): boolean;
  removeAttachment(id: string): void;
  /** Drop the draft only while `revision` is still the submitted one. */
  consume(revision: number): boolean;
  /** Explicit clear/retire: drop this thread's draft and kill older bindings. */
  clear(): void;
}

interface DraftEntry {
  text: string;
  attachments: ChatDraftAttachment[];
  revision: number;
}

function draftKey(identity: ChatDraftIdentity): string | null {
  const indexKey = chatIndexKey(identity.host, identity.userId);
  if (indexKey === null || !identity.threadId) return null;
  return `${indexKey}|gen:${identity.sessionGeneration}|thread:${identity.threadId}`;
}

const drafts = new Map<string, DraftEntry>();
// Per-key retire stamps: deleting/clearing a thread invalidates the bindings
// captured before it (late callbacks cannot recreate retired state) without
// blocking a genuinely new binding for the same thread later.
const retireStamps = new Map<string, number>();
// Globally monotonic revisions: a consumed revision is never reusable, so a
// stale send can never consume a newer draft.
let revisionCounter = 0;
// Bumped by clearChatDrafts(): every older binding dies at once.
let epoch = 0;
let attachmentCounter = 0;
const listeners = new Set<() => void>();

/** React reads the stable numeric draft revision via useSyncExternalStore. */
export function subscribeChatDrafts(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notifyDrafts(): void {
  for (const listener of listeners) listener();
}

function readyAttachments(files: UploadedFile[]): ChatDraftAttachment[] {
  return files.map((uploaded) => ({
    id: `chat-attachment-${++attachmentCounter}`,
    status: 'ready',
    uploaded
  }));
}

function retireStampOf(key: string): number {
  return retireStamps.get(key) ?? 0;
}

function retireKey(key: string): void {
  drafts.delete(key);
  retireStamps.set(key, retireStampOf(key) + 1);
  notifyDrafts();
}

export function bindChatDrafts(identity: ChatDraftIdentity): ChatDraftBinding {
  const key = draftKey(identity);
  const boundEpoch = epoch;
  const boundRetireStamp = key === null ? 0 : retireStampOf(key);
  const alive = (): boolean =>
    key !== null &&
    boundEpoch === epoch &&
    retireStampOf(key) === boundRetireStamp;

  const entry = (): DraftEntry | undefined =>
    key !== null && alive() ? drafts.get(key) : undefined;
  const store = (text: string, attachments: ChatDraftAttachment[]): number => {
    if (key === null || !alive()) return -1;
    revisionCounter += 1;
    drafts.set(key, { text, attachments, revision: revisionCounter });
    notifyDrafts();
    return revisionCounter;
  };
  const currentUpload = (attempt: ChatUploadAttempt): boolean =>
    entry()?.attachments.some(
      (item) =>
        item.id === attempt.id &&
        item.status === 'uploading' &&
        item.attempt === attempt.attempt
    ) ?? false;

  return {
    identity,
    isCurrent: alive,
    read: () => {
      const current = entry();
      return current
        ? {
            text: current.text,
            files: current.attachments.flatMap((item) =>
              item.status === 'ready' ? [item.uploaded] : []
            ),
            revision: current.revision
          }
        : { text: '', files: [], revision: 0 };
    },
    writeText: (text) => {
      return store(text, entry()?.attachments ?? []);
    },
    writeFiles: (files) => {
      return store(entry()?.text ?? '', readyAttachments(files));
    },
    readAttachments: () =>
      (entry()?.attachments ?? []).map((item) => ({ ...item })),
    queueAttachments: (files) => {
      if (!alive() || files.length === 0) return [];
      const queued = files.map(
        (file): ChatDraftAttachment => ({
          id: `chat-attachment-${++attachmentCounter}`,
          file,
          status: 'queued',
          attempt: 0
        })
      );
      store(entry()?.text ?? '', [...(entry()?.attachments ?? []), ...queued]);
      return queued.map((item) => item.id);
    },
    startUpload: (id) => {
      const current = entry();
      const item = current?.attachments.find((item) => item.id === id);
      if (
        !current ||
        !item ||
        item.status === 'ready' ||
        item.status === 'uploading'
      )
        return null;
      const attempt = { id, file: item.file, attempt: item.attempt + 1 };
      store(
        current.text,
        current.attachments.map((item) =>
          item.id === id ? { ...attempt, status: 'uploading' } : item
        )
      );
      return attempt;
    },
    isUploadCurrent: currentUpload,
    finishUpload: (attempt, uploaded) => {
      const current = entry();
      if (!current || !currentUpload(attempt)) return false;
      // A server response for another thread must never become a ready ref.
      const valid = uploaded?.thread_id === identity.threadId ? uploaded : null;
      store(
        current.text,
        current.attachments.map((item) =>
          item.id !== attempt.id
            ? item
            : valid
              ? { id: item.id, status: 'ready', uploaded: valid }
              : { ...attempt, status: 'failed' }
        )
      );
      return true;
    },
    removeAttachment: (id) => {
      const current = entry();
      if (!current || !current.attachments.some((item) => item.id === id))
        return;
      store(
        current.text,
        current.attachments.filter((item) => item.id !== id)
      );
    },
    consume: (revision) => {
      if (key === null || !alive()) return false;
      const entry = drafts.get(key);
      if (
        entry === undefined ||
        revision <= 0 ||
        entry.revision !== revision ||
        entry.attachments.some((item) => item.status !== 'ready')
      ) {
        return false;
      }
      store('', []);
      return true;
    },
    clear: () => {
      if (key === null || !alive()) return;
      retireKey(key);
    }
  };
}

/**
 * Session reset (resetSession): synchronously drop every private draft and
 * reference AND invalidate all older bindings, so a late callback can never
 * recreate cleared state — even with the same identity and thread.
 */
export function clearChatDrafts(): void {
  drafts.clear();
  retireStamps.clear();
  epoch += 1;
  notifyDrafts();
}

/**
 * Thread retirement (applyThreadDeletion): drop exactly this thread's draft
 * and ready references — single, purge-incomplete and bulk receipts alike —
 * while sibling drafts stay untouched.
 */
export function retireChatDrafts(
  scope: ChatDraftScope,
  threadId: string
): void {
  const key = draftKey({ ...scope, threadId });
  if (key !== null) retireKey(key);
}
