import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Group,
  Loader,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { Link } from 'react-router-dom';

import { AlarmTable } from './mimic/AlarmTable';
import { UnitMimic } from './mimic/UnitMimic';
import { displayFor } from './mimic/format';
import { useStationMimic } from './useStationMimic';

/**
 * A pump's own mimic: its drawing, its parts and what each one reads.
 *
 * The same view a station shows for a bay chosen on its drawing, reached here
 * from the pump's page instead. It asks the pump's station for that one bay -
 * a pump's readings are the station's, read out by bay - so it is drawn only
 * for a pump that knows which station and which bay it is.
 */
export default function PumpMimic({
  stationId,
  unit
}: Readonly<{ stationId: number; unit: string }>) {
  const { ref, query } = useStationMimic(stationId, unit);
  const data = query.data;
  const bay = data?.bays.find((b) => b.key === unit);
  // The station reports every pump's alarms; this page is about one of them.
  const alarms = (data?.alarms ?? []).filter(
    (alarm) => alarm.machine === bay?.machine
  );

  return (
    <Stack ref={ref} gap='md' mih={160}>
      <Group justify='space-between' align='center'>
        <Group gap='lg'>
          {data && (
            <>
              <Text size='xs' c='dimmed'>
                {t`Source`}: {data.source?.name ?? t`Not bound`}
              </Text>
              <Text size='xs' c='dimmed'>
                {t`Last poll`}:{' '}
                {data.last_poll_at
                  ? new Date(data.last_poll_at).toLocaleString()
                  : t`Not polled yet`}
              </Text>
              <Text size='xs' c='dimmed'>
                {t`Response time`}:{' '}
                {new Date(data.generated_at).toLocaleString()}
              </Text>
            </>
          )}
        </Group>
        <Button
          variant='default'
          size='xs'
          loading={query.isFetching}
          onClick={() => query.refetch()}
        >{t`Refresh`}</Button>
      </Group>
      {query.isError ? (
        <Alert color='red'>{t`Live readings are unavailable. Refresh to retry; cached values are hidden.`}</Alert>
      ) : !data ? (
        <Loader />
      ) : (
        <>
          {data.layout.review_status !== 'approved' && (
            <Alert color='yellow'>{t`This schematic awaits plant review. Missing mappings are shown as unavailable.`}</Alert>
          )}
          {!data.enabled && (
            <Alert color='yellow'>{t`Polling is disabled for this station. Live values are unavailable.`}</Alert>
          )}
          {data.last_error_code && (
            <Alert color='red'>
              {t`Last station polling error`}: {data.last_error_code}
            </Alert>
          )}
          <UnitMimic
            data={data}
            unit={unit}
            actions={
              <Anchor
                component={Link}
                to={`/machines/machine/${stationId}/mimic`}
                size='sm'
              >
                {t`Open station mimic`}
              </Anchor>
            }
          />
          <Title order={4}>{t`Threshold alarms`}</Title>
          {alarms.length ? (
            <AlarmTable
              alarms={alarms}
              display={displayFor(data, unit)}
              onAcknowledged={() => query.refetch()}
            />
          ) : (
            <Text>{t`No active alarms from configured thresholds.`}</Text>
          )}
        </>
      )}
    </Stack>
  );
}
