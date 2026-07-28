import type {
  IColumnConfig,
  ILink,
  IScaleConfig,
  ITask
} from '@svar-ui/react-gantt';
import { Gantt, Willow, WillowDark } from '@svar-ui/react-gantt';
import '@svar-ui/react-gantt/all.css';

import type { WorkOrder } from '@lib/types/Tasks';
import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  useMantineColorScheme
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconChevronLeft,
  IconChevronRight,
  IconSearch
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useScheduleWindow } from '../../hooks/UseScheduleWindow';
import './TaskGantt.css';

type Zoom = 'day' | 'week' | 'month';
type OrderBy = 'machine' | 'assignee';

interface TimelineTask extends ITask {
  id: number;
  text: string;
  title: string;
  reference: string;
  statusLabel: string;
  lifecycleLabel: string;
  machineLabel: string;
  assigneeLabel: string;
  durationLabel: string;
  priority: string;
  start: Date;
  end: Date;
  progress: number;
}

const RANGE_DAYS: Record<Zoom, number> = {
  day: 21,
  week: 84,
  month: 200
};

const CELL_WIDTH: Record<Zoom, number> = {
  day: 58,
  week: 96,
  month: 110
};

const STATUS_PROGRESS: Record<string, number> = {
  backlog: 10,
  'in-progress': 55,
  review: 85,
  done: 100
};

const LINK_TYPES: Record<string, ILink['type']> = {
  FS: 'e2s',
  SS: 's2s',
  FF: 'e2e',
  SF: 's2e'
};

const TASK_TYPES = [
  { id: 'stage-backlog', label: 'Backlog' },
  { id: 'stage-in-progress', label: 'In Progress' },
  { id: 'stage-review', label: 'In Review' },
  { id: 'stage-done', label: 'Done' }
];

/**
 * Operational work-order timeline powered by SVAR Gantt.
 *
 * The grid keeps task context visible without hover, while the chart preserves
 * hierarchy and dependencies. Scheduling mutations remain governed by AIMMS;
 * SVAR is deliberately read-only and selection opens the complete work order.
 */
