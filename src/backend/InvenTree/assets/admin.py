"""Admin configuration for assets."""

from django.contrib import admin

from .models import (
    AssetMachine,
    AssetMaintenanceRecord,
    Client,
    HealthSource,
    IngestionCheckpoint,
    MachinePart,
)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    """Admin interface for Client (the system-only tenant identity)."""

    list_display = ('name', 'code', 'active', 'updated_at')
    list_filter = ('active',)
    search_fields = ('name', 'code')
    ordering = ('name',)


@admin.register(AssetMachine)
class AssetMachineAdmin(admin.ModelAdmin):
    """Admin interface for AssetMachine."""

    list_display = (
        'name',
        'active',
        'location',
        'client',
        'manufacturer',
        'model',
        'serial',
        'updated_at',
    )
    list_filter = ('active', 'manufacturer')
    search_fields = (
        'name',
        'description',
        'location',
        'manufacturer',
        'model',
        'serial',
    )
    ordering = ('name',)


@admin.register(MachinePart)
class MachinePartAdmin(admin.ModelAdmin):
    """Admin interface for MachinePart."""

    list_display = ('machine', 'part', 'quantity')
    list_filter = ('machine',)
    search_fields = ('machine__name', 'part__name')
    ordering = ('machine__name', 'part__name')


@admin.register(AssetMaintenanceRecord)
class AssetMaintenanceRecordAdmin(admin.ModelAdmin):
    """Admin interface for AssetMaintenanceRecord."""

    list_display = ('machine', 'date', 'summary', 'performed_by', 'work_order')
    list_filter = ('machine',)
    search_fields = ('summary', 'details', 'performed_by')
    ordering = ('-date',)


@admin.register(IngestionCheckpoint)
class IngestionCheckpointAdmin(admin.ModelAdmin):
    """Read-mostly view of how far each source has been read.

    Positions are visible for diagnosis but not editable here: moving one by hand
    would either skip samples or replay them, and the model refuses a rewind in
    code precisely so nobody can do it by accident through a form.
    """

    list_display = (
        'source',
        'active',
        'station_uuid',
        'hour_bucket',
        'sub_time_period',
        'last_poll_at',
        'last_error_code',
        'updated_at',
    )
    list_filter = ('source',)
    search_fields = ('station_uuid',)
    ordering = ('source__name', 'station_uuid')
    readonly_fields = (
        'source',
        'active',
        'station_uuid',
        'hour_bucket',
        'sub_time_period',
        'continuation_token',
        'station',
        'scan_until',
        'last_poll_at',
        'last_success_at',
        'last_error_at',
        'last_error_code',
        'lease_until',
        'updated_at',
    )

    def has_add_permission(self, request) -> bool:
        """Checkpoints are created by the poller, never by hand."""
        return False


@admin.register(HealthSource)
class HealthSourceAdmin(admin.ModelAdmin):
    """Deployment-admin configuration; station APIs never expose connection config."""

    list_display = ('name', 'client', 'connector_type', 'active')
    list_filter = ('client', 'connector_type', 'active')
    readonly_fields = (
        'last_success_at',
        'last_error_at',
        'last_error_code',
        'created_at',
        'updated_at',
    )
