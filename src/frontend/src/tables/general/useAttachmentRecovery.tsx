import { t } from '@lingui/core/macro';
import { Alert } from '@mantine/core';
import { useCallback, useRef, useState } from 'react';
import type { UseFormReturn } from 'react-hook-form';
import { useApi } from '../../contexts/ApiContext';
import { DraftRecovery } from '../../pages/tasks/workorders/DraftRecovery';

export function useAttachmentRecovery(
  enabled: boolean,
  url: string,
  pk?: number
) {
  const api = useApi();
  const formRef = useRef<UseFormReturn | null>(null);
  const [failure, setFailure] = useState<Record<string, string> | null>(null);
  const [latest, setLatest] = useState<Record<string, string> | null>(null);
  const [revision, setRevision] = useState<string | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revisionAvailable, setRevisionAvailable] = useState(false);
  const [revisionRead, setRevisionRead] = useState(false);
  const onRevision = useCallback((value: unknown) => {
    setRevisionRead(true);
    setRevisionAvailable(typeof value === 'string' && value.length === 64);
  }, []);
  const fields = (value: Record<string, unknown>) =>
    Object.fromEntries(
      ['filename', 'link', 'comment']
        .filter((key) => typeof value[key] === 'string')
        .map((key) => [key, String(value[key])])
    );
  const reset = () => {
    setFailure(null);
    setLatest(null);
    setRevision(null);
    setReadFailed(false);
  };
  const review = async () => {
    setLatest(null);
    setRevision(null);
    setReadFailed(false);
    setBusy(true);
    try {
      const response = await api.get(`${url}${pk}/`);
      if (
        !response.data?.expected_revision ||
        response.data.pk !== pk ||
        response.data.model_type !== formRef.current?.getValues('model_type') ||
        String(response.data.model_id) !==
          String(formRef.current?.getValues('model_id'))
      )
        throw new Error('Missing revision');
      setLatest(fields(response.data));
      setRevision(response.data.expected_revision);
    } catch {
      setReadFailed(true);
    } finally {
      setBusy(false);
    }
  };
  const useReviewed = (keep: boolean) => {
    if (!latest || !revision || !formRef.current) return;
    if (!keep)
      for (const [key, value] of Object.entries(latest))
        formRef.current.setValue(key, value);
    formRef.current.setValue('expected_revision', revision);
    reset();
  };
  return {
    onRevision,
    ...(enabled
      ? {
          hideErrorNotification: true,
          preserveDraftOnRefresh: true,
          submitDisabled: !revisionAvailable || failure != null,
          onOpen: () => {
            reset();
            setRevisionAvailable(false);
            setRevisionRead(false);
          },
          onFormError: (error: any, form: UseFormReturn) => {
            if (
              !error?.isAxiosError &&
              !error?.response &&
              typeof error?.status !== 'number'
            )
              return;
            formRef.current = form;
            setFailure(fields(form.getValues()));
            setLatest(null);
            setRevision(null);
          },
          preFormContent: failure ? (
            <DraftRecovery
              title={t`Attachment save not confirmed`}
              message={
                readFailed
                  ? t`Latest metadata unavailable. Your draft is retained. Access or source could not be confirmed.`
                  : t`Your metadata draft is retained. Review the latest authorized record before another save.`
              }
              draft={failure}
              latest={latest ?? undefined}
              onReview={() => void review()}
              busy={busy}
              onKeep={latest ? () => useReviewed(true) : undefined}
              onLatest={latest ? () => useReviewed(false) : undefined}
            />
          ) : revisionRead && !revisionAvailable ? (
            <Alert
              color='orange'
              title={t`Attachment revision unavailable`}
            >{t`This server does not supply the saved revision needed for a safe edit. Your metadata draft stays in this form; saving is locked.`}</Alert>
          ) : undefined
        }
      : {})
  };
}
