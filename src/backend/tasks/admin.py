"""Admin configuration for tasks."""

from django.contrib import admin

from .models import KanbanCard


@admin.register(KanbanCard)
class KanbanCardAdmin(admin.ModelAdmin):
    """Admin interface for Kanban cards."""

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