export default function TaskGantt() {
  const navigate = useNavigate();
  const { colorScheme } = useMantineColorScheme();
  const [zoom, setZoom] = useState<Zoom>('week');
  const [orderBy, setOrderBy] = useState<OrderBy>('machine');
  const [search, setSearch] = useState('');
  const [rangeStart, setRangeStart] = useState<Date>(() =>
    dayjs().subtract(3, 'day').startOf('day').toDate()
  );

  const days = RANGE_DAYS[zoom];
  const rangeEnd = useMemo(
    () => dayjs(rangeStart).add(days, 'day').endOf('day').toDate(),
    [rangeStart, days]
  );
  const query = useScheduleWindow(rangeStart, rangeEnd);
  const data = query.data;

  const visibleCards = useMemo(() => {
    const scheduled = (data?.cards ?? []).filter(
      (card) => card.scheduled_start && card.scheduled_end
    );
    const term = search.trim().toLocaleLowerCase();
    const filtered = term
      ? scheduled.filter((card) =>
          [
            card.reference,
            card.title,
            card.description,
            card.machine_name,
            card.assigned_to_name,
            card.status,
            card.lifecycle_status
          ]
            .filter(Boolean)
            .some((value) => String(value).toLocaleLowerCase().includes(term))
        )
      : scheduled;

    return orderCards(filtered, orderBy);
  }, [data?.cards, orderBy, search]);

  const tasks = useMemo<TimelineTask[]>(
    () => visibleCards.map(toTimelineTask),
    [visibleCards]
  );

  const links = useMemo<ILink[]>(() => {
    const ids = new Set(tasks.map((task) => task.id));
    return (data?.dependencies ?? [])
      .filter(
        (dependency) =>
          ids.has(dependency.from_card) && ids.has(dependency.to_card)
      )
      .map((dependency) => ({
        id: dependency.id,
        source: dependency.from_card,
        target: dependency.to_card,
        type: LINK_TYPES[dependency.dependency_type] ?? 'e2s',
        lag: dependency.lag_minutes
      }));
  }, [data?.dependencies, tasks]);

  const scales = useMemo(() => scaleConfig(zoom), [zoom]);
  const columns = useMemo<IColumnConfig[]>(
    () => [
      { id: 'text', header: t`Work order / task`, flexgrow: 2, resize: true },
      { id: 'statusLabel', header: t`Stage`, width: 108, resize: true },
      { id: 'machineLabel', header: t`Machine`, width: 180, resize: true },
      { id: 'assigneeLabel', header: t`Assignee`, width: 130, resize: true },
      {
        id: 'start',
        header: t`Start`,
        width: 118,
        align: 'center',
        template: (value) => dayjs(value as Date).format('DD MMM, HH:mm')
      },
      {
        id: 'durationLabel',
        header: t`Effort`,
        width: 78,
        align: 'center'
      }
    ],
    []
  );

  const Theme = colorScheme === 'dark' ? WillowDark : Willow;
  const conflictCount = data?.warnings.length ?? 0;

  const shiftRange = (direction: number) =>
    setRangeStart(
      dayjs(rangeStart)
        .add(direction * Math.round(days / 2), 'day')
        .startOf('day')
        .toDate()
    );

  return (
    <Stack gap='sm'>
      <Group justify='space-between' align='end' wrap='wrap' gap='sm'>
        <Group gap='xs'>
          <ActionIcon
            variant='default'
            aria-label='gantt-prev'
            onClick={() => shiftRange(-1)}
          >
            <IconChevronLeft size={16} />
          </ActionIcon>
          <ActionIcon
            variant='default'
            aria-label='gantt-today'
            onClick={() =>
              setRangeStart(dayjs().subtract(3, 'day').startOf('day').toDate())
            }
          >
            <Text size='xs' fw={600}>{t`Today`}</Text>
          </ActionIcon>
          <ActionIcon
            variant='default'
            aria-label='gantt-next'
            onClick={() => shiftRange(1)}
          >
            <IconChevronRight size={16} />
          </ActionIcon>
          <Text size='sm' fw={500}>
            {dayjs(rangeStart).format('DD MMM')} –{' '}
            {dayjs(rangeEnd).format('DD MMM YYYY')}
          </Text>
        </Group>

        <Group gap='sm' wrap='wrap'>
          <TextInput
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            leftSection={<IconSearch size={14} />}
            placeholder={t`Filter work orders`}
            aria-label='gantt-search'
            size='xs'
            w={220}
          />
          <SegmentedControl
            size='xs'
            value={orderBy}
            onChange={(value) => setOrderBy(value as OrderBy)}
            data={[
              { value: 'machine', label: t`By machine` },
              { value: 'assignee', label: t`By assignee` }
            ]}
          />
          <SegmentedControl
            size='xs'
            value={zoom}
            onChange={(value) => setZoom(value as Zoom)}
            data={[
              { value: 'day', label: t`Day` },
              { value: 'week', label: t`Week` },
              { value: 'month', label: t`Month` }
            ]}
          />
        </Group>
      </Group>

      <Group gap='xs'>
        <Badge color='gray' variant='light'>{t`Backlog`}</Badge>
        <Badge color='indigo' variant='light'>{t`In Progress`}</Badge>
        <Badge color='yellow' variant='light'>{t`In Review`}</Badge>
        <Badge color='green' variant='light'>{t`Done`}</Badge>
        <Text size='xs' c='dimmed'>
          {tasks.length} {t`scheduled`} · {links.length} {t`dependencies`}
        </Text>
      </Group>

      {conflictCount > 0 && (
        <Alert
          color='orange'
          variant='light'
          icon={<IconAlertTriangle size={16} />}
          title={t`Scheduling conflicts`}
          p='xs'
        >
          <Text size='xs'>
            {t`${conflictCount} overlap warning(s) in this window.`}
          </Text>
        </Alert>
      )}

      {query.isLoading ? (
        <Group justify='center' p='xl'>
          <Loader />
        </Group>
      ) : tasks.length === 0 ? (
        <Paper withBorder p='xl'>
          <Text
            c='dimmed'
            ta='center'
          >{t`No scheduled work orders in this window.`}</Text>
        </Paper>
      ) : (
        <Paper withBorder className='aimms-gantt-shell'>
          <div className='aimms-gantt-chart'>
            <Theme fonts={false}>
              <Gantt
                readonly
                tasks={tasks}
                links={links}
                taskTypes={TASK_TYPES}
                taskTemplate={TaskBarContent}
                columns={columns}
                scales={scales}
                start={rangeStart}
                end={rangeEnd}
                autoScale={false}
                durationUnit='hour'
                lengthUnit='day'
                cellWidth={CELL_WIDTH[zoom]}
                cellHeight={44}
                scaleHeight={34}
                gridWidth={720}
                cellBorders='full'
                markers={[
                  {
                    start: new Date(),
                    text: t`Today`,
                    css: 'aimms-today-marker'
                  }
                ]}
                highlightTime={(date, unit) =>
                  unit === 'day' && [0, 6].includes(date.getDay())
                    ? 'aimms-weekend'
                    : ''
                }
                onSelectTask={({ id }) =>
                  navigate(`/maintenance/work-orders/${id}/`)
                }
              />
            </Theme>
          </div>
        </Paper>
      )}
    </Stack>
  );
}

