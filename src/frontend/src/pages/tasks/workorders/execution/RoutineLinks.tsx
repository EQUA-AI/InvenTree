import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Anchor, Stack, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

/** Durable, permission-filtered relations in Work and History; opening writes nothing. */
export function RoutineLinks({
  workOrder: work,
  originOnly = false
}: Readonly<{ workOrder: WorkOrderOverview; originOnly?: boolean }>) {
  const origin = work.routine_origin;
  const links = work.routine_work?.linked_corrective;
  if (!origin && (originOnly || links === undefined)) return null;
  return (
    <Stack
      gap='xs'
      data-testid='routine-links'
      style={{ minWidth: 0, overflowWrap: 'anywhere' }}
    >
      {origin?.state === 'unavailable' && (
        <Text size='sm'>{t`Found during routine Work · source unavailable`}</Text>
      )}
      {origin?.state === 'available' && (
        <Stack gap={2}>
          <Anchor
            component={Link}
            to={`/maintenance/work-orders/${origin.id}/?work_panel=work`}
            size='sm'
          >{t`Found during ${origin.reference}`}</Anchor>
          <Text size='sm'>{origin.finding}</Text>
          {(origin.reading_id != null || origin.step_id != null) && (
            <Text size='xs' c='dimmed'>
              {t`Reading`}: {origin.reading_id ?? t`Not selected`} ·{' '}
              {t`Step execution`}: {origin.step_id ?? t`Not selected`}
            </Text>
          )}
        </Stack>
      )}
      {!originOnly && links !== undefined && (
        <>
          <Text size='sm' fw={600}>{t`Linked corrective Work`}</Text>
          {links.length === 0 && (
            <Text
              size='sm'
              c='dimmed'
            >{t`No linked corrective Work returned in your authorized view.`}</Text>
          )}
          {links.map((row) => (
            <Stack key={row.id} gap={2}>
              <Anchor
                component={Link}
                to={`/maintenance/work-orders/${row.id}/?work_panel=work`}
                size='sm'
              >
                {row.reference}
                {row.title ? ` · ${row.title}` : ''}
              </Anchor>
              <Text size='sm'>{row.finding}</Text>
              {(row.reading_id != null || row.step_id != null) && (
                <Text size='xs' c='dimmed'>
                  {t`Reading`}: {row.reading_id ?? t`Not selected`} ·{' '}
                  {t`Step execution`}: {row.step_id ?? t`Not selected`}
                </Text>
              )}
            </Stack>
          ))}
        </>
      )}
    </Stack>
  );
}
