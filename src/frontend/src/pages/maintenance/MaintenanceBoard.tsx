import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  CloseButton,
  Group,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea,
  Tooltip
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconArrowLeft,
  IconArrowRight,
  IconArrowsSort,
  IconCircleCheck,
  IconDeviceFloppy,
  IconExternalLink,
  IconPencil,
  IconPlus,
  IconTrash,
  IconX
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  type DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useState
} from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  AllocationStatus,
  BoardCard,
  KanbanColumnRecord,
  KanbanPriority,
  KanbanStatus,
  WorkOrder,
  WorkOrderPart
} from '@lib/types/Tasks';

import { ScopedChatButton } from '../../components/aichat/ScopedChatButton';
import { useApi } from '../../contexts/ApiContext';
import { showApiErrorMessage } from '../../functions/notifications';
import {
  WorkOrderCreateModal,
  type WorkPackageResult
} from './components/WorkOrderCreateModal';

type PriorityFilterValue = KanbanPriority | 'all';

/** Local part attached to a card (for the form). */
interface FormPart {
  partId: number;
  partName: string;
  quantity: number;
  /** Set after saving — from the backend. */
  allocationStatus?: AllocationStatus;
  allocatedQuantity?: number;
  allocationNote?: string;
}

interface Task {
  /** The board card. One job may put several of these on the board. */
  id: number;
  /** The job the card belongs to; work-order edits address this, not `id`. */
  workOrderId: number;
  /** What kind of piece this card tracks: the job itself, a subtask, sourcing. */
  cardKind: string;
  title: string;
  description: string;
  status: KanbanStatus;
  priority: KanbanPriority;
  dueDate: string | null;
  assignee: string;
  machine: number | null;
  machineName: string | null;
  tags: string[];
  company: string;
  companyContactName: string;
  companyContactPhone: string;
  jobNumber: string;
  serviceQuote: string;
  createdAt: string;
  updatedAt: string;
  parts: FormPart[];
}

interface Column {
  id: string;
  label: string;
  color: string;
}

interface TaskFormValues {
  title: string;
  description: string;
  status: KanbanStatus;
  priority: KanbanPriority;
  assignee: string;
  machine: string | null;
  tags: string[];
  dueDate: Date | null;
  company: string;
  companyContactName: string;
  companyContactPhone: string;
  jobNumber: string;
  serviceQuote: string;
}

interface ColumnFormValues {
  label: string;
  color: string;
}

interface Filters {
  search: string;
  column: string;
  priority: PriorityFilterValue;
  tags: string[];
  assignee: string;
  jobNumber: string;
  serviceQuote: string;
}

interface ColumnDeletionContext {
  column: Column;
  fallbackColumn: Column;
}

const DEFAULT_TAG_OPTIONS = [
  'Service orders',
  'Purchase orders',
  'Sales orders',
  'Miscellaneous'
];

const priorityColors: Record<KanbanPriority, string> = {
  low: 'teal',
  medium: 'yellow',
  high: 'red'
};

const colorOptions = [
  'gray',
  'blue',
  'indigo',
  'violet',
  'teal',
  'green',
  'orange',
  'red'
];

const generateId = () => Math.random().toString(36).slice(2, 10);

const slugify = (value: string) => {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)+/g, '');

  return slug.length > 0 ? slug : `column-${generateId()}`;
};

const getDueBadgeColor = (dueDate: string) => {
  const due = dayjs(dueDate);

  if (due.isBefore(dayjs(), 'day')) {
    return 'red';
  }

  if (due.diff(dayjs(), 'day') <= 2) {
    return 'orange';
  }

  return 'blue';
};

/**
 * Build a board item from a card and the job it belongs to.
 *
 * The card decides what the board shows and where: its title, its column, its
 * own schedule. Everything the card does not answer for - priority, machine,
 * required parts, customer detail - is read from the work order, because those
 * describe the job rather than this piece of it.
 *
 * A job with no card of its own cannot happen (the model creates one), but the
 * work order is optional here so a card that arrives before its job has loaded
 * still renders rather than blanking the board.
 */
const convertCardToTask = (
  card: BoardCard,
  workOrder: WorkOrder | undefined
): Task => ({
  id: card.id,
  workOrderId: card.work_order,
  cardKind: card.card_kind,
  title: card.title,
  description: card.description ?? '',
  status: card.status as KanbanStatus,
  priority: (card.priority ??
    workOrder?.priority ??
    'medium') as KanbanPriority,
  dueDate: workOrder?.due_date ?? null,
  assignee: card.assignee || (workOrder?.assignee ?? ''),
  machine: card.machine ?? workOrder?.machine ?? null,
  machineName: card.machine_name ?? workOrder?.machine_name ?? null,
  tags: card.tags ?? workOrder?.tags ?? [],
  company: workOrder?.company ?? '',
  companyContactName: workOrder?.company_contact_name ?? '',
  companyContactPhone: workOrder?.company_contact_phone ?? '',
  jobNumber: workOrder?.job_number ?? '',
  serviceQuote: workOrder?.service_quote ?? '',
  createdAt: card.created_at,
  updatedAt: card.updated_at,
  parts: (workOrder?.parts ?? []).map((p: WorkOrderPart) => ({
    partId: p.part,
    partName: p.part_name,
    quantity: p.quantity,
    allocationStatus: p.allocation_status,
    allocatedQuantity: p.allocated_quantity,
    allocationNote: p.allocation_note
  }))
});

const formValuesToPayload = (values: TaskFormValues) => ({
  title: values.title,
  description: values.description,
  status: values.status,
  priority: values.priority,
  due_date: values.dueDate ? dayjs(values.dueDate).format('YYYY-MM-DD') : null,
  assignee: values.assignee,
  machine: values.machine ? Number(values.machine) : null,
  tags: values.tags,
  company: values.company,
  company_contact_name: values.companyContactName,
  company_contact_phone: values.companyContactPhone,
  job_number: values.jobNumber,
  service_quote: values.serviceQuote
});

/**
 * Maintenance board: the Board panel of the Maintenance workspace.
 *
 * Creating work goes through the audited work-package command via
 * {@link WorkOrderCreateModal}; the board never POSTs a raw Kanban card. The
 * in-place edit modal below still uses the generic card endpoint and migrates to
 * versioned update commands in a later phase.
 */
