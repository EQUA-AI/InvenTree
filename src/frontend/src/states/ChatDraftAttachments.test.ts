import { beforeEach, describe, expect, it } from 'vitest';

import type { UploadedFile } from '../hooks/UseAIChat';
import {
  bindChatDrafts,
  clearChatDrafts,
  retireChatDrafts
} from './ChatDraftState';

const scope = {
  host: 'https://demo.example.com',
  userId: 7,
  sessionGeneration: 3
};
const bind = (threadId = 'thread-a') => bindChatDrafts({ ...scope, threadId });
const file = new File(['Mock manual'], 'manual.txt', { type: 'text/plain' });
const uploaded: UploadedFile = {
  file_id: 'ready-a',
  filename: file.name,
  size: file.size,
  content_type: file.type,
  thread_id: 'thread-a'
};

describe('draft-owned attachment attempts', () => {
  beforeEach(clearChatDrafts);

  it('keeps uploads valid while text changes and completes in the original draft', () => {
    const draft = bind();
    draft.writeText('Original question');
    const [id] = draft.queueAttachments([file]);
    const attempt = draft.startUpload(id)!;
    draft.writeText('New question typed while uploading');
    bind('thread-b').writeText('Other question');
    expect(draft.finishUpload(attempt, uploaded)).toBe(true);
    expect(bind().read()).toMatchObject({
      text: 'New question typed while uploading',
      files: [uploaded]
    });
    expect(bind('thread-b').read()).toMatchObject({
      text: 'Other question',
      files: []
    });
    expect(draft.readAttachments()).toEqual([
      { id, status: 'ready', uploaded }
    ]);
  });

  it('never consumes a partially ready selection or an unresolved failure', () => {
    const draft = bind();
    draft.writeText('Both files are required');
    const [first, second] = draft.queueAttachments([file, file]);
    expect(draft.consume(draft.read().revision)).toBe(false);
    draft.finishUpload(draft.startUpload(first)!, uploaded);
    const failed = draft.startUpload(second)!;
    draft.finishUpload(failed, null);
    expect(draft.consume(draft.read().revision)).toBe(false);
    expect(draft.read().text).toBe('Both files are required');
    draft.removeAttachment(second);
    expect(draft.consume(draft.read().revision)).toBe(true);
    expect(draft.read()).toMatchObject({ text: '', files: [] });
    expect(draft.readAttachments()).toEqual([]);
  });

  it('rejects removed attempts even when the same file is selected again', () => {
    const draft = bind();
    const [id] = draft.queueAttachments([file]);
    const stale = draft.startUpload(id)!;
    draft.removeAttachment(id);
    const [newId] = draft.queueAttachments([file]);
    expect(newId).not.toBe(id);
    expect(draft.isUploadCurrent(stale)).toBe(false);
    expect(draft.finishUpload(stale, uploaded)).toBe(false);
    expect(draft.read().files).toEqual([]);
    expect(draft.readAttachments()[0].status).toBe('queued');
  });

  it('retries only a failed attempt and rejects old or duplicate completions', () => {
    const draft = bind();
    const [id] = draft.queueAttachments([file]);
    const first = draft.startUpload(id)!;
    expect(draft.startUpload(id)).toBeNull();
    draft.finishUpload(first, null);
    const retry = draft.startUpload(id)!;
    expect(retry.attempt).toBeGreaterThan(first.attempt);
    expect(draft.finishUpload(first, uploaded)).toBe(false);
    expect(draft.finishUpload(retry, uploaded)).toBe(true);
    expect(draft.finishUpload(retry, uploaded)).toBe(false);
    expect(draft.read().files).toEqual([uploaded]);
  });

  it('retiring a draft rejects queued and in-flight work without clearing siblings', () => {
    const draft = bind();
    const [id, queued] = draft.queueAttachments([file, file]);
    const attempt = draft.startUpload(id)!;
    bind('thread-b').queueAttachments([file]);
    retireChatDrafts(scope, 'thread-a');
    expect(draft.startUpload(queued)).toBeNull();
    expect(draft.finishUpload(attempt, uploaded)).toBe(false);
    expect(bind().readAttachments()).toEqual([]);
    expect(bind('thread-b').readAttachments()).toHaveLength(1);
  });

  it('session reset clears file contents and prevents old attempts recreating state', () => {
    const draft = bind();
    const [id] = draft.queueAttachments([file]);
    const attempt = draft.startUpload(id)!;
    clearChatDrafts();
    expect(draft.readAttachments()).toEqual([]);
    expect(draft.finishUpload(attempt, uploaded)).toBe(false);
    expect(draft.queueAttachments([file])).toEqual([]);
    expect(bind().readAttachments()).toEqual([]);
  });

  it('rejects a ready reference belonging to another thread', () => {
    const draft = bind();
    const [id] = draft.queueAttachments([file]);
    draft.finishUpload(draft.startUpload(id)!, {
      ...uploaded,
      thread_id: 'thread-b'
    });
    expect(draft.read().files).toEqual([]);
    expect(draft.readAttachments()[0].status).toBe('failed');
  });

  it('uses unique stable identities for duplicate filenames and keeps selection order', () => {
    const draft = bind();
    const [a, b] = draft.queueAttachments([file, file]);
    expect(a).not.toBe(b);
    const first = draft.startUpload(a)!;
    draft.finishUpload(draft.startUpload(b)!, {
      ...uploaded,
      file_id: 'ready-b'
    });
    draft.finishUpload(first, uploaded);
    expect(draft.read().files.map((item) => item.file_id)).toEqual([
      'ready-a',
      'ready-b'
    ]);
  });
});
