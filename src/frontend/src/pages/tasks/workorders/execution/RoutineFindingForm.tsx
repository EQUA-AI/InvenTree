import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useProcessCommand } from './useProcessCommand';

const buttonStyles = {
  root: { height: 'auto', minHeight: 44, paddingBlock: 8, maxWidth: '100%' },
  label: {
    whiteSpace: 'normal' as const,
    overflow: 'visible',
    textAlign: 'center' as const
  }
};

/** Optional native form; one saved intent and exact replay through the existing writer. */
export function RoutineFindingForm({
  workOrder: work
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const controller = useProcessCommand('routine_finding', work.id, work.id);
  const { state } = controller;
  const [title, setTitle] = useState('');
  const [finding, setFinding] = useState('');
  const [reading, setReading] = useState<string | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const value = work.routine_work;
  const permitted = value?.can_create_linked === true;
  const saved = 'intent' in state ? state.intent.payload : null;
  const selectedReading = value?.readings.find(
    (row) => String(row.id) === reading
  );
  const incompatiblePair =
    !!reading && !!step && selectedReading?.step_execution !== Number(step);
  const taskOptions = (value?.applications ?? [])
    .flatMap((app) => app.tasks)
    .filter((row) => !reading || row.id === selectedReading?.step_execution)
    .map((row) => ({
      value: String(row.id),
      label: `${row.step.sequence}. ${row.step.title}`
    }));
  const unavailable =
    !value || value.can_create_linked === undefined
      ? t`The server does not supply linked corrective creation availability.`
      : ((
          {
            routine_inactive_or_canceled: t`This routine Work is inactive or canceled. Linked corrective creation is unavailable.`,
            permission: t`Creating linked corrective Work requires Work creation permission.`,
            machine_scope: t`This machine is unavailable in your current scope. Linked corrective creation is unavailable.`,
            routine_scope: t`This routine Work is unavailable in your current scope. Linked corrective creation is unavailable.`
          } as Record<string, string>
        )[value.create_unavailable_reason ?? ''] ??
        t`Linked corrective creation is unavailable. Refresh this Work to check its current availability.`);
  if (!permitted && state.status === 'idle')
    return (
      <Text size='sm' c='dimmed'>
        {unavailable}
      </Text>
    );
  return (
    <Stack gap='sm' data-testid='routine-finding-form'>
      <Text fw={600} size='sm'>{t`Create linked corrective Work`}</Text>
      <Text
        size='sm'
        c='dimmed'
      >{t`Record the observation for a new corrective Work on this machine. This does not complete or change the routine Work.`}</Text>
      {state.status === 'idle' ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (
              !permitted ||
              !title.trim() ||
              !finding.trim() ||
              incompatiblePair
            )
              return;
            void controller.execute({
              kind: 'routine_finding',
              workId: work.id,
              targetId: work.id,
              url: '/api/kanban/work-orders/commands/create/',
              payload: {
                idempotency_key: crypto.randomUUID(),
                machine: work.machine!,
                recurrence_of: work.id,
                prior_version: work.lifecycle_version,
                corrective_origin_kind: 'routine_finding',
                work_order_type: 'corrective',
                title: title.trim(),
                corrective_finding: finding.trim(),
                corrective_reading: reading ? Number(reading) : null,
                corrective_step: step ? Number(step) : null
              }
            });
          }}
        >
          <Stack gap='sm'>
            <TextInput
              label={t`Corrective Work title`}
              value={title}
              onChange={(event) => setTitle(event.currentTarget.value)}
              required
              maxLength={200}
            />
            <Textarea
              label={t`Finding / observation`}
              value={finding}
              onChange={(event) => setFinding(event.currentTarget.value)}
              required
              autosize
              minRows={3}
            />
            <Select
              label={t`Related reading (optional)`}
              placeholder={t`No reading selected`}
              clearable
              value={reading}
              onChange={(next) => {
                setReading(next);
                const source = value?.readings.find(
                  (row) => String(row.id) === next
                );
                if (next && step && source?.step_execution !== Number(step))
                  setStep(null);
              }}
              data={(value?.readings ?? []).map((row) => ({
                value: String(row.id),
                label: `${row.label} · ${row.phase} · ${row.raw_text}`
              }))}
            />
            <Select
              label={t`Related task step (optional)`}
              placeholder={
                incompatiblePair
                  ? t`Saved task step needs review`
                  : t`No task step selected`
              }
              clearable
              value={step}
              onChange={setStep}
              data={taskOptions}
              disabled={!!reading && selectedReading?.step_execution == null}
            />
            {selectedReading?.step_execution === null && (
              <Text size='sm'>{t`This reading has no linked task step. Clear the reading to select a task step.`}</Text>
            )}
            {incompatiblePair && (
              <Alert
                color='orange'
                title={t`Reading and task step do not match`}
                styles={{ body: { minWidth: 0 } }}
              >
                <Stack gap='xs'>
                  <Text size='sm'>{t`Clear the task step or choose a compatible reading before creating linked Work.`}</Text>
                  <Button
                    type='button'
                    variant='light'
                    styles={buttonStyles}
                    onClick={() => setStep(null)}
                  >{t`Clear incompatible task step`}</Button>
                </Stack>
              </Alert>
            )}
            <Button
              type='submit'
              styles={buttonStyles}
              disabled={
                !controller.available ||
                !title.trim() ||
                !finding.trim() ||
                incompatiblePair
              }
            >{t`Create linked corrective Work`}</Button>
            {!controller.available && (
              <Text size='sm'>{t`Browser coordination is unavailable. Review saved Work before creating a follow-up.`}</Text>
            )}
          </Stack>
        </form>
      ) : (
        <>
          <Text size='sm' fw={500}>
            {String(saved?.title ?? '')}
          </Text>
          <Text size='sm'>{String(saved?.corrective_finding ?? '')}</Text>
          {state.status === 'pending' && (
            <Text
              component='output'
              size='sm'
            >{t`Creating linked corrective Work…`}</Text>
          )}
          {state.status === 'confirmed' && (
            <Alert
              color='blue'
              title={t`Linked corrective Work created`}
              styles={{ body: { minWidth: 0 } }}
            >
              {state.result && (
                <Anchor
                  component={Link}
                  to={`/maintenance/work-orders/${state.result.workId}/?work_panel=work`}
                >
                  {state.result.reference || state.result.workId}
                </Anchor>
              )}
              <Text size='sm'>{t`The routine Work retains its own tasks, readings and outcome.`}</Text>
            </Alert>
          )}
          {state.status === 'refused' && (
            <Alert
              color='orange'
              title={t`Linked corrective Work refused`}
              styles={{ body: { minWidth: 0 } }}
            >
              <Text size='sm'>
                {state.message ||
                  t`Review the latest saved Work and your permissions before trying again.`}
              </Text>
            </Alert>
          )}
          {state.status === 'uncertain' && (
            <Alert
              color='orange'
              title={t`Creation outcome unknown`}
              styles={{ body: { minWidth: 0 } }}
            >
              <Stack gap='xs'>
                <Text size='sm'>{t`Check the original command before creating anything else. A missing response does not prove that creation failed.`}</Text>
                <Button
                  styles={buttonStyles}
                  variant='light'
                  disabled={!controller.available}
                  onClick={() => void controller.execute()}
                >{t`Check original creation command`}</Button>
              </Stack>
            </Alert>
          )}
          {(state.status === 'confirmed' || state.status === 'refused') && (
            <Button
              styles={buttonStyles}
              variant='light'
              onClick={() => {
                const retain = state.status === 'refused';
                setTitle(retain ? String(saved?.title ?? '') : '');
                setFinding(
                  retain ? String(saved?.corrective_finding ?? '') : ''
                );
                setReading(
                  !retain || saved?.corrective_reading == null
                    ? null
                    : String(saved.corrective_reading)
                );
                setStep(
                  !retain || saved?.corrective_step == null
                    ? null
                    : String(saved.corrective_step)
                );
                void controller.review();
              }}
            >
              {state.status === 'confirmed'
                ? permitted
                  ? t`Record another finding`
                  : t`Done`
                : t`Review latest linked Work`}
            </Button>
          )}
        </>
      )}
    </Stack>
  );
}