export default function MaintenanceBoard() {
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Below Mantine's `sm` breakpoint the size='lg' modals are unusable; go
  // full-screen so forms are reachable on a phone. HTML5 drag between columns
  // does not fire on touch anyway, so the per-card status Select remains the
  // touch fallback for moving cards.
  const isSmallScreen = useMediaQuery('(max-width: 48em)');

  const defaultColumns = useMemo<Column[]>(
    () => [
      { id: 'backlog', label: t`Backlog`, color: 'gray' },
      { id: 'in-progress', label: t`In Progress`, color: 'indigo' },
      { id: 'review', label: t`In Review`, color: 'yellow' },
      { id: 'done', label: t`Done`, color: 'green' }
    ],
    []
  );

  const [columns, setColumns] = useState<Column[]>(defaultColumns);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskModalOpen, setTaskModalOpen] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createdWorkPackage, setCreatedWorkPackage] =
    useState<WorkPackageResult | null>(null);
  const [columnModalOpen, setColumnModalOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [tagOptions, setTagOptions] = useState<string[]>(DEFAULT_TAG_OPTIONS);
  const [newTagName, setNewTagName] = useState('');
  const [filters, setFilters] = useState<Filters>({
    search: '',
    column: 'all',
    priority: 'all',
    tags: [],
    assignee: 'all',
    jobNumber: 'all',
    serviceQuote: 'all'
  });
  const [isReordering, setIsReordering] = useState(false);
  const [pendingColumnOrder, setPendingColumnOrder] =
    useState<Column[]>(defaultColumns);
  const [columnDeletionContext, setColumnDeletionContext] =
    useState<ColumnDeletionContext | null>(null);
  const [draggingTaskId, setDraggingTaskId] = useState<number | null>(null);
  const [dragOverColumnId, setDragOverColumnId] = useState<string | null>(null);
  const [savingTask, setSavingTask] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<number | null>(null);
  const [statusUpdating, setStatusUpdating] = useState<Set<number>>(new Set());

  /* ── Parts picker state ──────────────────────────── */
  const [formParts, setFormParts] = useState<FormPart[]>([]);
  const [partSearch, setPartSearch] = useState('');
  const [debouncedPartSearch] = useDebouncedValue(partSearch, 300);
  const [partSearchResults, setPartSearchResults] = useState<
    { pk: number; name: string; IPN: string }[]
  >([]);
  const [partSearchLoading, setPartSearchLoading] = useState(false);

  /* Search parts API whenever the debounced term changes */
  useEffect(() => {
    if (!debouncedPartSearch || debouncedPartSearch.length < 2) {
      setPartSearchResults([]);
      return;
    }

    let cancelled = false;
    setPartSearchLoading(true);

    api
      .get(apiUrl(ApiEndpoints.part_list), {
        params: { search: debouncedPartSearch, limit: 20 }
      })
      .then((response) => {
        if (!cancelled) {
          const results = (response.data?.results ?? response.data ?? []) as {
            pk: number;
            name: string;
            IPN: string;
          }[];
          setPartSearchResults(results);
        }
      })
      .catch(() => {
        if (!cancelled) setPartSearchResults([]);
      })
      .finally(() => {
        if (!cancelled) setPartSearchLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [debouncedPartSearch, api]);

  const addFormPart = useCallback(
    (partId: number, partName: string) => {
      if (formParts.some((p) => p.partId === partId)) return;
      setFormParts((prev) => [...prev, { partId, partName, quantity: 1 }]);
      setPartSearch('');
      setPartSearchResults([]);
    },
    [formParts]
  );

  const removeFormPart = useCallback((partId: number) => {
    setFormParts((prev) => prev.filter((p) => p.partId !== partId));
  }, []);

  const updateFormPartQty = useCallback((partId: number, quantity: number) => {
    setFormParts((prev) =>
      prev.map((p) => (p.partId === partId ? { ...p, quantity } : p))
    );
  }, []);

  const cardsQuery = useQuery<
    WorkOrder[],
    Error,
    WorkOrder[],
    ['kanban-cards']
  >({
    queryKey: ['kanban-cards'],
    queryFn: async () => {
      try {
        const response = await api.get<WorkOrder[]>(
          apiUrl(ApiEndpoints.kanban_card_list)
        );
        return response.data ?? [];
      } catch (error) {
        showApiErrorMessage({
          error,
          title: t`Could not load Kanban cards`
        });
        throw error;
      }
    }
  });

  // The board renders cards. The work orders are fetched too, for everything a
  // card does not answer for (priority, parts, customer detail) and because the
  // edit form addresses the job.
  const boardCardsQuery = useQuery<
    BoardCard[],
    Error,
    BoardCard[],
    ['kanban-board-cards']
  >({
    queryKey: ['kanban-board-cards'],
    queryFn: async () => {
      try {
        const response = await api.get<BoardCard[]>(
          apiUrl(ApiEndpoints.kanban_board_card_list)
        );
        return response.data ?? [];
      } catch (error) {
        showApiErrorMessage({
          error,
          title: t`Could not load board cards`
        });
        throw error;
      }
    }
  });

  useEffect(() => {
    const workOrders = new Map(
      (cardsQuery.data ?? []).map((workOrder) => [workOrder.id, workOrder])
    );
    setTasks(
      (boardCardsQuery.data ?? []).map((card) =>
        convertCardToTask(card, workOrders.get(card.work_order))
      )
    );
  }, [boardCardsQuery.data, cardsQuery.data]);

  // Board columns are persisted server-side (kanban/columns/). Previously they
  // lived only in useState, so add/reorder/delete never survived a refresh and a
  // card in a custom column vanished. defaultColumns remains only as the initial
  // render before this query resolves.
  const columnsQuery = useQuery<
    KanbanColumnRecord[],
    Error,
    KanbanColumnRecord[],
    ['kanban-columns']
  >({
    queryKey: ['kanban-columns'],
    queryFn: async () => {
      try {
        const response = await api.get<KanbanColumnRecord[]>(
          apiUrl(ApiEndpoints.kanban_column_list)
        );
        return response.data ?? [];
      } catch (error) {
        showApiErrorMessage({
          error,
          title: t`Could not load board columns`
        });
        throw error;
      }
    }
  });

  useEffect(() => {
    // Do not clobber an in-progress reorder with a server refetch; the reorder
    // is persisted on save, after which this effect re-syncs to the server truth.
    if (isReordering || !columnsQuery.data || columnsQuery.data.length === 0) {
      return;
    }

    setColumns(
      columnsQuery.data.map((column) => ({
        id: column.key,
        label: column.label,
        color: column.color || 'gray'
      }))
    );
  }, [columnsQuery.data, isReordering]);

  // Machines to populate the (required) machine picker. There are few machines,
  // so the whole list is fetched once rather than searched.
  const machinesQuery = useQuery<
    { pk: number; name: string; location: string }[],
    Error,
    { pk: number; name: string; location: string }[],
    ['asset-machines']
  >({
    queryKey: ['asset-machines'],
    queryFn: async () => {
      const response = await api.get(apiUrl(ApiEndpoints.asset_machine_list));
      return response.data ?? [];
    }
  });

  const machineOptions = useMemo(
    () =>
      (machinesQuery.data ?? []).map((machine) => ({
        value: String(machine.pk),
        label: machine.location
          ? `${machine.name} — ${machine.location}`
          : machine.name
      })),
    [machinesQuery.data]
  );

  useEffect(() => {
    const serverTags = Array.from(
      new Set(
        tasks
          .flatMap((task) => task.tags)
          .filter((tag): tag is string => Boolean(tag))
      )
    );

    if (serverTags.length === 0) {
      return;
    }

    setTagOptions((current) => {
      const combined = Array.from(
        new Set([...DEFAULT_TAG_OPTIONS, ...current, ...serverTags])
      );

      if (
        combined.length === current.length &&
        combined.every((value) => current.includes(value))
      ) {
        return current;
      }

      return combined;
    });
  }, [tasks]);

  useEffect(() => {
    if (!isReordering) {
      setPendingColumnOrder(columns);
    }
  }, [columns, isReordering]);

  const taskForm = useForm<TaskFormValues>({
    initialValues: {
      title: '',
      description: '',
      status: 'backlog',
      priority: 'medium',
      assignee: '',
      machine: null,
      tags: [],
      dueDate: null,
      company: '',
      companyContactName: '',
      companyContactPhone: '',
      jobNumber: '',
      serviceQuote: ''
    },
    validate: {
      title: (value) =>
        value.trim().length === 0
          ? t`Give the work order a descriptive title.`
          : null,
      status: (value) =>
        value ? null : t`Choose a column for this work order.`,
      // Every work order is anchored to a machine (backend enforces this too).
      machine: (value) => (value ? null : t`Select the machine for this work.`)
    }
  });

  const columnForm = useForm<ColumnFormValues>({
    initialValues: {
      label: '',
      color: 'gray'
    },
    validate: {
      label: (value) =>
        value.trim().length === 0 ? t`Name cannot be empty.` : null
    }
  });

  const columnOptions = useMemo(
    () => columns.map((column) => ({ value: column.id, label: column.label })),
    [columns]
  );
  const tagData = useMemo(
    () => tagOptions.map((tag) => ({ value: tag, label: tag })),
    [tagOptions]
  );

  const columnFilterOptions = [
    { value: 'all', label: t`All columns` },
    ...columns.map((column) => ({ value: column.id, label: column.label }))
  ];

  const priorityFilterOptions = [
    { value: 'all', label: t`All priorities` },
    { value: 'low', label: t`Low` },
    { value: 'medium', label: t`Medium` },
    { value: 'high', label: t`High` }
  ];

  const jobFilterOptions = useMemo(() => {
    const jobNumbers = Array.from(
      new Set(
        tasks
          .map((task) => task.jobNumber)
          .filter((job): job is string => Boolean(job))
      )
    );

    return [
      { value: 'all', label: t`All jobs` },
      ...jobNumbers.map((job) => ({ value: job, label: job }))
    ];
  }, [tasks]);

  const assigneeFilterOptions = useMemo(() => {
    const assignees = Array.from(
      new Set(
        tasks
          .map((task) => task.assignee)
          .filter((assignee): assignee is string => Boolean(assignee))
      )
    );

    return [
      { value: 'all', label: t`All employees` },
      ...assignees.map((assignee) => ({ value: assignee, label: assignee }))
    ];
  }, [tasks]);

  const serviceQuoteFilterOptions = useMemo(() => {
    const quotes = Array.from(
      new Set(
        tasks
          .map((task) => task.serviceQuote)
          .filter((quote): quote is string => Boolean(quote))
      )
    );

    return [
      { value: 'all', label: t`All service quotes` },
      ...quotes.map((quote) => ({ value: quote, label: quote }))
    ];
  }, [tasks]);

  const {
    search: searchFilter,
    column: columnFilter,
    priority: priorityFilter,
    tags: tagFilter,
    assignee: assigneeFilter,
    jobNumber: jobNumberFilter,
    serviceQuote: serviceQuoteFilter
  } = filters;

  const filteredTasks = useMemo(
    () =>
      tasks.filter((task) => {
        if (columnFilter !== 'all' && task.status !== columnFilter) {
          return false;
        }

        if (priorityFilter !== 'all' && task.priority !== priorityFilter) {
          return false;
        }

        if (
          tagFilter.length > 0 &&
          !tagFilter.every((tag) => task.tags.includes(tag))
        ) {
          return false;
        }

        if (assigneeFilter !== 'all' && task.assignee !== assigneeFilter) {
          return false;
        }

        if (jobNumberFilter !== 'all' && task.jobNumber !== jobNumberFilter) {
          return false;
        }

        if (
          serviceQuoteFilter !== 'all' &&
          task.serviceQuote !== serviceQuoteFilter
        ) {
          return false;
        }

        if (searchFilter.trim().length > 0) {
          const haystack = [
            task.title,
            task.description,
            task.assignee,
            task.tags.join(' '),
            task.company,
            task.companyContactName,
            task.companyContactPhone,
            task.jobNumber,
            task.serviceQuote
          ]
            .join(' ')
            .toLowerCase();

          if (!haystack.includes(searchFilter.trim().toLowerCase())) {
            return false;
          }
        }

        return true;
      }),
    [
      tasks,
      columnFilter,
      priorityFilter,
      tagFilter,
      searchFilter,
      jobNumberFilter,
      serviceQuoteFilter,
      assigneeFilter
    ]
  );

  const filtersActive = useMemo(
    () =>
      searchFilter.trim().length > 0 ||
      columnFilter !== 'all' ||
      priorityFilter !== 'all' ||
      tagFilter.length > 0 ||
      assigneeFilter !== 'all' ||
      jobNumberFilter !== 'all' ||
      serviceQuoteFilter !== 'all',
    [
      searchFilter,
      columnFilter,
      priorityFilter,
      tagFilter,
      assigneeFilter,
      jobNumberFilter,
      serviceQuoteFilter
    ]
  );

  const displayColumns = isReordering ? pendingColumnOrder : columns;
  const visibleColumns = isReordering
    ? displayColumns
    : columnFilter === 'all'
      ? displayColumns
      : displayColumns.filter((column) => column.id === columnFilter);

  const displayColumnCount = visibleColumns.length;
  const largeBreakpointColumns = Math.max(1, Math.min(displayColumnCount, 4));
  const smallBreakpointColumns = Math.max(1, Math.min(displayColumnCount, 2));

  const isColumnOrderDirty = useMemo(() => {
    if (pendingColumnOrder.length !== columns.length) {
      return true;
    }

    return pendingColumnOrder.some(
      (column, index) => column.id !== columns[index]?.id
    );
  }, [pendingColumnOrder, columns]);

  const markStatusUpdating = (taskId: number, updating: boolean) => {
    setStatusUpdating((current) => {
      const next = new Set(current);

      if (updating) {
        next.add(taskId);
      } else {
        next.delete(taskId);
      }

      return next;
    });
  };

  const handleWorkPackageCreated = useCallback(
    async (result: WorkPackageResult) => {
      // Board, Calendar and Timeline all read the same collections. A new job
      // arrives with its card, so both have to be refreshed.
      await queryClient.invalidateQueries({ queryKey: ['kanban-cards'] });
      await queryClient.invalidateQueries({
        queryKey: ['kanban-board-cards']
      });

      notifications.show({
        title: t`Work order created`,
        message: result.repair_packet_reference
          ? t`${result.work_order_reference} was created with repair packet ${result.repair_packet_reference}.`
          : t`${result.work_order_reference} was created.`,
        color: 'green',
        icon: <IconCircleCheck size={16} />,
        autoClose: 8000
      });

      setCreatedWorkPackage(result);
    },
    [queryClient]
  );

  const openEditTaskModal = (task: Task) => {
    setEditingTask(task);
    setFormParts(task.parts.map((p) => ({ ...p })));
    taskForm.setValues({
      title: task.title,
      description: task.description,
      status: task.status,
      priority: task.priority,
      assignee: task.assignee,
      machine: task.machine ? String(task.machine) : null,
      tags: task.tags,
      dueDate: task.dueDate ? dayjs(task.dueDate).toDate() : null,
      company: task.company,
      companyContactName: task.companyContactName,
      companyContactPhone: task.companyContactPhone,
      jobNumber: task.jobNumber,
      serviceQuote: task.serviceQuote
    });
    taskForm.resetDirty();
    setTaskModalOpen(true);
  };

  const closeTaskModal = () => {
    setTaskModalOpen(false);
    setEditingTask(null);
    setNewTagName('');
    setFormParts([]);
    setPartSearch('');
    setPartSearchResults([]);
    taskForm.reset();
    setSavingTask(false);
  };

  // Editing only. Creation is not reachable from this form: a new work order is
  // an audited compound command, not a generic card POST.
  const handleTaskSubmit = taskForm.onSubmit(async (values) => {
    if (!editingTask) {
      return;
    }

    const payload = formValuesToPayload(values);

    setSavingTask(true);

    try {
      // The form edits the job - priority, machine, parts, customer - so it
      // addresses the work order, not the card that happens to be selected.
      const response = await api.put(
        apiUrl(ApiEndpoints.kanban_card_detail, editingTask.workOrderId),
        payload
      );
      // Reflect the job's new values on every card showing it, keeping each
      // card's own title and column.
      const workOrder: WorkOrder = response.data;
      setTasks((current) =>
        current.map((task) =>
          task.workOrderId === workOrder.id
            ? {
                ...task,
                title:
                  task.cardKind === 'work_order' ? workOrder.title : task.title,
                description: workOrder.description ?? task.description,
                priority: workOrder.priority as KanbanPriority,
                dueDate: workOrder.due_date,
                machine: workOrder.machine ?? null,
                machineName: workOrder.machine_name ?? null,
                tags: workOrder.tags ?? []
              }
            : task
        )
      );
      const cardId = editingTask.workOrderId;

      notifications.show({
        title: t`Work order updated`,
        message: t`Changes saved successfully.`,
        color: 'green',
        icon: <IconCircleCheck size={16} />
      });
      try {
        const partsUrl = apiUrl(ApiEndpoints.kanban_card_parts, cardId);
        const allocResponse = await api.put(
          partsUrl,
          formParts.map((fp) => ({ part: fp.partId, quantity: fp.quantity }))
        );
        const allocData = allocResponse.data as {
          parts: WorkOrderPart[];
          warnings: string[];
          all_allocated: boolean;
        };

        if (!allocData.all_allocated && allocData.warnings.length > 0) {
          notifications.show({
            title: t`Stock warning`,
            message: allocData.warnings.join('\n'),
            color: 'orange',
            autoClose: 10000
          });
        } else if (allocData.all_allocated && allocData.parts.length > 0) {
          notifications.show({
            title: t`Stock allocated`,
            message: t`All required parts have sufficient stock.`,
            color: 'green',
            icon: <IconCircleCheck size={16} />
          });
        }
      } catch (error) {
        showApiErrorMessage({
          error,
          title: t`Could not save the parts for this work order`
        });
      }

      await queryClient.invalidateQueries({ queryKey: ['kanban-cards'] });
      closeTaskModal();
    } catch (error) {
      showApiErrorMessage({ error, title: t`Could not update work order` });
      setSavingTask(false);
    }
  });

  const handleStatusChange = async (taskId: number, status: KanbanStatus) => {
    const currentTask = tasks.find((task) => task.id === taskId);

    if (!currentTask || currentTask.status === status) {
      return;
    }

    markStatusUpdating(taskId, true);

    setTasks((current) =>
      current.map((task) => (task.id === taskId ? { ...task, status } : task))
    );

    try {
      // A drag moves the card. The job's lifecycle is not touched by it - that
      // is what the work-order commands are for.
      await api.patch(apiUrl(ApiEndpoints.kanban_board_card_detail, taskId), {
        status
      });

      setTasks((current) =>
        current.map((task) => (task.id === taskId ? { ...task, status } : task))
      );
      await queryClient.invalidateQueries({
        queryKey: ['kanban-board-cards']
      });
    } catch (error) {
      showApiErrorMessage({
        error,
        title: t`Could not update status`
      });

      setTasks((current) =>
        current.map((task) =>
          task.id === taskId ? { ...task, status: currentTask.status } : task
        )
      );
    } finally {
      markStatusUpdating(taskId, false);
    }
  };

  const handleDeleteTask = async (taskId: number) => {
    if (deletingTaskId === taskId) {
      return;
    }

    setDeletingTaskId(taskId);

    const target = tasks.find((task) => task.id === taskId);

    try {
      // Archiving the card that tracks the job archives the job; the job would
      // otherwise stay open with nothing on the board. Archiving any other card
      // removes only that piece of work.
      if (target && target.cardKind !== 'work_order') {
        await api.delete(apiUrl(ApiEndpoints.kanban_board_card_detail, taskId));
      } else {
        await api.delete(
          apiUrl(ApiEndpoints.kanban_card_detail, target?.workOrderId ?? taskId)
        );
      }

      setTasks((current) => current.filter((task) => task.id !== taskId));

      notifications.show({
        title:
          target && target.cardKind !== 'work_order'
            ? t`Card archived`
            : t`Work order archived`,
        message: t`The card is no longer visible on the board.`,
        color: 'green',
        icon: <IconCircleCheck size={16} />
      });

      await queryClient.invalidateQueries({ queryKey: ['kanban-cards'] });
      await queryClient.invalidateQueries({
        queryKey: ['kanban-board-cards']
      });
    } catch (error) {
      showApiErrorMessage({
        error,
        title: t`Could not delete work order`
      });
    } finally {
      setDeletingTaskId(null);
    }
  };

  const resetFilters = () => {
    setFilters({
      search: '',
      column: 'all',
      priority: 'all',
      tags: [],
      assignee: 'all',
      jobNumber: 'all',
      serviceQuote: 'all'
    });
  };

  const handleTaskDragStart = (
    event: DragEvent<HTMLDivElement>,
    taskId: number
  ) => {
    event.dataTransfer.setData('text/plain', String(taskId));
    event.dataTransfer.effectAllowed = 'move';
    setDraggingTaskId(taskId);
  };

  const handleTaskDragEnd = () => {
    setDraggingTaskId(null);
    setDragOverColumnId(null);
  };

  const handleColumnDragOver = (
    event: DragEvent<HTMLDivElement>,
    columnId: string
  ) => {
    if (draggingTaskId == null) {
      return;
    }

    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';

    if (dragOverColumnId !== columnId) {
      setDragOverColumnId(columnId);
    }
  };

  const handleColumnDragLeave = (
    event: DragEvent<HTMLDivElement>,
    columnId: string
  ) => {
    const relatedTarget = event.relatedTarget as Node | null;

    if (!relatedTarget || !event.currentTarget.contains(relatedTarget)) {
      setDragOverColumnId((current) => (current === columnId ? null : current));
    }
  };

  const handleColumnDrop = async (
    event: DragEvent<HTMLDivElement>,
    columnId: string
  ) => {
    event.preventDefault();

    const rawId = event.dataTransfer.getData('text/plain');
    const droppedTaskId = rawId ? Number(rawId) : draggingTaskId;

    if (droppedTaskId == null) {
      handleTaskDragEnd();
      return;
    }

    await handleStatusChange(droppedTaskId, columnId as KanbanStatus);
    handleTaskDragEnd();
  };

  const handleAddTagOption = () => {
    const normalized = newTagName.trim();

    if (!normalized) {
      return;
    }

    setTagOptions((current) =>
      current.includes(normalized) ? current : [...current, normalized]
    );

    setNewTagName('');

    taskForm.setFieldValue(
      'tags',
      taskForm.values.tags.includes(normalized)
        ? taskForm.values.tags
        : [...taskForm.values.tags, normalized]
    );
  };

  const handleRequestDeleteColumn = (columnId: string) => {
    const columnIndex = columns.findIndex((column) => column.id === columnId);

    if (columnIndex <= 0) {
      return;
    }

    setColumnDeletionContext({
      column: columns[columnIndex],
      fallbackColumn: columns[columnIndex - 1]
    });
  };

  const handleDeleteColumn = async (columnId: string) => {
    const columnIndex = columns.findIndex((column) => column.id === columnId);

    if (columnIndex <= 0) {
      return;
    }

    const fallbackId = columns[columnIndex - 1].id;
    const record = columnsQuery.data?.find((column) => column.key === columnId);

    // The server refuses to delete a default (seeded) column. Detect that up
    // front so we do NOT mass-reassign the column's cards for a deletion that
    // is guaranteed to fail - which would scramble the board while leaving the
    // column in place.
    if (record?.is_default) {
      showApiErrorMessage({
        error: new Error(t`Default columns cannot be deleted.`),
        title: t`Could not delete column`
      });
      setColumnDeletionContext(null);
      return;
    }

    const affectedTasks = tasks.filter((task) => task.status === columnId);

    // Reassign the column's cards to the fallback first: the server refuses to
    // delete a column that still holds cards, so this is a precondition, not
    // just tidy-up.
    await Promise.all(
      affectedTasks.map((task) =>
        handleStatusChange(task.id, fallbackId as KanbanStatus)
      )
    );

    try {
      if (record) {
        await api.delete(apiUrl(ApiEndpoints.kanban_column_detail, record.id));
      }

      await queryClient.invalidateQueries({ queryKey: ['kanban-columns'] });

      setFilters((currentFilters) => ({
        ...currentFilters,
        column:
          currentFilters.column === columnId ? 'all' : currentFilters.column
      }));

      setColumnDeletionContext(null);
    } catch (error) {
      showApiErrorMessage({ error, title: t`Could not delete column` });
    }
  };

  const closeDeleteColumnModal = () => {
    setColumnDeletionContext(null);
  };

  const openColumnModal = () => {
    columnForm.setValues({ label: '', color: 'gray' });
    columnForm.resetDirty();
    setColumnModalOpen(true);
  };

  const closeColumnModal = () => {
    setColumnModalOpen(false);
    columnForm.reset();
  };

  const handleColumnSubmit = columnForm.onSubmit(async (values) => {
    const nextSlug = slugify(values.label);

    if (columns.some((column) => column.id === nextSlug)) {
      columnForm.setFieldError('label', t`Choose a unique name.`);
      return;
    }

    try {
      await api.post(apiUrl(ApiEndpoints.kanban_column_list), {
        key: nextSlug,
        label: values.label,
        color: values.color
      });

      await queryClient.invalidateQueries({ queryKey: ['kanban-columns'] });
      closeColumnModal();
    } catch (error) {
      showApiErrorMessage({ error, title: t`Could not create column` });
    }
  });

  const renderPriorityLabel = (priority: KanbanPriority) => {
    if (priority === 'low') {
      return t`Low`;
    }

    if (priority === 'high') {
      return t`High`;
    }

    return t`Medium`;
  };

  const enterReorderMode = () => {
    setPendingColumnOrder(columns);
    setIsReordering(true);
  };

  const cancelReorderMode = () => {
    setPendingColumnOrder(columns);
    setIsReordering(false);
  };

  const saveColumnOrder = async () => {
    const order = pendingColumnOrder.map((column) => column.id);

    try {
      await api.post(apiUrl(ApiEndpoints.kanban_column_reorder), { order });

      setColumns([...pendingColumnOrder]);
      setIsReordering(false);
      await queryClient.invalidateQueries({ queryKey: ['kanban-columns'] });
    } catch (error) {
      showApiErrorMessage({ error, title: t`Could not save column order` });
    }
  };

  const moveColumn = (index: number, direction: number) => {
    setPendingColumnOrder((current) => {
      const targetIndex = index + direction;

      if (targetIndex < 0 || targetIndex >= current.length) {
        return current;
      }

      const next = [...current];
      [next[targetIndex], next[index]] = [next[index], next[targetIndex]];
      return next;
    });
  };

  return (
    <Stack gap='lg'>
      <Text>{t`Track maintenance work by stage, keep ownership visible, and open any work order for its full detail.`}</Text>
      <Group justify='space-between' align='flex-start'>
        <Group gap='sm'>
          <Button
            leftSection={<IconPlus size={16} />}
            onClick={() => setCreateModalOpen(true)}
          >
            {t`New work order`}
          </Button>
          <Button
            variant='subtle'
            leftSection={<IconPlus size={16} />}
            onClick={openColumnModal}
            disabled={isReordering}
          >
            {t`Add column`}
          </Button>
        </Group>
        <Group gap='sm'>
          {!isReordering ? (
            <Button
              variant='subtle'
              leftSection={<IconArrowsSort size={16} />}
              onClick={enterReorderMode}
              disabled={columns.length < 2}
            >
              {t`Edit column order`}
            </Button>
          ) : (
            <>
              <Button
                variant='default'
                leftSection={<IconX size={16} />}
                onClick={cancelReorderMode}
              >
                {t`Cancel`}
              </Button>
              <Button
                leftSection={<IconDeviceFloppy size={16} />}
                onClick={saveColumnOrder}
                disabled={!isColumnOrderDirty}
              >
                {t`Save order`}
              </Button>
            </>
          )}
        </Group>
      </Group>

      <Paper withBorder radius='md' p='md'>
        <Stack gap='sm'>
          <Group gap='sm' wrap='wrap'>
            <TextInput
              size='sm'
              label={t`Search`}
              placeholder={t`Title, description, or owner`}
              value={searchFilter}
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  search: event.currentTarget.value
                }))
              }
              style={{ flex: 1, minWidth: '220px' }}
            />
            <Select
              size='sm'
              label={t`Column`}
              data={columnFilterOptions}
              value={columnFilter}
              onChange={(value) =>
                setFilters((current) => ({
                  ...current,
                  column: value ?? 'all'
                }))
              }
              style={{ minWidth: '180px' }}
            />
            <Select
              size='sm'
              label={t`Priority`}
              data={priorityFilterOptions}
              value={priorityFilter}
              onChange={(value) =>
                setFilters((current) => ({
                  ...current,
                  priority: (value as PriorityFilterValue) ?? 'all'
                }))
              }
              style={{ minWidth: '180px' }}
            />
            <MultiSelect
              size='sm'
              label={t`Tags`}
              placeholder={t`Filter by tags`}
              data={tagData}
              value={tagFilter}
              onChange={(value) =>
                setFilters((current) => ({ ...current, tags: value }))
              }
              searchable
              style={{ minWidth: '220px', flex: 1 }}
            />
            <Select
              size='sm'
              label={t`Employee`}
              data={assigneeFilterOptions}
              value={assigneeFilter}
              onChange={(value) =>
                setFilters((current) => ({
                  ...current,
                  assignee: value ?? 'all'
                }))
              }
              style={{ minWidth: '200px' }}
              disabled={assigneeFilterOptions.length <= 1}
            />
            <Select
              size='sm'
              label={t`Job number`}
              data={jobFilterOptions}
              value={jobNumberFilter}
              onChange={(value) =>
                setFilters((current) => ({
                  ...current,
                  jobNumber: value ?? 'all'
                }))
              }
              style={{ minWidth: '200px' }}
              disabled={jobFilterOptions.length <= 1}
            />
            <Select
              size='sm'
              label={t`Service quote`}
              data={serviceQuoteFilterOptions}
              value={serviceQuoteFilter}
              onChange={(value) =>
                setFilters((current) => ({
                  ...current,
                  serviceQuote: value ?? 'all'
                }))
              }
              style={{ minWidth: '200px' }}
              disabled={serviceQuoteFilterOptions.length <= 1}
            />
          </Group>
          <Group justify='flex-end'>
            <Button
              variant='subtle'
              onClick={resetFilters}
              disabled={!filtersActive}
            >
              {t`Reset filters`}
            </Button>
          </Group>
        </Stack>
      </Paper>

      {cardsQuery.isLoading ? (
        <Paper withBorder radius='md' p='md'>
          <Group justify='center'>
            <Loader />
          </Group>
        </Paper>
      ) : visibleColumns.length === 0 ? (
        <Paper withBorder radius='md' p='md'>
          <Text c='dimmed'>{t`No columns match the current filters.`}</Text>
        </Paper>
      ) : (
        <SimpleGrid
          cols={{
            base: 1,
            sm: smallBreakpointColumns,
            lg: largeBreakpointColumns
          }}
          spacing='lg'
        >
          {visibleColumns.map((column, index) => {
            const columnTasks = filteredTasks.filter(
              (task) => task.status === column.id
            );
            const columnIndex = columns.findIndex(
              (item) => item.id === column.id
            );

            return (
              <Paper
                key={column.id}
                withBorder
                radius='md'
                p='md'
                onDragOver={
                  isReordering
                    ? undefined
                    : (event) => handleColumnDragOver(event, column.id)
                }
                onDragLeave={
                  isReordering
                    ? undefined
                    : (event) => handleColumnDragLeave(event, column.id)
                }
                onDrop={
                  isReordering
                    ? undefined
                    : (event) => handleColumnDrop(event, column.id)
                }
                style={
                  !isReordering && dragOverColumnId === column.id
                    ? {
                        outline: '2px dashed var(--mantine-color-blue-5)',
                        outlineOffset: '4px'
                      }
                    : undefined
                }
              >
                <Stack gap='md'>
                  <Group justify='space-between'>
                    <Group gap='xs'>
                      <Text fw={600}>{column.label}</Text>
                      <Badge color={column.color} variant='light'>
                        {columnTasks.length}
                      </Badge>
                    </Group>
                    <Group gap='xs'>
                      {isReordering && (
                        <>
                          <ActionIcon
                            size='sm'
                            variant='subtle'
                            aria-label={t`Move column left`}
                            onClick={() => moveColumn(index, -1)}
                            disabled={index === 0}
                          >
                            <IconArrowLeft size={16} />
                          </ActionIcon>
                          <ActionIcon
                            size='sm'
                            variant='subtle'
                            aria-label={t`Move column right`}
                            onClick={() => moveColumn(index, 1)}
                            disabled={index === visibleColumns.length - 1}
                          >
                            <IconArrowRight size={16} />
                          </ActionIcon>
                        </>
                      )}
                      {!isReordering && columnIndex > 0 && (
                        <ActionIcon
                          size='sm'
                          variant='subtle'
                          color='red'
                          aria-label={t`Delete column`}
                          onClick={() => handleRequestDeleteColumn(column.id)}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      )}
                    </Group>
                  </Group>

                  {columnTasks.length === 0 ? (
                    <Text size='sm' c='dimmed'>
                      {t`No work in this column yet.`}
                    </Text>
                  ) : (
                    <Stack gap='sm'>
                      {columnTasks.map((task) => {
                        const isDeleting = deletingTaskId === task.id;
                        const isStatusUpdating = statusUpdating.has(task.id);

                        return (
                          <Card
                            key={task.id}
                            withBorder
                            shadow='sm'
                            radius='md'
                            p='md'
                            draggable={
                              !isReordering && !isDeleting && !isStatusUpdating
                            }
                            onDragStart={
                              isReordering
                                ? undefined
                                : (event) => handleTaskDragStart(event, task.id)
                            }
                            onDragEnd={
                              isReordering ? undefined : handleTaskDragEnd
                            }
                            style={
                              !isReordering && draggingTaskId === task.id
                                ? { opacity: 0.4, cursor: 'grabbing' }
                                : {
                                    cursor:
                                      isReordering ||
                                      isDeleting ||
                                      isStatusUpdating
                                        ? 'default'
                                        : 'grab'
                                  }
                            }
                          >
                            <Stack gap='sm'>
                              <Group justify='space-between' align='flex-start'>
                                <Stack gap={2}>
                                  <Text fw={600}>{task.title}</Text>
                                  {task.description && (
                                    <Text size='sm' c='dimmed' lineClamp={3}>
                                      {task.description}
                                    </Text>
                                  )}
                                </Stack>
                                <Group gap='xs'>
                                  <ActionIcon
                                    size='sm'
                                    variant='subtle'
                                    aria-label={t`Open work order detail`}
                                    onClick={() =>
                                      navigate(
                                        `/maintenance/work-orders/${task.id}/`
                                      )
                                    }
                                    disabled={isDeleting}
                                  >
                                    <IconExternalLink size={16} />
                                  </ActionIcon>
                                  <ActionIcon
                                    size='sm'
                                    variant='subtle'
                                    aria-label={t`Edit work order`}
                                    onClick={() => openEditTaskModal(task)}
                                    disabled={isDeleting}
                                  >
                                    <IconPencil size={16} />
                                  </ActionIcon>
                                  <ActionIcon
                                    size='sm'
                                    variant='subtle'
                                    color='red'
                                    aria-label={t`Delete work order`}
                                    onClick={() => handleDeleteTask(task.id)}
                                    disabled={isDeleting}
                                  >
                                    {isDeleting ? (
                                      <Loader size='xs' />
                                    ) : (
                                      <IconTrash size={16} />
                                    )}
                                  </ActionIcon>
                                </Group>
                              </Group>

                              <Group gap='xs'>
                                <Badge
                                  color={priorityColors[task.priority]}
                                  variant='light'
                                >
                                  {renderPriorityLabel(task.priority)}
                                </Badge>
                                {task.assignee && (
                                  <Badge color='gray' variant='outline'>
                                    {task.assignee}
                                  </Badge>
                                )}
                                {task.dueDate && (
                                  <Badge
                                    color={getDueBadgeColor(task.dueDate)}
                                    variant='light'
                                  >
                                    {dayjs(task.dueDate).format('MMM D')}
                                  </Badge>
                                )}
                              </Group>

                              {task.tags.length > 0 && (
                                <Group gap='xs'>
                                  {task.tags.map((tag) => (
                                    <Badge
                                      key={tag}
                                      color='gray'
                                      variant='outline'
                                    >
                                      {tag}
                                    </Badge>
                                  ))}
                                </Group>
                              )}

                              <Stack gap={2}>
                                {task.machineName && (
                                  <Text size='sm'>
                                    {t`Machine`}: {task.machineName}
                                  </Text>
                                )}
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

                              {/* Parts & stock allocation badges */}
                              {task.parts.length > 0 && (
                                <Stack gap={4}>
                                  <Text size='xs' fw={500} c='dimmed'>
                                    {t`Parts`}
                                  </Text>
                                  {task.parts.map((p) => {
                                    const statusColor: Record<string, string> =
                                      {
                                        full: 'green',
                                        partial: 'yellow',
                                        insufficient: 'red',
                                        none: 'gray'
                                      };
                                    const color =
                                      statusColor[
                                        p.allocationStatus ?? 'none'
                                      ] ?? 'gray';
                                    return (
                                      <Group key={p.partId} gap={4}>
                                        <Text
                                          size='xs'
                                          style={{ flex: 1 }}
                                          lineClamp={1}
                                        >
                                          {p.partName}
                                        </Text>
                                        <Tooltip
                                          label={p.allocationNote ?? ''}
                                          disabled={!p.allocationNote}
                                        >
                                          <Badge
                                            size='xs'
                                            color={color}
                                            variant='light'
                                          >
                                            {p.allocatedQuantity ?? 0}/
                                            {p.quantity}
                                          </Badge>
                                        </Tooltip>
                                      </Group>
                                    );
                                  })}
                                </Stack>
                              )}

                              <Select
                                size='xs'
                                label={t`Status`}
                                data={columnOptions}
                                value={task.status}
                                onChange={(value) =>
                                  value &&
                                  handleStatusChange(
                                    task.id,
                                    value as KanbanStatus
                                  )
                                }
                                disabled={isStatusUpdating || isDeleting}
                                rightSection={
                                  isStatusUpdating ? (
                                    <Loader size='xs' />
                                  ) : undefined
                                }
                              />
                            </Stack>
                          </Card>
                        );
                      })}
                    </Stack>
                  )}
                </Stack>
              </Paper>
            );
          })}
        </SimpleGrid>
      )}

      <Modal
        opened={columnDeletionContext !== null}
        onClose={closeDeleteColumnModal}
        title={t`Delete column`}
        size='sm'
        fullScreen={isSmallScreen}
      >
        {columnDeletionContext && (
          <Stack gap='md'>
            <Text>
              {t`Deleting ${columnDeletionContext.column.label} moves all of its tasks to ${columnDeletionContext.fallbackColumn.label}. Continue?`}
            </Text>
            <Group justify='flex-end'>
              <Button
                variant='default'
                onClick={closeDeleteColumnModal}
                type='button'
              >
                {t`Cancel`}
              </Button>
              <Button
                color='red'
                onClick={() =>
                  handleDeleteColumn(columnDeletionContext.column.id)
                }
                type='button'
              >
                {t`Delete column`}
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <WorkOrderCreateModal
        opened={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        origin='manual'
        onCreated={handleWorkPackageCreated}
      />

      <Modal
        opened={createdWorkPackage !== null}
        onClose={() => setCreatedWorkPackage(null)}
        title={t`Work order created`}
        size='sm'
        fullScreen={isSmallScreen}
      >
        {createdWorkPackage && (
          <Stack gap='md'>
            <Text>
              {t`Created ${createdWorkPackage.work_order_reference}. It is planned, not started.`}
            </Text>
            <Group justify='flex-end'>
              <Button
                variant='default'
                type='button'
                onClick={() => setCreatedWorkPackage(null)}
              >
                {t`Stay on the board`}
              </Button>
              {createdWorkPackage.repair_packet_id && (
                <Button
                  variant='light'
                  type='button'
                  onClick={() =>
                    navigate(
                      `/repair/packets/${createdWorkPackage.repair_packet_id}/`
                    )
                  }
                >
                  {t`Open repair packet`}
                </Button>
              )}
              <Button
                type='button'
                onClick={() =>
                  navigate(
                    `/maintenance/work-orders/${createdWorkPackage.work_order_id}/`
                  )
                }
              >
                {t`Open work order`}
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <Modal
        opened={taskModalOpen}
        onClose={closeTaskModal}
        title={t`Edit work order`}
        size='lg'
        fullScreen={isSmallScreen}
      >
        <form onSubmit={handleTaskSubmit}>
          <Stack gap='md'>
            {editingTask && (
              <Group justify='flex-end'>
                <ScopedChatButton
                  contextType='work_order'
                  objectId={editingTask.id}
                />
              </Group>
            )}
            <TextInput
              label={t`Title`}
              placeholder={t`Summarize the work in one line`}
              withAsterisk
              {...taskForm.getInputProps('title')}
            />
            <Textarea
              label={t`Description`}
              placeholder={t`What needs to get done?`}
              minRows={3}
              {...taskForm.getInputProps('description')}
            />
            <Select
              label={t`Machine`}
              placeholder={
                machinesQuery.isLoading
                  ? t`Loading machines…`
                  : t`Select the machine this work is for`
              }
              data={machineOptions}
              searchable
              withAsterisk
              nothingFoundMessage={t`No machines found`}
              disabled={machinesQuery.isLoading}
              {...taskForm.getInputProps('machine')}
            />
            <Group align='flex-end' gap='md'>
              <Select
                label={t`Status`}
                data={columnOptions}
                placeholder={t`Select column`}
                withAsterisk
                style={{ flex: 1 }}
                {...taskForm.getInputProps('status')}
              />
              <Select
                label={t`Priority`}
                data={[
                  { value: 'low', label: t`Low` },
                  { value: 'medium', label: t`Medium` },
                  { value: 'high', label: t`High` }
                ]}
                style={{ flex: 1 }}
                {...taskForm.getInputProps('priority')}
              />
            </Group>
            <Group align='flex-end' gap='md'>
              <TextInput
                label={t`Assignee`}
                placeholder={t`Who owns this work?`}
                style={{ flex: 1 }}
                {...taskForm.getInputProps('assignee')}
              />
              <DateInput
                label={t`Due date`}
                placeholder={t`Pick a date`}
                valueFormat='MMM D, YYYY'
                style={{ flex: 1 }}
                {...taskForm.getInputProps('dueDate')}
              />
            </Group>
            <Group align='flex-end' gap='md'>
              <TextInput
                label={t`Company`}
                placeholder={t`Customer or organization`}
                style={{ flex: 1 }}
                {...taskForm.getInputProps('company')}
              />
              <TextInput
                label={t`Job number`}
                placeholder={t`Link work to a job or project`}
                style={{ flex: 1 }}
                {...taskForm.getInputProps('jobNumber')}
              />
            </Group>
            <Group align='flex-end' gap='md'>
              <TextInput
                label={t`Company contact name`}
                placeholder={t`Primary point of contact`}
                style={{ flex: 1 }}
                {...taskForm.getInputProps('companyContactName')}
              />
              <TextInput
                label={t`Company contact phone`}
                placeholder={t`Phone number`}
                style={{ flex: 1 }}
                {...taskForm.getInputProps('companyContactPhone')}
              />
            </Group>
            <TextInput
              label={t`Associated service quote`}
              placeholder={t`Reference quote or agreement`}
              {...taskForm.getInputProps('serviceQuote')}
            />

            {/* ── Parts picker ─────────────────────────── */}
            <Stack gap='xs'>
              <Text size='sm' fw={500}>{t`Parts needed`}</Text>

              <TextInput
                placeholder={t`Search parts by name or IPN...`}
                value={partSearch}
                onChange={(e) => setPartSearch(e.currentTarget.value)}
                rightSection={
                  partSearchLoading ? <Loader size='xs' /> : undefined
                }
              />

              {partSearchResults.length > 0 && (
                <Paper
                  withBorder
                  p='xs'
                  style={{ maxHeight: 160, overflowY: 'auto' }}
                >
                  <Stack gap={2}>
                    {partSearchResults.map((result) => (
                      <Button
                        key={result.pk}
                        variant='subtle'
                        size='xs'
                        justify='flex-start'
                        fullWidth
                        onClick={() =>
                          addFormPart(
                            result.pk,
                            result.IPN
                              ? `${result.name} (${result.IPN})`
                              : result.name
                          )
                        }
                        disabled={formParts.some(
                          (fp) => fp.partId === result.pk
                        )}
                      >
                        {result.IPN
                          ? `${result.name}  •  ${result.IPN}`
                          : result.name}
                      </Button>
                    ))}
                  </Stack>
                </Paper>
              )}

              {formParts.length > 0 && (
                <Stack gap='xs'>
                  {formParts.map((fp) => (
                    <Group key={fp.partId} gap='xs' align='center'>
                      <Text size='sm' style={{ flex: 1 }} lineClamp={1}>
                        {fp.partName}
                      </Text>
                      <NumberInput
                        size='xs'
                        min={1}
                        value={fp.quantity}
                        onChange={(val) =>
                          updateFormPartQty(
                            fp.partId,
                            typeof val === 'number' ? val : 1
                          )
                        }
                        style={{ width: 80 }}
                        placeholder={t`Qty`}
                      />
                      {fp.allocationStatus && (
                        <Badge
                          size='xs'
                          color={
                            fp.allocationStatus === 'full'
                              ? 'green'
                              : fp.allocationStatus === 'partial'
                                ? 'yellow'
                                : fp.allocationStatus === 'insufficient'
                                  ? 'red'
                                  : 'gray'
                          }
                          variant='light'
                        >
                          {fp.allocationStatus}
                        </Badge>
                      )}
                      <CloseButton
                        size='xs'
                        onClick={() => removeFormPart(fp.partId)}
                        aria-label={t`Remove part`}
                      />
                    </Group>
                  ))}
                </Stack>
              )}
            </Stack>

            <MultiSelect
              label={t`Tags`}
              placeholder={t`Add labels to group related work`}
              data={tagData}
              searchable
              {...taskForm.getInputProps('tags')}
            />
            <Group gap='sm' align='flex-end'>
              <TextInput
                label={t`New tag`}
                placeholder={t`Add another tag option`}
                value={newTagName}
                onChange={(event) => setNewTagName(event.currentTarget.value)}
                style={{ flex: 1 }}
              />
              <Button
                type='button'
                onClick={handleAddTagOption}
                disabled={newTagName.trim().length === 0}
              >
                {t`Add tag`}
              </Button>
            </Group>
            <Group justify='flex-end'>
              <Button variant='default' onClick={closeTaskModal} type='button'>
                {t`Cancel`}
              </Button>
              <Button type='submit' loading={savingTask}>
                {t`Save changes`}
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>

      <Modal
        opened={columnModalOpen}
        onClose={closeColumnModal}
        title={t`Add column`}
        size='sm'
        fullScreen={isSmallScreen}
      >
        <form onSubmit={handleColumnSubmit}>
          <Stack gap='md'>
            <TextInput
              label={t`Name`}
              placeholder={t`How should this stage be called?`}
              withAsterisk
              {...columnForm.getInputProps('label')}
            />
            <Select
              label={t`Color`}
              data={colorOptions.map((color) => ({
                value: color,
                label: color.charAt(0).toUpperCase() + color.slice(1)
              }))}
              {...columnForm.getInputProps('color')}
            />
            <Group justify='flex-end'>
              <Button
                variant='default'
                onClick={closeColumnModal}
                type='button'
              >
                {t`Cancel`}
              </Button>
              <Button type='submit'>{t`Create column`}</Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
