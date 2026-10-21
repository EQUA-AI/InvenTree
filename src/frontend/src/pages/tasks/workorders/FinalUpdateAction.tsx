import type { CloseoutReadiness } from '@lib/types/Closeout';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Group,
  Loader,
  Stack,
  Text
} from '@mantine/core';
import dayjs from 'dayjs';
import { Link } from 'react-router-dom';
import { useActionFocus } from './useActionFocus';
import type { UpdateState } from './workRecordUpdate';

/** The action and report both consume this controller's single operation state. */
export function WorkRecordUpdate({
  state,
  readiness,
  loading,
  eligible,
  completed,
  completedAt,
  completedId,
  onSubmit,
  onReconcile,
  onReview,
  onUseReviewed,
  reviewed,
  locked,
  latest,
  version,
  onRecovery
}: Readonly<{
  state: UpdateState;
  readiness?: CloseoutReadiness;
  loading: boolean;
  eligible: boolean;
  completed: boolean;
  completedAt?: string | null;
  completedId?: number;
  onSubmit: () => void;
  onReconcile: () => void;
  onReview: () => void;
  onUseReviewed: () => void;
  reviewed: boolean;
  locked: boolean;
  latest: Record<string, string>;
  version: number;
  onRecovery: (step: number) => void;
}>) {
  const resultFocus = useActionFocus(state.status === 'pending');
  const labels = {
    cause: t`Cause`,
    action: t`Action taken`,
    result: t`Result`,
    verification_summary: t`Verification summary`
  };
  const comparedFields = [
    'cause',
    'action',
    'result',
    'verification_summary'
  ] as const;
  const when = (value?: string | null) =>
    value ? dayjs(value).format('DD MMM YYYY, HH:mm') : t`Time unavailable`;
  return (
    <Stack
      {...resultFocus.props}
      aria-label={t`Maintenance record update`}
      gap='sm'
      mt='md'
      data-testid='work-record-update'
    >
      <Text size='sm'>{t`Updates the internal AIMMS maintenance record. Physical return and external-system acknowledgement are separate.`}</Text>
      {state.status === 'confirmed' ? (
        <Alert color='green'>
          {t`Record updated`} ·{' '}
          {when(
            state.recordedAt ??
              (state.receipt.metadata.closeout_id === completedId
                ? completedAt
                : null)
          )}
        </Alert>
      ) : completed && ['idle', 'stale', 'failed'].includes(state.status) ? (
        <Alert color='blue'>
          {t`Record updated elsewhere`} · {when(completedAt)}
        </Alert>
      ) : (
        <>
          {loading ? (
            <Text>{t`Checking readiness…`}</Text>
          ) : !readiness ? (
            <Alert color='yellow'>{t`Readiness is unavailable. The update is locked.`}</Alert>
          ) : (
            <>
              {!readiness.ready && (
                <Alert color='yellow'>
                  <Stack gap='xs'>
                    {readiness.blockers.map((row) => (
                      <Text key={`${row.code}-${row.message}`} size='sm'>
                        {row.message}
                        {row.code === 'VERIFICATION_REQUIRED' && (
                          <Anchor
                            component={Link}
                            replace
                            to='?work_panel=verify'
                          >{t`Open Verify & Return`}</Anchor>
                        )}
                        {(row.code === 'PART_VARIANCE_UNRESOLVED' ||
                          row.code === 'PART_CANDIDATE_UNRESOLVED') && (
                          <Anchor
                            href='#closeout-recovery'
                            onClick={() => onRecovery(2)}
                          >{t`Open material reconciliation`}</Anchor>
                        )}
                        {row.code === 'CLOSEOUT_REQUIRED' && (
                          <Anchor
                            href='#closeout-recovery'
                            onClick={() => onRecovery(1)}
                          >{t`Open capture review`}</Anchor>
                        )}
                      </Text>
                    ))}
                  </Stack>
                </Alert>
              )}
              {readiness.warnings?.length > 0 && (
                <Alert color='yellow'>
                  {readiness.warnings.map((row) => (
                    <Text key={`${row.code}-${row.message}`} size='sm'>
                      {row.message}
                    </Text>
                  ))}
                </Alert>
              )}
            </>
          )}
          {state.status === 'pending' ? (
            <Button
              disabled
              mih={44}
              leftSection={
                <Loader size='xs' color='currentColor' aria-hidden />
              }
              styles={{ root: { color: 'var(--mantine-color-text)' } }}
            >
              {state.reconciling ? t`Checking update status…` : t`Updating…`}
            </Button>
          ) : state.status === 'uncertain' ? (
            <Alert color='yellow'>
              <Stack gap='xs'>
                <Text>
                  {state.message ??
                    t`The original update has not been confirmed.`}
                </Text>
                <Button
                  variant='light'
                  onClick={() => resultFocus.run(onReconcile)}
                  disabled={locked}
                >{t`Check original update`}</Button>
              </Stack>
            </Alert>
          ) : state.status === 'failed' || state.status === 'stale' ? (
            <Alert color={state.status === 'stale' ? 'yellow' : 'red'}>
              <Stack gap='sm'>
                <Text>{state.message}</Text>
                <Text size='xs'>
                  {t`Retained draft version`}:{' '}
                  {state.intent.payload.expected_version}
                </Text>
                {comparedFields.map((field) => (
                  <Text
                    size='sm'
                    key={field}
                    style={{ overflowWrap: 'anywhere' }}
                  >
                    {labels[field]}:{' '}
                    {state.intent.payload[field] ||
                      (field === 'cause'
                        ? t`Not established`
                        : t`Not recorded`)}
                  </Text>
                ))}
                <Text size='xs'>
                  {t`Latest saved account version`}: {version}
                </Text>
                {comparedFields.map((field) => (
                  <Text
                    size='sm'
                    key={`latest-${field}`}
                    style={{ overflowWrap: 'anywhere' }}
                  >
                    {labels[field]}:{' '}
                    {latest[field] ||
                      (field === 'cause'
                        ? t`Not established`
                        : t`Not recorded`)}
                  </Text>
                ))}
                <Text size='xs'>{t`Newer saved decisions replace conflicting retained values. Manual values and later local edits are preserved for review.`}</Text>
                <Group>
                  <Button
                    variant='light'
                    onClick={() => resultFocus.run(onReview)}
                    disabled={locked}
                  >{t`Review latest record`}</Button>
                  {state.status === 'failed' && (
                    <Button
                      variant='light'
                      onClick={() => resultFocus.run(onReconcile)}
                      disabled={locked}
                    >{t`Retry original update`}</Button>
                  )}
                  {reviewed && (
                    <Button
                      variant='default'
                      onClick={() => resultFocus.run(onUseReviewed)}
                      disabled={locked || completed}
                    >{t`Use reviewed account for a new update`}</Button>
                  )}
                </Group>
              </Stack>
            </Alert>
          ) : (
            <Button
              color='blue.9'
              mih={44}
              onClick={() => resultFocus.run(onSubmit)}
              disabled={
                !eligible || locked || !readiness?.ready || loading || completed
              }
            >{t`Update maintenance record`}</Button>
          )}
          {!eligible && state.status === 'idle' && (
            <Text
              size='sm'
              c='dimmed'
            >{t`A current account with action, result and verification summary is required. Finish the native capture review below; an active voice capture must finish first.`}</Text>
          )}
        </>
      )}
      <Anchor
        component={Link}
        replace
        to='?work_panel=verify'
      >{t`View Verify & Return`}</Anchor>
    </Stack>
  );
}
