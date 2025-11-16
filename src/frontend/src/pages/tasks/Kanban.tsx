import { t } from '@lingui/core/macro';
import {
	ActionIcon,
	Badge,
	Button,
	Card,
	Group,
	Loader,
	Modal,
	MultiSelect,
	Paper,
	Select,
	SimpleGrid,
	Stack,
	Text,
	Textarea,
	TextInput
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import {
	IconArrowLeft,
	IconArrowRight,
	IconArrowsSort,
	IconCircleCheck,
	IconDeviceFloppy,
	IconPencil,
	IconPlus,
	IconTrash,
	IconX
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { type DragEvent, useEffect, useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { KanbanCard, KanbanPriority, KanbanStatus } from '@lib/types/Tasks';

import PageTitle from '../../components/nav/PageTitle';
import { useApi } from '../../contexts/ApiContext';
import { showApiErrorMessage } from '../../functions/notifications';

type PriorityFilterValue = KanbanPriority | 'all';

interface Task {
	id: number;
	title: string;
	description: string;
	status: KanbanStatus;
	priority: KanbanPriority;
	dueDate: string | null;
	assignee: string;
	tags: string[];
	company: string;
	companyContactName: string;
	companyContactPhone: string;
	jobNumber: string;
	serviceQuote: string;
	createdAt: string;
	updatedAt: string;
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

const colorOptions = ['gray', 'blue', 'indigo', 'violet', 'teal', 'green', 'orange', 'red'];

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

const convertCardToTask = (card: KanbanCard): Task => ({
	id: card.id,
	title: card.title,
	description: card.description ?? '',
	status: card.status as KanbanStatus,
	priority: card.priority as KanbanPriority,
	dueDate: card.due_date,
	assignee: card.assignee ?? '',
	tags: card.tags ?? [],
	company: card.company ?? '',
	companyContactName: card.company_contact_name ?? '',
	companyContactPhone: card.company_contact_phone ?? '',
	jobNumber: card.job_number ?? '',
	serviceQuote: card.service_quote ?? '',
	createdAt: card.created_at,
	updatedAt: card.updated_at
});

const formValuesToPayload = (values: TaskFormValues) => ({
	title: values.title,
	description: values.description,
	status: values.status,
	priority: values.priority,
	due_date: values.dueDate ? dayjs(values.dueDate).format('YYYY-MM-DD') : null,
	assignee: values.assignee,
	tags: values.tags,
	company: values.company,
	company_contact_name: values.companyContactName,
	company_contact_phone: values.companyContactPhone,
	job_number: values.jobNumber,
	service_quote: values.serviceQuote
});

export default function Kanban() {
	const api = useApi();
	const queryClient = useQueryClient();

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
	const [pendingColumnOrder, setPendingColumnOrder] = useState<Column[]>(defaultColumns);
	const [columnDeletionContext, setColumnDeletionContext] = useState<ColumnDeletionContext | null>(
		null
	);
	const [draggingTaskId, setDraggingTaskId] = useState<number | null>(null);
	const [dragOverColumnId, setDragOverColumnId] = useState<string | null>(null);
	const [savingTask, setSavingTask] = useState(false);
	const [deletingTaskId, setDeletingTaskId] = useState<number | null>(null);
	const [statusUpdating, setStatusUpdating] = useState<Set<number>>(new Set());

	const cardsQuery = useQuery<KanbanCard[], Error, KanbanCard[], ['kanban-cards']>({
		queryKey: ['kanban-cards'],
		queryFn: async () => {
			try {
				const response = await api.get<KanbanCard[]>(apiUrl(ApiEndpoints.kanban_card_list));
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

	useEffect(() => {
		const mapped = (cardsQuery.data ?? []).map(convertCardToTask);
		setTasks(mapped);
	}, [cardsQuery.data]);

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
			const combined = Array.from(new Set([...DEFAULT_TAG_OPTIONS, ...current, ...serverTags]));

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
			tags: [],
			dueDate: null,
			company: '',
			companyContactName: '',
			companyContactPhone: '',
			jobNumber: '',
			serviceQuote: ''
		},
		validate: {
			title: (value) => (value.trim().length === 0 ? t`Give the task a descriptive title.` : null),
			status: (value) => (value ? null : t`Choose a column for this task.`)
		}
	});

	const columnForm = useForm<ColumnFormValues>({
		initialValues: {
			label: '',
			color: 'gray'
		},
		validate: {
			label: (value) => (value.trim().length === 0 ? t`Name cannot be empty.` : null)
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
			new Set(tasks.map((task) => task.jobNumber).filter((job): job is string => Boolean(job)))
		);

		return [{ value: 'all', label: t`All jobs` }, ...jobNumbers.map((job) => ({ value: job, label: job }))];
	}, [tasks]);

	const assigneeFilterOptions = useMemo(() => {
		const assignees = Array.from(
			new Set(tasks.map((task) => task.assignee).filter((assignee): assignee is string => Boolean(assignee)))
		);

		return [{ value: 'all', label: t`All employees` }, ...assignees.map((assignee) => ({ value: assignee, label: assignee }))];
	}, [tasks]);

	const serviceQuoteFilterOptions = useMemo(() => {
		const quotes = Array.from(
			new Set(tasks.map((task) => task.serviceQuote).filter((quote): quote is string => Boolean(quote)))
		);

		return [{ value: 'all', label: t`All service quotes` }, ...quotes.map((quote) => ({ value: quote, label: quote }))];
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

				if (tagFilter.length > 0 && !tagFilter.every((tag) => task.tags.includes(tag))) {
					return false;
				}

				if (assigneeFilter !== 'all' && task.assignee !== assigneeFilter) {
					return false;
				}

				if (jobNumberFilter !== 'all' && task.jobNumber !== jobNumberFilter) {
					return false;
				}

				if (serviceQuoteFilter !== 'all' && task.serviceQuote !== serviceQuoteFilter) {
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
		[searchFilter, columnFilter, priorityFilter, tagFilter, assigneeFilter, jobNumberFilter, serviceQuoteFilter]
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

		return pendingColumnOrder.some((column, index) => column.id !== columns[index]?.id);
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

	const openCreateTaskModal = () => {
		const firstColumn = columns[0]?.id ?? 'backlog';

		setEditingTask(null);
		taskForm.setValues({
			title: '',
			description: '',
			status: firstColumn as KanbanStatus,
			priority: 'medium',
			assignee: '',
			tags: [],
			dueDate: null,
			company: '',
			companyContactName: '',
			companyContactPhone: '',
			jobNumber: '',
			serviceQuote: ''
		});
		taskForm.resetDirty();
		setTaskModalOpen(true);
	};

	const openEditTaskModal = (task: Task) => {
		setEditingTask(task);
		taskForm.setValues({
			title: task.title,
			description: task.description,
			status: task.status,
			priority: task.priority,
			assignee: task.assignee,
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
		taskForm.reset();
		setSavingTask(false);
	};

	const handleTaskSubmit = taskForm.onSubmit(async (values) => {
		const payload = formValuesToPayload(values);

		setSavingTask(true);

		try {
			if (editingTask) {
				const response = await api.put(
					apiUrl(ApiEndpoints.kanban_card_detail, editingTask.id),
					payload
				);
				const updated = convertCardToTask(response.data);

				setTasks((current) =>
					current.map((task) => (task.id === updated.id ? updated : task))
				);

				notifications.show({
					title: t`Task updated`,
					message: t`Changes saved successfully.`,
					color: 'green',
					icon: <IconCircleCheck size={16} />
				});
			} else {
				const response = await api.post(apiUrl(ApiEndpoints.kanban_card_list), payload);
				const created = convertCardToTask(response.data);

				setTasks((current) => [...current, created]);

				notifications.show({
					title: t`Task created`,
					message: t`The card was added to the board.`,
					color: 'green',
					icon: <IconCircleCheck size={16} />
				});
			}

			await queryClient.invalidateQueries({ queryKey: ['kanban-cards'] });
			closeTaskModal();
		} catch (error) {
			showApiErrorMessage({
				error,
				title: editingTask ? t`Could not update task` : t`Could not create task`
			});
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
			const response = await api.patch(apiUrl(ApiEndpoints.kanban_card_detail, taskId), {
				status
			});
			const updated = convertCardToTask(response.data);

			setTasks((current) =>
				current.map((task) => (task.id === updated.id ? updated : task))
			);
			await queryClient.invalidateQueries({ queryKey: ['kanban-cards'] });
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

		try {
			await api.delete(apiUrl(ApiEndpoints.kanban_card_detail, taskId));

			setTasks((current) => current.filter((task) => task.id !== taskId));

			notifications.show({
				title: t`Task archived`,
				message: t`The card is no longer visible on the board.`,
				color: 'green',
				icon: <IconCircleCheck size={16} />
			});

			await queryClient.invalidateQueries({ queryKey: ['kanban-cards'] });
		} catch (error) {
			showApiErrorMessage({
				error,
				title: t`Could not delete task`
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

	const handleTaskDragStart = (event: DragEvent<HTMLDivElement>, taskId: number) => {
		event.dataTransfer.setData('text/plain', String(taskId));
		event.dataTransfer.effectAllowed = 'move';
		setDraggingTaskId(taskId);
	};

	const handleTaskDragEnd = () => {
		setDraggingTaskId(null);
		setDragOverColumnId(null);
	};

	const handleColumnDragOver = (event: DragEvent<HTMLDivElement>, columnId: string) => {
		if (draggingTaskId == null) {
			return;
		}

		event.preventDefault();
		event.dataTransfer.dropEffect = 'move';

		if (dragOverColumnId !== columnId) {
			setDragOverColumnId(columnId);
		}
	};

	const handleColumnDragLeave = (event: DragEvent<HTMLDivElement>, columnId: string) => {
		const relatedTarget = event.relatedTarget as Node | null;

		if (!relatedTarget || !event.currentTarget.contains(relatedTarget)) {
			setDragOverColumnId((current) => (current === columnId ? null : current));
		}
	};

	const handleColumnDrop = async (event: DragEvent<HTMLDivElement>, columnId: string) => {
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
		const affectedTasks = tasks.filter((task) => task.status === columnId);

		await Promise.all(
			affectedTasks.map((task) =>
				handleStatusChange(task.id, fallbackId as KanbanStatus)
			)
		);

		setColumns((currentColumns) =>
			currentColumns.filter((column) => column.id !== columnId)
		);

		setFilters((currentFilters) => ({
			...currentFilters,
			column: currentFilters.column === columnId ? 'all' : currentFilters.column
		}));

		setColumnDeletionContext(null);
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

	const handleColumnSubmit = columnForm.onSubmit((values) => {
		const nextSlug = slugify(values.label);

		if (columns.some((column) => column.id === nextSlug)) {
			columnForm.setFieldError('label', t`Choose a unique name.`);
			return;
		}

		setColumns((current) => [
			...current,
			{
				id: nextSlug,
				label: values.label,
				color: values.color
			}
		]);

		closeColumnModal();
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

	const saveColumnOrder = () => {
		setColumns([...pendingColumnOrder]);
		setIsReordering(false);
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
			<PageTitle title={t`Kanban`} />
			<Text>{t`Use this board to group tasks by stage, keep ownership visible, and iterate quickly.`}</Text>
			<Group justify='space-between' align='flex-start'>
				<Group gap='sm'>
					<Button leftSection={<IconPlus size={16} />} onClick={openCreateTaskModal}>
						{t`Add task`}
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
							<Button variant='default' leftSection={<IconX size={16} />} onClick={cancelReorderMode}>
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
								setFilters((current) => ({ ...current, column: value ?? 'all' }))
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
							onChange={(value) => setFilters((current) => ({ ...current, tags: value }))}
							searchable
							style={{ minWidth: '220px', flex: 1 }}
						/>
						<Select
							size='sm'
							label={t`Employee`}
							data={assigneeFilterOptions}
							value={assigneeFilter}
							onChange={(value) =>
								setFilters((current) => ({ ...current, assignee: value ?? 'all' }))
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
								setFilters((current) => ({ ...current, jobNumber: value ?? 'all' }))
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
								setFilters((current) => ({ ...current, serviceQuote: value ?? 'all' }))
							}
							style={{ minWidth: '200px' }}
							disabled={serviceQuoteFilterOptions.length <= 1}
						/>
					</Group>
					<Group justify='flex-end'>
						<Button variant='subtle' onClick={resetFilters} disabled={!filtersActive}>
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
					cols={{ base: 1, sm: smallBreakpointColumns, lg: largeBreakpointColumns }}
					spacing='lg'
				>
					{visibleColumns.map((column, index) => {
						const columnTasks = filteredTasks.filter((task) => task.status === column.id);
						const columnIndex = columns.findIndex((item) => item.id === column.id);

						return (
							<Paper
								key={column.id}
								withBorder
								radius='md'
								p='md'
								onDragOver={
									isReordering ? undefined : (event) => handleColumnDragOver(event, column.id)
								}
								onDragLeave={
									isReordering ? undefined : (event) => handleColumnDragLeave(event, column.id)
								}
								onDrop={
									isReordering ? undefined : (event) => handleColumnDrop(event, column.id)
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
														draggable={!isReordering && !isDeleting && !isStatusUpdating}
														onDragStart={
															isReordering
																? undefined
																: (event) => handleTaskDragStart(event, task.id)
														}
														onDragEnd={isReordering ? undefined : handleTaskDragEnd}
														style={
															!isReordering && draggingTaskId === task.id
																? { opacity: 0.4, cursor: 'grabbing' }
																: {
																		cursor:
																			isReordering || isDeleting || isStatusUpdating
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
																		aria-label={t`Edit task`}
																		onClick={() => openEditTaskModal(task)}
																		disabled={isDeleting}
																	>
																		<IconPencil size={16} />
																	</ActionIcon>
																	<ActionIcon
																		size='sm'
																		variant='subtle'
																		color='red'
																		aria-label={t`Delete task`}
																		onClick={() => handleDeleteTask(task.id)}
																		disabled={isDeleting}
																	>
																		{isDeleting ? <Loader size='xs' /> : <IconTrash size={16} />}
																	</ActionIcon>
																</Group>
															</Group>

															<Group gap='xs'>
																<Badge color={priorityColors[task.priority]} variant='light'>
																	{renderPriorityLabel(task.priority)}
																</Badge>
																{task.assignee && (
																	<Badge color='gray' variant='outline'>
																		{task.assignee}
																	</Badge>
																)}
																{task.dueDate && (
																	<Badge color={getDueBadgeColor(task.dueDate)} variant='light'>
																		{dayjs(task.dueDate).format('MMM D')}
																	</Badge>
																)}
															</Group>

															{task.tags.length > 0 && (
																<Group gap='xs'>
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

															<Select
																size='xs'
																label={t`Status`}
																data={columnOptions}
																value={task.status}
																onChange={(value) =>
																	value &&
																	handleStatusChange(task.id, value as KanbanStatus)
																}
																disabled={isStatusUpdating || isDeleting}
																rightSection={isStatusUpdating ? <Loader size='xs' /> : undefined}
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
			>
				{columnDeletionContext && (
					<Stack gap='md'>
						<Text>
							{t`Deleting ${columnDeletionContext.column.label} moves all of its tasks to ${columnDeletionContext.fallbackColumn.label}. Continue?`}
						</Text>
						<Group justify='flex-end'>
							<Button variant='default' onClick={closeDeleteColumnModal} type='button'>
								{t`Cancel`}
							</Button>
							<Button
								color='red'
								onClick={() => handleDeleteColumn(columnDeletionContext.column.id)}
								type='button'
							>
								{t`Delete column`}
							</Button>
						</Group>
					</Stack>
				)}
			</Modal>

			<Modal
				opened={taskModalOpen}
				onClose={closeTaskModal}
				title={editingTask ? t`Edit task` : t`New task`}
				size='lg'
			>
				<form onSubmit={handleTaskSubmit}>
					<Stack gap='md'>
						<TextInput
							label={t`Title`}
							placeholder={t`Add a concise task name`}
							withAsterisk
							{...taskForm.getInputProps('title')}
						/>
						<Textarea
							label={t`Description`}
							placeholder={t`What needs to get done?`}
							minRows={3}
							{...taskForm.getInputProps('description')}
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
								{editingTask ? t`Save changes` : t`Create task`}
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
							<Button variant='default' onClick={closeColumnModal} type='button'>
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
