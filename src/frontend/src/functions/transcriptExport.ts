/** Validate a complete bounded transcript before handing any bytes to Downloads. */
export const MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024;
export const MAX_TRANSCRIPT_RECORDS = 10000;

export class TranscriptExportError extends Error {
  constructor(readonly code: 'too_large' | 'unavailable' | 'incomplete') {
    super(code);
  }
}

export interface TranscriptExportCounts {
  threads: number;
  messages: number;
}

function countsFromArtifact(value: any, owner: string): TranscriptExportCounts {
  const rows = value?.records;
  const fail = () => {
    throw new TranscriptExportError('incomplete');
  };
  if (value?.schema_version !== 1 || !Array.isArray(rows) || rows.length < 2)
    return fail();
  if (rows.length > MAX_TRANSCRIPT_RECORDS)
    throw new TranscriptExportError('too_large');
  const manifest = rows[0];
  const last = rows[rows.length - 1];
  if (
    manifest?.type !== 'manifest' ||
    manifest.schema_version !== 1 ||
    manifest.scope !== 'owned_thread_transcripts' ||
    manifest.owner_id !== owner ||
    manifest.consistency !== 'live_read_with_creation_cutoff' ||
    !Array.isArray(manifest.includes) ||
    !Array.isArray(manifest.excludes) ||
    last?.type !== 'complete'
  )
    return fail();
  const threads = new Set<string>();
  const messageIds = new Set<string>();
  let currentThread = '';
  let sequence = 0;
  let watermark = 0;
  let messages = 0;
  for (const row of rows.slice(1, -1)) {
    if (row?.type === 'thread') {
      if (
        typeof row.thread_id !== 'string' ||
        !row.thread_id ||
        threads.has(row.thread_id) ||
        typeof row.title !== 'string' ||
        typeof row.summary !== 'string' ||
        !Number.isSafeInteger(row.message_watermark) ||
        row.message_watermark < 0
      )
        return fail();
      currentThread = row.thread_id;
      threads.add(currentThread);
      sequence = 0;
      watermark = row.message_watermark;
    } else if (row?.type === 'message') {
      if (
        !currentThread ||
        row.thread_id !== currentThread ||
        !['string', 'number'].includes(typeof row.message_id) ||
        messageIds.has(String(row.message_id)) ||
        !Number.isSafeInteger(row.sequence) ||
        row.sequence <= sequence ||
        row.sequence > watermark ||
        typeof row.content !== 'string' ||
        typeof row.role !== 'string'
      )
        return fail();
      messageIds.add(String(row.message_id));
      sequence = row.sequence;
      messages++;
    } else return fail();
  }
  if (last.threads !== threads.size || last.messages !== messages)
    return fail();
  return { threads: threads.size, messages };
}

export async function fetchTranscriptExport(
  host: string,
  owner: string,
  headers: Record<string, string>,
  signal: AbortSignal
): Promise<TranscriptExportCounts & { blob: Blob }> {
  const response = await fetch(`${host}/threads/export`, {
    method: 'POST',
    credentials: 'include',
    cache: 'no-store',
    headers,
    signal
  });
  const oversized =
    response.status === 413 ||
    Number(response.headers.get('Content-Length')) > MAX_TRANSCRIPT_BYTES;
  if (
    !response.ok ||
    oversized ||
    !response.headers.get('Content-Type')?.startsWith('application/json')
  ) {
    await response.body?.cancel().catch(() => {});
    throw new TranscriptExportError(oversized ? 'too_large' : 'unavailable');
  }
  if (!response.body) throw new TranscriptExportError('incomplete');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let done = false;
  try {
    while (!done) {
      signal.throwIfAborted();
      const next = await reader.read();
      done = next.done;
      if (next.value) {
        size += next.value.byteLength;
        if (size > MAX_TRANSCRIPT_BYTES)
          throw new TranscriptExportError('too_large');
        chunks.push(next.value);
      }
    }
  } finally {
    if (!done) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  signal.throwIfAborted();
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  let value: unknown;
  try {
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    throw new TranscriptExportError('incomplete');
  }
  const counts = countsFromArtifact(value, owner);
  return { ...counts, blob: new Blob([bytes], { type: 'application/json' }) };
}

export function saveTranscriptDownload(blob: Blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'aimms-conversations.json';
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    // Give the browser time to acquire the download before releasing its URL.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
