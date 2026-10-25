import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { StaffMember } from '@lib/types/Staff';
import type { WorkOrder } from '@lib/types/Tasks';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Modal,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useApi } from '../../contexts/ApiContext';
import { StaffPicker } from './StaffPicker';
import { StaffSummaryPopover } from './StaffSummaryPopover';
import { TradeRequirementsFields } from './TradeRequirementsFields';
import {
  assignmentReview,
  buildAssignmentCommand,
  newAssignmentKey
} from './staffAssignment';
import { useStaffAccess } from './useStaffDirectory';

export type AssignmentWorkOrder = Pick<
  WorkOrder,
  | 'id'
  | 'lifecycle_version'
  | 'assigned_to'
  | 'assigned_to_name'
  | 'assignee'
  | 'required_trades'
  | 'trade_requirements_confirmed'
>;

export function StaffAssignmentModal({
  workOrder,
  cardId,
  cardAssignedTo,
  onClose,
  onSaved
}: {
  workOrder: AssignmentWorkOrder;
  cardId?: number;
  cardAssignedTo?: number | null;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const api = useApi();
  const client = useQueryClient();
  const access = useStaffAccess();
  const [staff, setStaff] = useState<StaffMember>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [serverMismatch, setServerMismatch] = useState(false);
  const [key, setKey] = useState(newAssignmentKey);
  const [expectedVersion] = useState(workOrder.lifecycle_version);
  const form = useForm({
    initialValues: {
      assignedTo:
        cardId !== undefined ? (cardAssignedTo ?? null) : workOrder.assigned_to,
      requiredTrades: workOrder.required_trades ?? [],
      confirmed: workOrder.trade_requirements_confirmed ?? false,
      reason: '',
      overrideReason: '',
      overrideConfirmed: false
    },
    validate: {
      reason: (value) =>
        value.trim() ? null : t`Record why the assignment is changing.`
    },
    onValuesChange: () => setKey(newAssignmentKey())
  });
  const review = assignmentReview(
    staff,
    form.values.requiredTrades,
    form.values.confirmed
  );
  const mismatch = serverMismatch || review === 'mismatch';
  const mayOverride =
    access.override &&
    form.values.overrideConfirmed &&
    !!form.values.overrideReason.trim();
  const blocked =
    !access.directory ||
    !access.assign ||
    stale ||
    (form.values.assignedTo != null && !staff) ||
    (mismatch && !mayOverride);
  const change = () => {
    setServerMismatch(false);
    form.setFieldValue('overrideConfirmed', false);
    form.setFieldValue('overrideReason', '');
    setKey(newAssignmentKey());
    setError('');
  };
  const submit = form.onSubmit(async (values) => {
    if (blocked) return;
    setSaving(true);
    setError('');
    try {
      await api.post(
        apiUrl(ApiEndpoints.staff_assignment),
        buildAssignmentCommand({
          work_order_id: workOrder.id,
          assigned_to: values.assignedTo,
          expected_version: expectedVersion,
          idempotency_key: key,
          reason: values.reason,
          ...(access.plan
            ? {
                required_trades: values.requiredTrades,
                trade_requirements_confirmed: values.confirmed
              }
            : {}),
          ...(cardId !== undefined ? { kanban_card_id: cardId } : {}),
          ...(mismatch ? { override_reason: values.overrideReason } : {})
        })
      );
      await Promise.all([
        client.invalidateQueries({ queryKey: ['kanban-cards'] }),
        client.invalidateQueries({ queryKey: ['kanban-board-cards'] }),
        client.invalidateQueries({ queryKey: ['work-order', workOrder.id] }),
        client.invalidateQueries({ queryKey: ['staff-member'] }),
        client.invalidateQueries({ queryKey: ['staff-directory'] })
      ]);
      onSaved?.();
      onClose();
    } catch (err: any) {
      const data = err?.response?.data;
      const code = data?.code ?? data?.error?.code;
      if (code === 'ASSIGNMENT_TRADE_MISMATCH') {
        setServerMismatch(true);
        form.setFieldValue('overrideConfirmed', false);
        setKey(newAssignmentKey());
      }
      if (
        err?.response?.status === 409 &&
        code !== 'ASSIGNMENT_TRADE_MISMATCH'
      ) {
        setStale(true);
        await client.invalidateQueries({ queryKey: ['kanban-cards'] });
        await client.invalidateQueries({
          queryKey: ['work-order', workOrder.id]
        });
      }
      setError(
        typeof data?.message === 'string'
          ? data.message
          : typeof data?.detail === 'string'
            ? data.detail
            : t`Assignment was not saved. Review permissions, staff eligibility and the current work-order version.`
      );
    } finally {
      setSaving(false);
    }
  });
  return (
    <Modal
      opened
      onClose={onClose}
      title={cardId !== undefined ? t`Assign board card` : t`Assign work order`}
      size='lg'
    >
      <form onSubmit={submit}>
        <Stack>
          {cardId !== undefined && (
            <Stack gap={4}>
              <Text size='sm'>{t`Clear this selection to inherit the work-order assignee. Trade requirements apply to the whole work order.`}</Text>
              <StaffSummaryPopover
                userId={workOrder.assigned_to}
                label={
                  workOrder.assigned_to != null
                    ? workOrder.assigned_to_name
                    : workOrder.assignee
                }
                scope={{ workOrderId: workOrder.id }}
              />
            </Stack>
          )}
          {workOrder.assignee && !workOrder.assigned_to && (
            <Alert color='blue'>
              {t`Legacy assignment text (not a verified identity)`}:{' '}
              {workOrder.assignee}
            </Alert>
          )}
          <TradeRequirementsFields
            value={form.values.requiredTrades}
            confirmed={form.values.confirmed}
            disabled={saving || !access.plan}
            onChange={(value) => {
              change();
              form.setFieldValue('requiredTrades', value);
            }}
            onConfirm={(value) => {
              change();
              form.setFieldValue('confirmed', value);
            }}
          />
          <StaffPicker
            scope={{ workOrderId: workOrder.id, kanbanCardId: cardId }}
            value={form.values.assignedTo}
            disabled={saving || !access.assign}
            placeholder={
              cardId !== undefined
                ? t`Inherit work-order assignee`
                : t`Unassigned`
            }
            requiredTrades={form.values.requiredTrades}
            confirmed={form.values.confirmed}
            onChange={(value) => {
              change();
              setStaff(undefined);
              form.setFieldValue('assignedTo', value);
            }}
            onStaffChange={setStaff}
          />
          <Textarea
            label={t`Assignment reason`}
            required
            disabled={saving}
            {...form.getInputProps('reason')}
          />
          {mismatch && (
            <Alert
              color='orange'
              title={t`Trade mismatch — human review required`}
            >
              <Stack gap='sm'>
                <Text size='sm'>{t`This person does not cover all required trades. An override cannot bypass scope, inactive-account or work-execution restrictions.`}</Text>
                {access.override ? (
                  <>
                    <Textarea
                      label={t`Override reason`}
                      required
                      disabled={saving}
                      {...form.getInputProps('overrideReason')}
                    />
                    <Checkbox
                      label={t`I explicitly authorize this trade mismatch override`}
                      disabled={saving}
                      {...form.getInputProps('overrideConfirmed', {
                        type: 'checkbox'
                      })}
                    />
                  </>
                ) : (
                  <Text size='sm'>{t`You do not have permission to override a trade mismatch.`}</Text>
                )}
              </Stack>
            </Alert>
          )}
          {error && (
            <Alert color='red' role='alert'>
              {error}
            </Alert>
          )}
          {stale && (
            <Alert color='orange'>{t`The work order changed. Close and reopen this dialog to review the latest assignment before trying again.`}</Alert>
          )}
          <Group justify='flex-end'>
            <Button
              variant='default'
              onClick={onClose}
              disabled={saving}
            >{t`Cancel`}</Button>
            <Button
              type='submit'
              color={mismatch ? 'orange' : undefined}
              loading={saving}
              disabled={blocked}
            >
              {mismatch ? t`Override and save assignment` : t`Save assignment`}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
