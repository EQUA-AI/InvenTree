"""Serializers for the assets (equipment machines) application."""

from rest_framework import serializers

from .models import AssetMachine, AssetMaintenanceRecord, MachinePart


class AssetMachineSerializer(serializers.ModelSerializer):
    """Serializer for AssetMachine instances."""

    customer_name = serializers.CharField(
        source='customer.name', read_only=True, default=None
    )

    class Meta:
        model = AssetMachine
        fields = (
            'pk',
            'name',
            'description',
            'active',
            'location',
            'customer',
            'customer_name',
            'manufacturer',
            'model',
            'serial',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('pk', 'created_at', 'updated_at')


class MachinePartSerializer(serializers.ModelSerializer):
    """Serializer for MachinePart instances."""

    part_name = serializers.CharField(source='part.name', read_only=True)

    class Meta:
        model = MachinePart
        fields = (
            'pk',
            'machine',
            'part',
            'part_name',
            'quantity',
            'notes',
        )
        read_only_fields = ('pk',)


class AssetMaintenanceRecordSerializer(serializers.ModelSerializer):
    """Serializer for AssetMaintenanceRecord instances."""

    work_order_title = serializers.CharField(
        source='work_order.title', read_only=True, default=None
    )

    class Meta:
        model = AssetMaintenanceRecord
        fields = (
            'pk',
            'machine',
            'date',
            'summary',
            'details',
            'performed_by',
            'work_order',
            'work_order_title',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('pk', 'created_at', 'updated_at')
