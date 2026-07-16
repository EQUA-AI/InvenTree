/**
 * useVoiceCapture (WS8-T8 re-cut): transcript-only capture client.
 *
 * Drives /api/voice/captures/ — create (the consent act), append immutable
 * revisions (from the realtime voice path or typed correction), accept the
 * exact reviewed revision by hash, cancel, and commit. Commit fails closed
 * with DESTINATION_UNAVAILABLE until the canonical destination substrate
 * (WS11 Repair intake / Feature #15) is live. No audio ever exists here.
 */

import { useCallback, useState } from 'react';

import { api } from '../App';

export interface CaptureRevision {
  id: string;
  revision: number;
  full_text: string;
  content_hash: string;
  language: string;
  edit_reason: string;
  accepted: boolean;
  created_at: string;
}

export interface CapturePayload {
  id: string;
  purpose: 'fault_intake' | 'closeout';
  state: string;
  work_order_id: number;
  work_order_version: number;
  consent_version: string;
  accepted_revision_id: string | null;
  terminal_reason: string | null;
  revisions: CaptureRevision[];
}

export interface UseVoiceCaptureResult {
  capture: CapturePayload | null;
  error: string | null;
  busy: boolean;
  create: (
    purpose: CapturePayload['purpose'],
    workOrderId: number,
    workOrderVersion: number
  ) => Promise<void>;
  addRevision: (fullText: string, editReason?: string) => Promise<void>;
  acceptRevision: (revision: CaptureRevision) => Promise<void>;
  cancel: () => Promise<void>;
  commit: () => Promise<void>;
  reset: () => void;
}

export function useVoiceCapture(): UseVoiceCaptureResult {
  const [capture, setCapture] = useState<CapturePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const call = useCallback(
    async (invoke: () => Promise<{ data: CapturePayload }>) => {
      setBusy(true);
      setError(null);
      try {
        const response = await invoke();
        setCapture(response.data);
      } catch (err: any) {
        setError(err?.response?.data?.error ?? 'CAPTURE_REQUEST_FAILED');
      } finally {
        setBusy(false);
      }
    },
    []
  );

  const create = useCallback(
    async (
      purpose: CapturePayload['purpose'],
      workOrderId: number,
      workOrderVersion: number
    ) =>
      call(() =>
        api.post('/api/voice/captures/', {
          purpose,
          work_order_id: workOrderId,
          work_order_version: workOrderVersion
        })
      ),
    [call]
  );

  const addRevision = useCallback(
    async (fullText: string, editReason = '') => {
      if (!capture) {
        return;
      }
      await call(() =>
        api.post(`/api/voice/captures/${capture.id}/revise/`, {
          full_text: fullText,
          edit_reason: editReason
        })
      );
    },
    [call, capture]
  );

  const acceptRevision = useCallback(
    async (revision: CaptureRevision) => {
      if (!capture) {
        return;
      }
      await call(() =>
        api.post(`/api/voice/captures/${capture.id}/accept/`, {
          revision_id: revision.id,
          content_hash: revision.content_hash
        })
      );
    },
    [call, capture]
  );

  const cancel = useCallback(async () => {
    if (!capture) {
      return;
    }
    await call(() => api.post(`/api/voice/captures/${capture.id}/cancel/`));
  }, [call, capture]);

  const commit = useCallback(async () => {
    if (!capture) {
      return;
    }
    await call(() => api.post(`/api/voice/captures/${capture.id}/commit/`));
  }, [call, capture]);

  const reset = useCallback(() => {
    setCapture(null);
    setError(null);
  }, []);

  return {
    capture,
    error,
    busy,
    create,
    addRevision,
    acceptRevision,
    cancel,
    commit,
    reset
  };
}