function TaskBarContent({ data }: Readonly<{ data: ITask }>) {
  const task = data as TimelineTask;
  return (
    <div className='aimms-gantt-bar-content'>
      <span className='aimms-gantt-bar-reference'>{task.reference}</span>
      <span className='aimms-gantt-bar-title'>{task.title}</span>
      <span className='aimms-gantt-bar-stage'>{task.statusLabel}</span>
    </div>
  );
}

function toTimelineTask(card: WorkOrder): TimelineTask {
  const start = new Date(card.scheduled_start as string);
  const end = new Date(card.scheduled_end as string);
  const reference = card.reference ?? `WO-${card.id}`;
  return {
    id: card.id,
    text: `${reference} · ${card.title}`,
    title: card.title,
    reference,
    details: card.description,
    start,
    end,
    duration:
      (card.estimated_minutes ??
        Math.max(dayjs(end).diff(start, 'minute'), 60)) / 60,
    durationLabel: formatMinutes(card.estimated_minutes),
    progress: STATUS_PROGRESS[card.status] ?? 0,
    type: `stage-${card.status}`,
    statusLabel: humanize(card.status),
    lifecycleLabel: humanize(card.lifecycle_status),
    machineLabel: card.machine_name ?? '—',
    assigneeLabel: card.assigned_to_name ?? card.assignee ?? '—',
    priority: card.priority
  };
}

function orderCards(cards: WorkOrder[], orderBy: OrderBy): WorkOrder[] {
  const compare = (left: WorkOrder, right: WorkOrder) => {
    const leftValue =
      orderBy === 'machine'
        ? (left.machine_name ?? '')
        : (left.assigned_to_name ?? left.assignee ?? '');
    const rightValue =
      orderBy === 'machine'
        ? (right.machine_name ?? '')
        : (right.assigned_to_name ?? right.assignee ?? '');
    return (
      leftValue.localeCompare(rightValue) ||
      left.title.localeCompare(right.title)
    );
  };
  return [...cards].sort(compare);
}

function scaleConfig(zoom: Zoom): IScaleConfig[] {
  if (zoom === 'day') {
    return [
      {
        unit: 'month',
        step: 1,
        format: (date) => dayjs(date).format('MMMM YYYY')
      },
      { unit: 'day', step: 1, format: (date) => dayjs(date).format('DD ddd') }
    ];
  }
  if (zoom === 'month') {
    return [
      { unit: 'year', step: 1, format: (date) => dayjs(date).format('YYYY') },
      { unit: 'month', step: 1, format: (date) => dayjs(date).format('MMM') }
    ];
  }
  return [
    {
      unit: 'month',
      step: 1,
      format: (date) => dayjs(date).format('MMMM YYYY')
    },
    { unit: 'week', step: 1, format: (date) => dayjs(date).format('DD MMM') }
  ];
}

function humanize(value: string): string {
  return value
    .replaceAll('_', ' ')
    .replaceAll('-', ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatMinutes(minutes: number | null): string {
  if (!minutes) return '—';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} h ${remainder} min` : `${hours} h`;
}
