"""Admin configuration for tasks."""

from django.contrib import admin

from .models import KanbanColumn, WorkOrder


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    """Admin interface for maintenance work orders."""

    list_display = (
        'title',
        'status',
        'priority',
        'assignee',
        'due_date',
        'is_active',
        'updated_at',
    )
    list_filter = ('status', 'priority', 'is_active')
    search_fields = (
        'title',
        'description',
        'assignee',
        'company',
        'job_number',
        'service_quote',
    )
    ordering = ('-updated_at',)


@admin.register(KanbanColumn)
class KanbanColumnAdmin(admin.ModelAdmin):
    """Admin interface for board columns."""

    list_display = ('label', 'key', 'color', 'order', 'is_default')
    list_editable = ('order',)
    ordering = ('order', 'key')
