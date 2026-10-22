import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { FitProjection, FittedRecord } from '@lib/types/RecordedFacts';
import { t } from '@lingui/core/macro';
import {
  Anchor,
  Button,
  NumberInput,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { MissingValue } from '../../components/items/ValueTooltip';
import { returnState } from '../../components/nav/returnPath';
import { RecordCommandState } from '../../components/records/RecordCommandState';
import { useRecordedCommand } from '../../components/records/useRecordedCommand';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import { recordedTime } from '../tasks/workorders/execution/ExecutionFacts';

export function FittedItemFact({
  fit,
  compact = false
}: Readonly<{ fit: FittedRecord; compact?: boolean }>) {
  const location = useLocation();
  const back = returnState(
    `${location.pathname}${location.search}`,
    t`Fitted identity`
  );
  return (
    <Stack gap={2} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
      {fit.retracted_at && (
        <Text fw={600} size='sm'>
          {t`Retracted fit record`} · {fit.retracted_by ?? t`Not recorded`} ·{' '}
          {recordedTime(fit.retracted_at)} · {fit.retraction_reason}
        </Text>
      )}
      <Anchor
        component={Link}
        to={getDetailUrl(ModelType.stockitem, fit.stock_item)}
        state={back}
        size='sm'
      >{t`Stock item ${fit.stock_item}`}</Anchor>
      <Text size='sm'>
        {t`Serial`}: {fit.serial ?? t`Not recorded`} · {t`Lot`}:{' '}
        {fit.lot ?? t`Not recorded`}
      </Text>
      <Text size='sm'>
        {fit.source === 'closeout'
          ? t`Work completion (fit ordering basis)`
          : t`Fitted`}
        : {recordedTime(fit.fitted_at)}
      </Text>
      <Text size='xs' c='dimmed'>
        {t`Recorded by`}: {fit.recorded_by ?? t`Not recorded`} ·{' '}
        {recordedTime(fit.recorded_at)}
      </Text>
      {!compact && (
        <>
          {fit.source === 'closeout' && (
            <Text
              size='xs'
              c='dimmed'
            >{t`Actual installation time is not separately recorded.`}</Text>
          )}
          <Text size='xs' c='dimmed'>
            {t`Verified by`}: {fit.verified_by ?? t`Not recorded`} ·{' '}
            {recordedTime(fit.verified_at)}
          </Text>
          {fit.work_order && (
            <Anchor
              component={Link}
              to={`/maintenance/work-orders/${fit.work_order}/`}
              size='sm'
            >{t`Work ${fit.work_order}`}</Anchor>
          )}
          <Text size='xs'>{fit.reason}</Text>
        </>
      )}
    </Stack>
  );
}
export function FittedIdentity({
  machineId,
  slotId,
  value
}: Readonly<{
  machineId: number;
  slotId: number;
  value: FitProjection | null | undefined;
}>) {
  const user = useUserState();
  const host = useLocalState((state) => state.hostKey);
  const scope = JSON.stringify([host, user.user?.pk, user.authGeneration]);
  const [saved, setSaved] = useState<{
    scope: string;
    value: FitProjection;
  } | null>(null);
  const newerSaved =
    saved?.scope === scope && saved.value.version >= (value?.version ?? 0)
      ? saved.value
      : null;
  const facts =
    user.hasViewRole(UserRoles.stock) && value !== null
      ? (newerSaved ?? value)
      : null;
  const command = useRecordedCommand<FitProjection>(
    `${apiUrl(ApiEndpoints.equipment_registry).replace(/registry\/$/, 'recorded/')}${machineId}/slots/${slotId}/fits/`,
    (result) => setSaved({ scope, value: result }),
    [
      ['registry', 'components'],
      [
        'tabledata',
        `${apiUrl(ApiEndpoints.equipment_registry)}${machineId}/components/`
      ],
      ['machine-positions']
    ]
  );
  const [stock, setStock] = useState<string | number>('');
  const [date, setDate] = useState('');
  const [reason, setReason] = useState('');
  return (
    <Stack gap='sm' data-testid='fitted-identity'>
      {facts?.current ? (
        <FittedItemFact fit={facts.current} />
      ) : facts ? (
        <MissingValue reason={t`No fit recorded`} />
      ) : (
        <MissingValue reason={t`Fitted identity unavailable for your role`} />
      )}
      {!!facts?.history.length && (
        <details>
          <summary>{t`Replacement history (${facts.history.length})`}</summary>
          <Stack gap='md'>
            {facts.history.map((fit) => (
              <FittedItemFact key={fit.id} fit={fit} />
            ))}
          </Stack>
        </details>
      )}
      {facts?.retracted?.map((fit) => (
        <FittedItemFact key={fit.id} fit={fit} />
      ))}
      {facts?.pending.map((fit) => (
        <Stack gap='xs' key={fit.id}>
          <Text fw={600} size='sm'>
            {fit.source === 'manual'
              ? t`Pending verification — not current fit`
              : t`Installation requires review — not current fit`}
          </Text>
          <FittedItemFact fit={fit} />
          {fit.source === 'manual' &&
            user.hasChangeRole(UserRoles.work_order) && (
              <Button
                disabled={
                  !!command.retained ||
                  !fit.recorded_by_id ||
                  fit.recorded_by_id === user.user?.pk
                }
                loading={command.pending}
                onClick={() =>
                  command.submit('verify', { fit_id: fit.id }, facts.version)
                }
              >{t`Verify existing fit`}</Button>
            )}
        </Stack>
      ))}
      {facts &&
        user.hasChangeRole(UserRoles.work_order) &&
        user.hasViewRole(UserRoles.stock) && (
          <details>
            <summary>{t`Record existing fit for verification`}</summary>
            <Stack gap='sm' mt='sm'>
              <NumberInput
                min={1}
                label={t`Native stock item ID`}
                value={stock}
                onChange={setStock}
              />
              <TextInput
                label={t`Fitted date (ISO time with zone)`}
                placeholder='2026-10-20T08:00:00Z'
                value={date}
                onChange={(e) => setDate(e.currentTarget.value)}
              />
              <Textarea
                label={t`Fit evidence / reason`}
                value={reason}
                onChange={(e) => setReason(e.currentTarget.value)}
              />
              <Text
                size='xs'
                c='dimmed'
              >{t`A different authorized registry reviewer must verify this entry. Recording a fit does not move stock or release equipment.`}</Text>
              <Button
                disabled={
                  !stock || !date || !reason.trim() || !!command.retained
                }
                loading={command.pending}
                onClick={() =>
                  command.submit(
                    'record',
                    { stock_item: Number(stock), fitted_at: date, reason },
                    facts.version
                  )
                }
              >{t`Record pending fit`}</Button>
            </Stack>
          </details>
        )}
      <RecordCommandState controller={command} />
    </Stack>
  );
}
