"""Django admin configuration for the Approvals app."""

from django.contrib import admin

from .models import Approval, ApprovalEvent, ApprovalRevision, ExecutedEffect


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    """Admin for Approval model."""

    list_display = [
        'id',
        'status',
        'risk_tier',
        'action_type',
        'summary',
        'assigned_to_user',
        'created_at',
        'expires_at',
        'resolved_at',
    ]
    list_filter = ['status', 'risk_tier', 'action_type']
    search_fields = ['summary', 'agent_run_id', 'tool_call_id', 'idempotency_key']
    readonly_fields = [
        'id',
        'idempotency_key',
        'created_at',
        'updated_at',
        'resolved_at',
    ]
    raw_id_fields = [
        'assigned_to_user',
        'viewed_confirmed_by_user',
        'modification_lock_user',
        'resolved_by_user',
    ]
    date_hierarchy = 'created_at'
    ordering = ['-created_at']


@admin.register(ApprovalEvent)
class ApprovalEventAdmin(admin.ModelAdmin):
    """Admin for ApprovalEvent model."""

    list_display = ['id', 'approval', 'event_type', 'actor_user', 'timestamp']
    list_filter = ['event_type']
    search_fields = ['approval__id']
    readonly_fields = ['id', 'timestamp']
    raw_id_fields = ['approval', 'actor_user']
    ordering = ['-timestamp']


@admin.register(ApprovalRevision)
class ApprovalRevisionAdmin(admin.ModelAdmin):
    """Admin for ApprovalRevision model."""

    list_display = [
        'id',
        'approval',
        'revision_number',
        'created_at',
        'created_by_user',
    ]
    search_fields = ['approval__id']
    readonly_fields = ['id', 'created_at']
    raw_id_fields = ['approval', 'created_by_user']
    ordering = ['approval', 'revision_number']


@admin.register(ExecutedEffect)
class ExecutedEffectAdmin(admin.ModelAdmin):
    """Admin for ExecutedEffect model."""

    list_display = [
        'idempotency_key',
        'approval',
        'effect_type',
        'effect_ref',
        'created_at',
    ]
    search_fields = ['idempotency_key', 'approval__id', 'effect_type']
    readonly_fields = ['created_at']
    raw_id_fields = ['approval']
    ordering = ['-created_at']
