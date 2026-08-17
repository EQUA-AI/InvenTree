"""Admin configuration for assets."""

from django.contrib import admin

from .models import AssetMachine, AssetMaintenanceRecord, Client, MachinePart


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    """Admin interface for Client (the system-only tenant identity)."""

    list_display = ('name', 'code', 'active', 'updated_at')
    list_filter = ('active',)
    search_fields = ('name', 'code')
    ordering = ('name',)

    def get_readonly_fields(self, request, obj=None):
        """The code is the scope-token identifier — immutable once created."""
        if obj is not None:
            return (*self.readonly_fields, 'code')
        return self.readonly_fields


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
