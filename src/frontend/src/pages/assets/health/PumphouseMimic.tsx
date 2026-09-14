import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Stack,
  Table,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import { useDocumentVisibility, useInViewport } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../../App';
import pumpUnit from '../../../assets/mimic/pump-unit.svg';
import overview from '../../../assets/mimic/pumphouse-overview.svg';

export type MimicPoint = {
  pointer: string;
  label: string;
  group: string;
  value: string | number | boolean | null;
  unit: string;
  quality: string;
  observed_at: string | null;
  age_seconds: number | null;
  reason: string | null;
  condition: string;
  thresholds_configured: boolean;
};
type LayoutElement = {
  id: string;
  pointer: string;
  view: string;
  label: string;
  role: string;
  x: number;
  y: number;
};
type Bay = {
  key: string;
  name: string;
  active: boolean;
  state: string;
  points: Record<string, MimicPoint>;
};
type Total = {
  value: number | null;
  unit: string;
  derived: boolean;
  reason: string | null;
  contributors: string[];
};
export type MimicData = {
  station: number;
  name: string;
  generated_at: string;
  enabled: boolean;
  source: { pk: number; name: string } | null;
  last_poll_at: string | null;
  last_error_code: string;
  layout: { version: number; review_status: string; elements: LayoutElement[] };
  station_points: Record<string, MimicPoint>;
  bays: Bay[];
  selected_unit: string | null;
  points: Record<string, MimicPoint>;
  totals: Record<string, Total>;
  alarms: MimicPoint[];
  unconfigured_thresholds: number;
};

export function reasonLabel(reason: string | null): string {
  const labels: Record<string, string> = {
    disabled: t`Polling disabled`,
    not_bound: t`Not bound`,
    not_approved: t`Not approved`,
    no_data: t`No reading`,
    stale: t`Stale`,
    bad_quality: t`Unusable quality`,
    clock_skew: t`Source clock is ahead`,
    mapping_changed: t`Mapping changed`,
    type_mismatch: t`Unexpected value type`,
    inactive_equipment: t`Inactive equipment`,
    incomplete: t`Missing contributors`,
    unconfirmed_unit: t`Unit needs review`,
    incompatible_unit: t`Incompatible units`
  };
  return reason ? (labels[reason] ?? t`Unavailable`) : '';
}

function stateLabel(state: string) {
  const labels: Record<string, string> = {
    running: t`Running`,
    idle: t`Idle`,
    fault: t`Fault`,
    stale: t`Stale`,
    not_bound: t`Not bound`,
    unknown: t`Unknown`
  };
  return labels[state] ?? t`Unknown`;
}

function valueText(point?: MimicPoint) {
  if (!point || point.value === null || point.reason) return t`Unavailable`;
  if (typeof point.value === 'boolean') return point.value ? t`Yes` : t`No`;
  return `${typeof point.value === 'number' ? point.value.toLocaleString(undefined, { maximumFractionDigits: 3 }) : point.value}${point.unit ? ` ${point.unit}` : ''}`;
}

function elementLabel(element: LayoutElement) {
  const labels: Record<string, string> = {
    forebay: t`Forebay level`,
    'station-status': t`Station status`,
    'pump-status': t`Pump status`,
    'pump-power': t`Input power`,
    'pump-flow': t`Discharge flow`
  };
  return labels[element.id] ?? element.label;
}

