import type { VoiceErrorCode } from '../../../../lib/types/Voice';
import { InvalidReadResponse } from '../../../functions/readQueryPolicy';
import { parseBusinessResult } from '../businessResult';

export class VoiceHttpError extends Error {
  constructor(
    public code: VoiceErrorCode,
    public status: number,
    public retryAfter?: string | null
  ) {
    super(code);
  }
}
export function voiceUrl(host: string, path: string) {
  return new URL(`voice/${path}`, `${host.replace(/\/$/, '')}/`).toString();
}
export function voiceHeaders() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return {
    'Content-Type': 'application/json',
    'X-CSRFToken': match ? decodeURIComponent(match[1]) : ''
  };
}
export async function voiceHttp<T>(
  host: string,
  path: string,
  method = 'GET',
  body?: unknown,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(voiceUrl(host, path), {
    method,
    headers: voiceHeaders(),
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.any([
      ...(signal ? [signal] : []),
      AbortSignal.timeout(path.endsWith('/turns') ? 240_000 : 20_000)
    ])
  });
  const payload = await response.json().catch(() => null);
  if (response.ok && (!payload || typeof payload !== 'object')) {
    throw new InvalidReadResponse('Invalid voice response');
  }
  const result = parseBusinessResult<T>(
    response.status ?? (response.ok ? 200 : 503),
    payload
  );
  if (!result.ok) {
    const code =
      /^VOICE_[A-Z_]+$/.test(result.code) ||
      result.code === 'IDEMPOTENCY_CONFLICT'
        ? (result.code as VoiceErrorCode)
        : 'VOICE_SESSION_UNAVAILABLE';
    throw new VoiceHttpError(
      code,
      response.status,
      response.headers?.get('Retry-After')
    );
  }
  return result.data;
}
export function endOnPageHide(host: string, id: string) {
  // Keepalive DELETE is authenticated and CSRF-protected; no beacon GET mutation.
  void fetch(voiceUrl(host, `sessions/${id}`), {
    method: 'DELETE',
    headers: voiceHeaders(),
    credentials: 'include',
    keepalive: true
  }).catch(() => {});
}
