import type { AllocationStatus, KanbanPriority } from '@lib/types/Tasks';
import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Anchor,
  Badge,
  Card,
  Group,
  Loader,
  Menu,
  Select,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import { IconDotsVertical, IconPencil, IconTrash } from '@tabler/icons-react';
import dayjs from 'dayjs';
import { type DragEvent, useRef } from 'react';
import { Link } from 'react-router-dom';
import classes from './BoardTaskCard.module.css';

interface CardData {
  id: number;
  reference: string | null;
  title: string;
  description: string;
  status: string;
  priority: KanbanPriority;
  dueDate: string | null;
  assignee: string;
  machineName: string | null;
  tags: string[];
  company: string;
  companyContactName: string;
  companyContactPhone: string;
  jobNumber: string;
  serviceQuote: string;
  parts: {
    partId: number;
    partName: string;
    quantity: number;
    allocatedQuantity?: number;
    allocationStatus?: AllocationStatus;
    allocationNote?: string;
  }[];
}

export function BoardTaskCard({
  task,
  density,
  href,
  columns,
  deleting,
  updating,
  draggable,
  dragging,
  onEdit,
  onDelete,
  onStatusChange,
  onDragStart,
  onDragEnd
}: {
  task: CardData;
  density: 'collapsed' | 'expanded';
  href: string;
  columns: { value: string; label: string }[];
  deleting: boolean;
  updating: boolean;
  draggable: boolean;
  dragging: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onStatusChange: (value: string) => void;
  onDragStart: (event: DragEvent<HTMLDivElement>) => void;
  onDragEnd: () => void;
}) {
  const suppressClickUntil = useRef(0);
  const collapsed = density === 'collapsed';
  const overdue = task.dueDate && dayjs(task.dueDate).isBefore(dayjs(), 'day');
  const priorityLabel =
    task.priority === 'high'
      ? t`High`
      : task.priority === 'low'
        ? t`Low`
        : t`Medium`;
  return (
    <Card
      withBorder
      radius='md'
      p={collapsed ? 'sm' : 'md'}
      className={classes.card}
      data-testid='maintenance-card'
      data-density={density}
      draggable={draggable}
      onDragStart={(event) => {
        suppressClickUntil.current = Number.POSITIVE_INFINITY;
        onDragStart(event);
      }}
      onDragEnd={() => {
        suppressClickUntil.current = Date.now() + 250;
        onDragEnd();
      }}
      style={{ opacity: dragging ? 0.4 : 1 }}
    >
      <Stack gap={collapsed ? 'xs' : 'sm'}>
        <Group
          justify='space-between'
          align='flex-start'
          wrap='nowrap'
          gap='xs'
        >
          <Stack gap={2} style={{ minWidth: 0 }}>
            {task.reference && (
              <Text size='xs' c='dimmed'>
                {task.reference}
              </Text>
            )}
            <Anchor
              component={Link}
              to={href}
              fw={600}
              className={`${classes.primaryLink} ${collapsed ? classes.collapsedLink : ''}`}
              draggable={false}
              aria-label={t`Open work order: ${task.title}`}
              onClick={(event) => {
                if (event.detail > 0 && Date.now() < suppressClickUntil.current)
                  event.preventDefault();
              }}
            >
              {task.title}
            </Anchor>
          </Stack>
          <Group gap={4} wrap='nowrap' className={classes.actions}>
            {!collapsed && (
              <ActionIcon
                variant='subtle'
                aria-label={t`Edit work order`}
                onClick={onEdit}
                disabled={deleting}
              >
                <IconPencil size={16} />
              </ActionIcon>
            )}
            <Menu withinPortal position='bottom-end'>
              <Menu.Target>
                <ActionIcon
                  variant='subtle'
                  aria-label={t`Actions for ${task.title}`}
                  disabled={deleting || updating}
                >
                  <IconDotsVertical size={16} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item
                  component={Link}
                  to={href}
                >{t`Open work order detail`}</Menu.Item>
                <Menu.Item
                  leftSection={<IconPencil size={16} />}
                  onClick={onEdit}
                >{t`Edit work order`}</Menu.Item>
                <Menu.Label>{t`Move to`}</Menu.Label>
                {columns.map((column) => (
                  <Menu.Item
                    key={column.value}
                    disabled={column.value === task.status}
                    onClick={() => onStatusChange(column.value)}
                  >
                    {column.label}
                  </Menu.Item>
                ))}
                <Menu.Divider />
                <Menu.Item
                  color='red'
                  leftSection={<IconTrash size={16} />}
                  onClick={onDelete}
                >{t`Delete work order`}</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
        {task.machineName && (
          <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
            {t`Machine`}: {task.machineName}
          </Text>
        )}
        <Group gap={6}>
          <Badge
            variant='light'
            color={
              task.priority === 'high'
                ? 'red'
                : task.priority === 'low'
                  ? 'teal'
                  : 'yellow'
            }
          >
            {priorityLabel}
          </Badge>
          {task.dueDate && (
            <Badge variant='light' color={overdue ? 'red' : 'blue'}>
              {overdue ? t`Overdue` : t`Due`}:{' '}
              {dayjs(task.dueDate).format('MMM D')}
            </Badge>
          )}
          {(updating || deleting) && (
            <Loader size='xs' aria-label={t`Updating work order`} />
          )}
        </Group>
        <Text size='xs' c='dimmed'>
          {task.assignee || t`Unassigned`}
        </Text>
        {!collapsed && (
          <>
            {task.description && (
              <Text size='sm' c='dimmed' lineClamp={3}>
                {task.description}
              </Text>
            )}
            {task.tags.length > 0 && (
              <Group gap={4}>
                {task.tags.map((tag) => (
                  <Badge key={tag} color='gray' variant='outline'>
                    {tag}
                  </Badge>
                ))}
              </Group>
            )}
            <Stack gap={2}>
              {task.company && (
                <Text size='sm'>
                  {t`Company`}: {task.company}
                </Text>
              )}
              {task.companyContactName && (
                <Text size='sm' c='dimmed'>
                  {t`Contact`}: {task.companyContactName}
                  {task.companyContactPhone
                    ? ` • ${task.companyContactPhone}`
                    : ''}
                </Text>
              )}
              {task.jobNumber && (
                <Text size='sm' c='dimmed'>
                  {t`Job`}: {task.jobNumber}
                </Text>
              )}
              {task.serviceQuote && (
                <Text size='sm' c='dimmed'>
                  {t`Service quote`}: {task.serviceQuote}
                </Text>
              )}
            </Stack>
            {task.parts.length > 0 && (
              <Stack gap={4}>
                <Text size='xs' fw={500} c='dimmed'>{t`Parts`}</Text>
                {task.parts.map((part) => (
                  <Group key={part.partId} gap={4}>
                    <Text size='xs' style={{ flex: 1 }} lineClamp={1}>
                      {part.partName}
                    </Text>
                    <Tooltip
                      label={part.allocationNote ?? ''}
                      disabled={!part.allocationNote}
                    >
                      <Badge
                        size='xs'
                        variant='light'
                        color={
                          {
                            full: 'green',
                            partial: 'yellow',
                            insufficient: 'red',
                            none: 'gray'
                          }[part.allocationStatus ?? 'none']
                        }
                      >
                        {part.allocatedQuantity ?? 0}/{part.quantity}
                      </Badge>
                    </Tooltip>
                  </Group>
                ))}
              </Stack>
            )}
            <Select
              size='xs'
              label={t`Status`}
              data={columns}
              value={task.status}
              onChange={(value) => value && onStatusChange(value)}
              disabled={updating || deleting}
            />
          </>
        )}
      </Stack>
    </Card>
  );
}