function Diagram({ data, unit }: { data: MimicData; unit?: string }) {
  const points = unit ? data.points : data.station_points;
  const view = unit ? 'unit' : 'station';
  const height = unit ? 330 : 180;
  return (
    <svg
      viewBox={`0 0 600 ${height}`}
      role='img'
      aria-label={unit ? t`Pump unit schematic` : t`Station schematic`}
      style={{ width: '100%', maxHeight: unit ? 380 : 220 }}
    >
      <rect width={600} height={height} fill='#f1f3f5' rx={8} />
      <image href={unit ? pumpUnit : overview} width={600} height={height} />
      {data.layout.elements
        .filter((e) => e.view === view)
        .map((element) => {
          const pointer = element.pointer.replace(
            '{pump}',
            (unit ?? '').replaceAll('~', '~0').replaceAll('/', '~1')
          );
          const point = points[pointer];
          return (
            <g key={element.id} data-point={pointer}>
              <title>
                {elementLabel(element)}: {valueText(point)}{' '}
                {reasonLabel(point?.reason ?? 'not_bound')}
              </title>
              <rect
                x={element.x - 60}
                y={element.y - 22}
                width={180}
                height={42}
                rx={4}
                fill='white'
                stroke={point?.reason ? '#868e96' : '#495057'}
                strokeDasharray={point?.reason ? '4 3' : undefined}
              />
              <text
                x={element.x - 55}
                y={element.y - 7}
                fontSize={11}
                fill='#495057'
              >
                {elementLabel(element)}
              </text>
              <text
                x={element.x - 55}
                y={element.y + 10}
                fontSize={12}
                fill='#212529'
              >
                {valueText(point)}
              </text>
            </g>
          );
        })}
    </svg>
  );
}

