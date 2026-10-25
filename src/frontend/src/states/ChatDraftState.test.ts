import { beforeEach, describe, expect, it } from 'vitest';

import type { UploadedFile } from '../hooks/UseAIChat';
import {
  type ChatDraftIdentity,
  type ChatDraftScope,
  bindChatDrafts,
  clearChatDrafts,
  retireChatDrafts
} from './ChatDraftState';

const scope: ChatDraftScope = {
  host: 'https://demo.example.com',
  userId: 7,
  sessionGeneration: 3
};

const identity = (threadId: string): ChatDraftIdentity => ({
  ...scope,
  threadId
});

const readyFile = (id: string): UploadedFile => ({
  file_id: id,
  filename: `${id}.txt`,
  size: 11,
  content_type: 'text/plain',
  thread_id: 'thread-a'
});

describe('C06 chat draft store', () => {
  beforeEach(() => {
    clearChatDrafts();
  });

  it('restores each thread own text and ready references without cross-thread leakage', () => {
    bindChatDrafts(identity('thread-a')).writeText('C06 private draft A');
    bindChatDrafts(identity('thread-a')).writeFiles([readyFile('f-a')]);

    const other = bindChatDrafts(identity('thread-b')).read();
    expect(other).toEqual({ text: '', files: [], revision: 0 });

    const own = bindChatDrafts(identity('thread-a')).read();
    expect(own.text).toBe('C06 private draft A');
    expect(own.files.map((file) => file.file_id)).toEqual(['f-a']);
  });

  it('separates drafts by host, user and session generation', () => {
    bindChatDrafts(identity('thread-a')).writeText('base draft');

    expect(bindChatDrafts(identity('thread-a')).read().text).toBe('base draft');
    expect(
      bindChatDrafts({
        ...scope,
        host: 'https://other.example.com',
        threadId: 'thread-a'
      }).read().text
    ).toBe('');
    expect(
      bindChatDrafts({ ...scope, userId: 8, threadId: 'thread-a' }).read().text
    ).toBe('');
    expect(
      bindChatDrafts({
        ...scope,
        sessionGeneration: 4,
        threadId: 'thread-a'
      }).read().text
    ).toBe('');
  });

  it('starts a new thread empty while sibling drafts survive', () => {
    bindChatDrafts(identity('thread-a')).writeText('draft A');
    bindChatDrafts(identity('thread-b')).writeText('draft B');

    expect(bindChatDrafts(identity('thread-c')).read().text).toBe('');
    expect(bindChatDrafts(identity('thread-a')).read().text).toBe('draft A');
    expect(bindChatDrafts(identity('thread-b')).read().text).toBe('draft B');
  });

  it('consumes only the matching submitted revision', () => {
    bindChatDrafts(identity('thread-a')).writeText('submitted draft');
    const submitted = bindChatDrafts(identity('thread-a')).read().revision;

    expect(bindChatDrafts(identity('thread-a')).consume(submitted)).toBe(true);
    expect(bindChatDrafts(identity('thread-a')).read().text).toBe('');
    // A second consume of the same revision must not succeed.
    expect(bindChatDrafts(identity('thread-a')).consume(submitted)).toBe(false);

    // An edit after the snapshot makes the older send revision stale.
    bindChatDrafts(identity('thread-a')).writeText('newer edited draft');
    const newer = bindChatDrafts(identity('thread-a')).read().revision;
    expect(newer).not.toBe(submitted);
    expect(bindChatDrafts(identity('thread-a')).consume(submitted)).toBe(false);
    expect(bindChatDrafts(identity('thread-a')).read().text).toBe(
      'newer edited draft'
    );
    expect(bindChatDrafts(identity('thread-a')).consume(newer)).toBe(true);
    expect(bindChatDrafts(identity('thread-a')).read().text).toBe('');
  });

  it('session reset drops every draft and dead bindings cannot recreate it', () => {
    const stale = bindChatDrafts(identity('thread-a'));
    stale.writeText('C06 previous session draft');
    stale.writeFiles([readyFile('f-a')]);

    clearChatDrafts();

    expect(stale.isCurrent()).toBe(false);
    expect(stale.writeText('late callback')).toBe(-1);
    expect(stale.writeFiles([readyFile('late')])).toBe(-1);
    expect(stale.read()).toEqual({ text: '', files: [], revision: 0 });
    // Even with the same identity and thread, cleared state stays gone.
    expect(bindChatDrafts(identity('thread-a')).read()).toEqual({
      text: '',
      files: [],
      revision: 0
    });
  });

  it('thread deletion retires only that thread drafts and blocks stale bindings', () => {
    const stale = bindChatDrafts(identity('thread-a'));
    stale.writeText('C06 deleted private draft');
    stale.writeFiles([readyFile('f-a')]);
    bindChatDrafts(identity('thread-b')).writeText('sibling draft');

    retireChatDrafts(scope, 'thread-a');

    expect(stale.isCurrent()).toBe(false);
    expect(stale.writeText('late callback')).toBe(-1);
    expect(bindChatDrafts(identity('thread-a')).read()).toEqual({
      text: '',
      files: [],
      revision: 0
    });
    expect(bindChatDrafts(identity('thread-b')).read().text).toBe(
      'sibling draft'
    );
  });

  it('invalid identities never create label-only or thread-only keys', () => {
    const noUser = bindChatDrafts({
      ...scope,
      userId: undefined,
      threadId: 'thread-a'
    });
    expect(noUser.isCurrent()).toBe(false);
    expect(noUser.writeText('draft')).toBe(-1);
    expect(noUser.read()).toEqual({ text: '', files: [], revision: 0 });

    const badHost = bindChatDrafts({
      ...scope,
      host: 'not a url',
      threadId: ''
    });
    expect(badHost.writeText('draft')).toBe(-1);
    expect(badHost.read()).toEqual({ text: '', files: [], revision: 0 });
  });
});
