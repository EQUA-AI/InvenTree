import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { PointTable, ReadingSections } from './mimic/MimicReadings';
import { MimicSchematic, stateStyle } from './mimic/MimicSchematic';
import { PANEL, UnitMimic } from './mimic/UnitMimic';
import {
  displayFor,
  numberText,
  reasonLabel,
  stateLabel
} from './mimic/format';
import type { MimicData, Total } from './mimic/types';
import { useStationMimic } from './useStationMimic';

export { reasonLabel, unchangedLabel } from './mimic/format';
export type { MimicData, MimicPoint } from './mimic/types';

/** The states a bay can be drawn in, in the order the legend lists them. */
const LEGEND = ['running', 'idle', 'fault', 'stale', 'unknown'];

/**
 * How many bays are in each state, as a key to the drawing above it.
 *
 * Counts what is drawn, nothing more: a bay whose status cannot be read is
 * counted as unknown, not as idle, and the source's own count of running
 * pumps is a separate reading on the drawing that this does not replace.
 */
function Legend({ data }: Readonly<{ data: MimicData }>) {
  const counts: Record<string, number> = {};
  for (const bay of data.bays) {
    const key = LEGEND.includes(bay.state) ? bay.state : 'unknown';
    counts[key] = (counts[key] ?? 0) + 1;
  }
  const shown = LEGEND.filter(
    (state) => counts[state] || state === 'running' || state === 'idle'
  );

  return (
    <Group gap='lg' justify='center' mt='xs'>
      {shown.map((state) => {
        const style = stateStyle(state);
        return (
          <Group key={state} gap={6} wrap='nowrap' data-legend={state}>
            <Box
              w={12}
              h={12}
              style={{
                borderRadius: '50%',
                background: style.fill,
                border: `1.6px ${style.dashed ? 'dashed' : 'solid'} ${style.line}`
              }}
            />
            <Text size='xs' c='dimmed'>
              {stateLabel(state)} {counts[state] ?? 0}
            </Text>
          </Group>
        );
      })}
    </Group>
  );
}

function TotalCard({ name, total }: Readonly<{ name: string; total: Total }>) {
  return (
    <Paper withBorder p='sm' radius='md' miw={170}>
      <Text size='xs' c='dimmed' tt='uppercase' fw={600}>
        {name === 'power'
          ? t`Plant power`
          : name === 'flow'
            ? t`Plant flow`
            : name}
      </Text>
      <Text size='xl' fw={700} lh={1.3}>
        {total.value === null ? (
          <Text span c='dimmed' fs='italic' size='md' fw={400}>
            {t`Unavailable`}
          </Text>
        ) : (
          <>
            {numberText(total.value)}{' '}
            <Text span size='sm' c='dimmed' fw={500}>
              {total.unit}
            </Text>
          </>
        )}
      </Text>
      <Group gap='xs' mt={4}>
        <Badge variant='outline' size='sm' color='gray'>
          {total.derived ? t`Derived total` : t`Source measurement`}
        </Badge>
        {total.reason && <Text size='xs'>{reasonLabel(total.reason)}</Text>}
      </Group>
    </Paper>
  );
}

export default function PumphouseMimic({ stationId }: { stationId: number }) {
  const [unit, setUnit] = useState<string | null>(null);
  const { ref, query } = useStationMimic(stationId, unit);
  const data = query.data;
  const display = displayFor(data, unit);
  const stationPoints = Object.values(data?.station_points ?? {});
  const bay = data?.bays.find((b) => b.key === unit);
  const select = (key: string) =>
    setUnit((current) => (current === key ? null : key));

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
        <Stack>
          <Alert color='red'>{t`Live readings are unavailable. Refresh to retry; cached values are hidden.`}</Alert>
          {unit && (
            <Button
              variant='default'
              onClick={() => setUnit(null)}
            >{t`Overview only`}</Button>
          )}
        </Stack>
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
          <Paper withBorder radius='md' p='sm' bg={PANEL}>
            <MimicSchematic data={data} selected={unit} onSelect={select} />
            {!!data.bays.length && <Legend data={data} />}
          </Paper>
          {!data.bays.length && (
            <Alert>{t`No pump slots are registered for this station.`}</Alert>
          )}
          <Group align='stretch'>
            {Object.entries(data.totals).map(([name, total]) => (
              <TotalCard key={name} name={name} total={total} />
            ))}
          </Group>
          {!!stationPoints.length && (
            <ReadingSections
              display={display}
              sections={[
                {
                  key: 'station',
                  title: t`Station readings`,
                  points: stationPoints
                }
              ]}
            />
          )}
          {unit && data.selected_unit === unit ? (
            <UnitMimic
              key={unit}
              data={data}
              unit={unit}
              actions={
                <>
                  {bay?.machine && (
                    <Anchor
                      component={Link}
                      to={`/machines/machine/${bay.machine}/performance`}
                      size='sm'
                    >
                      {t`Open pump page`}
                    </Anchor>
                  )}
                  <Button
                    variant='subtle'
                    onClick={() => setUnit(null)}
                  >{t`Overview only`}</Button>
                </>
              }
            />
          ) : (
            <Text c='dimmed'>{t`Select a pump bay to view its instruments and readings.`}</Text>
          )}
          <Title order={4}>{t`Threshold alarms`}</Title>
          {data.alarms.length ? (
            <PointTable points={data.alarms} display={display} />
          ) : (
            <Text>{t`No active alarms from configured thresholds.`}</Text>
          )}
          {!!data.unconfigured_thresholds && (
            <Text c='dimmed' size='sm'>
              {t`Points without configured thresholds`}:{' '}
              {data.unconfigured_thresholds}
            </Text>
          )}
        </>
      )}
    </Stack>
  );
}