function PointTable({ points }: { points: MimicPoint[] }) {
  return (
    <Table.ScrollContainer minWidth={650}>
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Signal`}</Table.Th>
            <Table.Th>{t`Value`}</Table.Th>
            <Table.Th>{t`Quality`}</Table.Th>
            <Table.Th>{t`Observed at`}</Table.Th>
            <Table.Th>{t`Condition`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {points.map((point) => (
            <Table.Tr key={point.pointer} data-point={point.pointer}>
              <Table.Td>
                {point.label}
                <Text size='xs' c='dimmed'>
                  {point.pointer}
                </Text>
              </Table.Td>
              <Table.Td>
                {valueText(point)}
                {point.reason && (
                  <Text size='xs'>{reasonLabel(point.reason)}</Text>
                )}
              </Table.Td>
              <Table.Td>
                {point.quality === 'good' ? t`Good` : t`Unusable or unknown`}
              </Table.Td>
              <Table.Td>
                {point.observed_at
                  ? new Date(point.observed_at).toLocaleString()
                  : t`No reading`}
                {point.age_seconds !== null && (
                  <Text size='xs'>
                    {t`Age at response`}: {Math.round(point.age_seconds)}{' '}
                    {t`seconds`}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                {!point.thresholds_configured
                  ? t`No threshold configured`
                  : point.reason
                    ? t`Unknown`
                    : point.condition === 'critical'
                      ? t`Critical`
                      : point.condition === 'warning'
                        ? t`Warning`
                        : point.condition === 'normal'
                          ? t`Normal`
                          : t`Unknown`}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export default function PumphouseMimic({ stationId }: { stationId: number }) {
  const [unit, setUnit] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const visibility = useDocumentVisibility();
  const { ref, inViewport } = useInViewport<HTMLDivElement>();
  const query = useQuery({
    queryKey: ['station-mimic', stationId, unit],
    enabled: inViewport && visibility === 'visible',
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
    retry: false,
    queryFn: async () =>
      (
        await api.get<MimicData>(
          `/api/machine-health/station/${stationId}/mimic/`,
          { params: unit ? { unit } : {}, timeout: 10000 }
        )
      ).data
  });
  const data = query.data;
  const groups: Record<string, MimicPoint[]> = {};
  for (const point of Object.values(data?.points ?? {})) {
    if (
      !`${point.label} ${point.pointer}`
        .toLowerCase()
        .includes(search.toLowerCase())
    )
      continue;
    const group = point.group || t`Other reviewed points`;
    groups[group] ??= [];
    groups[group].push(point);
  }
  return (
    <Stack ref={ref} gap='md' mih={160}>
      <Group justify='space-between'>
        <Title order={3}>{t`Pumphouse overview`}</Title>
        <Button
          variant='default'
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
          <Group>
            <Text size='sm'>
              {t`Source`}: {data.source?.name ?? t`Not bound`}
            </Text>
            <Text size='sm'>
              {t`Last poll`}:{' '}
              {data.last_poll_at
                ? new Date(data.last_poll_at).toLocaleString()
                : t`Not polled yet`}
            </Text>
            <Text size='sm'>
              {t`Response time`}: {new Date(data.generated_at).toLocaleString()}
            </Text>
          </Group>
          <Diagram data={data} />
          <Group>
            {Object.entries(data.totals).map(([name, total]) => (
              <Paper key={name} withBorder p='sm'>
                <Text fw={600}>
                  {name === 'power'
                    ? t`Plant power`
                    : name === 'flow'
                      ? t`Plant flow`
                      : name}
                </Text>
                <Text>
                  {total.value === null
                    ? t`Unavailable`
                    : `${total.value.toLocaleString()} ${total.unit}`}
                </Text>
                <Badge variant='outline'>
                  {total.derived ? t`Derived total` : t`Source measurement`}
                </Badge>
                {total.reason && (
                  <Text size='xs'>{reasonLabel(total.reason)}</Text>
                )}
              </Paper>
            ))}
          </Group>
          <Group align='stretch'>
            {data.bays.map((bay) => (
              <Button
                key={bay.key}
                variant={unit === bay.key ? 'filled' : 'outline'}
                color={
                  bay.state === 'running'
                    ? 'green'
                    : bay.state === 'fault'
                      ? 'red'
                      : bay.state === 'idle'
                        ? 'yellow'
                        : 'gray'
                }
                onClick={() => {
                  setUnit(bay.key);
                  setSearch('');
                }}
                aria-pressed={unit === bay.key}
                aria-label={`${bay.key}: ${stateLabel(bay.state)}`}
                h='auto'
                py='sm'
              >
                <Stack gap={2}>
                  <Text>
                    {bay.key}{' '}
                    {bay.state === 'running'
                      ? '▶'
                      : bay.state === 'idle'
                        ? 'Ⅱ'
                        : bay.state === 'fault'
                          ? '!'
                          : '?'}
                  </Text>
                  <Text size='xs'>{stateLabel(bay.state)}</Text>
                  {!bay.active && (
                    <Text size='xs'>{t`Inactive equipment`}</Text>
                  )}
                </Stack>
              </Button>
            ))}
          </Group>
          {!data.bays.length && (
            <Alert>{t`No pump slots are registered for this station.`}</Alert>
          )}
          <Title order={4}>{t`Station readings`}</Title>
          <PointTable points={Object.values(data.station_points)} />
          {unit && data.selected_unit === unit ? (
            <>
              <Group>
                <Title order={4}>
                  {t`Pump unit`}: {unit}
                </Title>
                <Button
                  variant='subtle'
                  onClick={() => setUnit(null)}
                >{t`Overview only`}</Button>
              </Group>
              <Diagram data={data} unit={unit} />
              <TextInput
                label={t`Filter unit readings`}
                value={search}
                onChange={(event) => setSearch(event.currentTarget.value)}
              />
              {Object.entries(groups).map(([name, points]) => (
                <Stack key={name} gap='xs'>
                  <Title order={5}>{name}</Title>
                  <PointTable points={points} />
                </Stack>
              ))}
              {!Object.keys(groups).length && (
                <Text>{t`No matching readings.`}</Text>
              )}
            </>
          ) : (
            <Text>{t`Select a pump bay to view its instruments and readings.`}</Text>
          )}
          <Title order={4}>{t`Threshold alarms`}</Title>
          {data.alarms.length ? (
            <PointTable points={data.alarms} />
          ) : (
            <Text>{t`No active alarms from configured thresholds.`}</Text>
          )}
          {!!data.unconfigured_thresholds && (
            <Text c='dimmed'>
              {t`Points without configured thresholds`}:{' '}
              {data.unconfigured_thresholds}
            </Text>
          )}
        </>
      )}
    </Stack>
  );
}
