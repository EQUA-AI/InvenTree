"""Admin configuration for assets."""

from django.contrib import admin

from .models import AssetMachine, AssetMaintenanceRecord, MachinePart


@admin.register(AssetMachine)
class AssetMachineAdmin(admin.ModelAdmin):
    """Admin interface for AssetMachine."""

    list_display = (
        'name',
        'active',
        'location',
        'customer',
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
