/** Deferred parser/transport qualification; no real network or filesystem. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_TRANSCRIPT_BYTES,
  fetchTranscriptExport
} from './transcriptExport';

const artifact = () => ({
  schema_version: 1,
  records: [
    {
      type: 'manifest',
      schema_version: 1,
      scope: 'owned_thread_transcripts',
      owner_id: '7',
      consistency: 'live_read_with_creation_cutoff',
      includes: ['messages'],
      excludes: ['shared_threads']
    },
    {
      type: 'thread',
      thread_id: 'thread_owned',
      title: 'Fixture',
      summary: '',
      message_watermark: 1
    },
    {
      type: 'message',
      thread_id: 'thread_owned',
      message_id: 'message_1',
      sequence: 1,
      content: 'café 機械',
      role: 'user'
    },
    { type: 'complete', threads: 1, messages: 1 }
  ]
});
const request = (signal = new AbortController().signal, owner = '7') =>
  fetchTranscriptExport('/api/ai', owner, { 'X-CSRFToken': 'fixture' }, signal);
const serve = (value: unknown) =>
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(value), {
        headers: { 'Content-Type': 'application/json' }
      })
    )
  );
afterEach(() => vi.unstubAllGlobals());

describe('bounded transcript downloads', () => {
  it('preserves complete Unicode JSON and sends authenticated non-cached POST', async () => {
    const value = artifact();
    serve(value);
    const result = await request();
    expect(result.threads).toBe(1);
    expect(result.messages).toBe(1);
    expect(JSON.parse(await result.blob.text())).toEqual(value);
    expect(fetch).toHaveBeenCalledWith(
      '/api/ai/threads/export',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        cache: 'no-store',
        headers: { 'X-CSRFToken': 'fixture' }
      })
    );
  });

  it('refuses wrong-owner, incomplete, miscounted and out-of-order artifacts', async () => {
    const rows = artifact().records;
    for (const records of [
      rows.slice(0, -1),
      [...rows, rows[1]],
      [rows[0], rows[2], rows[1], rows[3]],
      [...rows.slice(0, -1), { type: 'complete', threads: 2, messages: 1 }]
    ]) {
      serve({ schema_version: 1, records });
      await expect(request()).rejects.toMatchObject({ code: 'incomplete' });
    }
    serve(artifact());
    await expect(request(undefined, '8')).rejects.toMatchObject({
      code: 'incomplete'
    });
  });

  it('rejects oversize headers and HTTP 413 before consuming text', async () => {
    for (const response of [
      new Response('', { status: 413 }),
      new Response('', {
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': String(MAX_TRANSCRIPT_BYTES + 1)
        }
      })
    ]) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
      await expect(request()).rejects.toMatchObject({ code: 'too_large' });
    }
  });

  it('counts streamed bytes even without a trustworthy content length', async () => {
    const cancelled = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(MAX_TRANSCRIPT_BYTES + 1));
      },
      cancel: cancelled
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(body, {
          headers: {
            'Content-Type': 'application/json',
            'Content-Length': '1'
          }
        })
      )
    );
    await expect(request()).rejects.toMatchObject({ code: 'too_large' });
    expect(cancelled).toHaveBeenCalled();
  });

  it('refuses truncated JSON and an already-cancelled download', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('{"schema_version":', {
          headers: { 'Content-Type': 'application/json' }
        })
      )
    );
    await expect(request()).rejects.toMatchObject({ code: 'incomplete' });
    serve(artifact());
    const controller = new AbortController();
    controller.abort();
    await expect(request(controller.signal)).rejects.toMatchObject({
      name: 'AbortError'
    });
  });
});
