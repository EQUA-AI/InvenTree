"""Serializers for the assets (equipment machines) application."""

from django.conf import settings

from rest_framework import serializers
from tasks.scope import ScopeError, require_work_order_scope

from .models import (
    AssetMachine,
    AssetMaintenanceRecord,
    Client,
    MachinePart,
    get_default_client,
)


class AssetMachineSerializer(serializers.ModelSerializer):
    """Serializer for AssetMachine instances.

    The client is exposed as a bare id only: it is a system identity used for
    scope resolution, never rendered by the frontend.
    """

    class Meta:
        """Metaclass defining serializer fields."""

        model = AssetMachine
        fields = (
            'pk',
            'name',
            'description',
            'active',
            'location',
            'client',
            'manufacturer',
            'model',
            'serial',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('pk', 'created_at', 'updated_at')

    def create(self, validated_data):
        """Ensure every machine created through the API carries a client."""
        if validated_data.get('client') is None:
            validated_data['client'] = get_default_client()
        return super().create(validated_data)


class MachinePartSerializer(serializers.ModelSerializer):
    """Serializer for MachinePart instances."""

    part_name = serializers.CharField(source='part.name', read_only=True)

    class Meta:
        """Metaclass defining serializer fields."""

        model = MachinePart
        fields = ('pk', 'machine', 'part', 'part_name', 'quantity', 'notes')
        read_only_fields = ('pk',)


class AssetMaintenanceRecordSerializer(serializers.ModelSerializer):
    """Serializer for AssetMaintenanceRecord instances.

    The machine Maintenance blade is a job-history index, not a second copy of
    the work order: it projects just enough of the linked card to render a row
    and an authoritative link. Every work-order field here is read-only and
    derived, so a history row can never rewrite completion truth.

    When the caller may read the machine history but not the linked work order,
    the whole link - including the id - is withheld rather than rendered as a
    dead reference.
    """

    work_order_reference = serializers.SerializerMethodField()
    work_order_title = serializers.SerializerMethodField()
    work_order_type = serializers.SerializerMethodField()
    lifecycle_status = serializers.SerializerMethodField()
    actual_completed_at = serializers.SerializerMethodField()
    downtime_minutes = serializers.SerializerMethodField()
    verified = serializers.SerializerMethodField()
    follow_up_required = serializers.SerializerMethodField()

    class Meta:
        """Metaclass defining serializer fields."""

        model = AssetMaintenanceRecord
        fields = (
            'pk',
            'machine',
            'date',
            'summary',
            'details',
            'performed_by',
            'work_order',
            'work_order_reference',
            'work_order_title',
            'work_order_type',
            'lifecycle_status',
            'actual_completed_at',
            'downtime_minutes',
            'verified',
            'follow_up_required',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('pk', 'created_at', 'updated_at')

    def _visible_work_order(self, record):
        """Return the linked work order if this caller may see it.

        Memoised per record: every projected field asks the same question, and
        the scope check is not free once the canonical API is enabled.
        """
        cache = self.__dict__.setdefault('_visible_cache', {})
        if id(record) in cache:
            return cache[id(record)]

        cache[id(record)] = work_order = self._resolve_visible_work_order(record)
        return work_order

    def _resolve_visible_work_order(self, record):
        """Apply the scope rule for one record's work-order link."""
        work_order = record.work_order
        if work_order is None:
            return None

        # Customer/site scope is a flagged-canonical-API concern: while the
        # canonical work-order API is off, this surface is governed by the
        # ``work_order`` ruleset at the endpoint, and imposing scope here would
        # hide every link from actors without configured maintenance scopes.
        # See the scope note in ``tasks.services.scheduling``.
        if not getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False):
            return work_order

        request = self.context.get('request')
        try:
            require_work_order_scope(getattr(request, 'user', None), work_order)
        except ScopeError:
            return None
        return work_order

    @staticmethod
    def _closeout(work_order):
        """Return the structured closeout for a work order, if one exists."""
        if work_order is None:
            return None
        return getattr(work_order, 'structured_closeout', None)

    def to_representation(self, instance):
        """Withhold the work-order id when the link itself is not visible."""
        data = super().to_representation(instance)
        if self._visible_work_order(instance) is None:
            data['work_order'] = None
        return data

    def get_work_order_reference(self, record) -> str | None:
        """Human-facing work-order reference for the row link."""
        work_order = self._visible_work_order(record)
        return work_order.reference if work_order else None

    def get_work_order_title(self, record) -> str | None:
        """Title of the linked work order."""
        work_order = self._visible_work_order(record)
        return work_order.title if work_order else None

    def get_work_order_type(self, record) -> str | None:
        """Maintenance type (corrective, preventive, inspection, ...)."""
        work_order = self._visible_work_order(record)
        return work_order.work_order_type if work_order else None

    def get_lifecycle_status(self, record) -> str | None:
        """Work-order lifecycle outcome; distinct from the board stage."""
        work_order = self._visible_work_order(record)
        return work_order.lifecycle_status if work_order else None

    def get_actual_completed_at(self, record) -> str | None:
        """Actual completion instant, which may differ from the history date."""
        work_order = self._visible_work_order(record)
        completed_at = work_order.actual_completed_at if work_order else None
        return completed_at.isoformat() if completed_at else None

    def get_downtime_minutes(self, record) -> int | None:
        """Downtime captured by the structured closeout, when recorded."""
        closeout = self._closeout(self._visible_work_order(record))
        return closeout.downtime_minutes if closeout else None

    def get_verified(self, record) -> bool:
        """Whether a supervisor verified the closeout (return to service)."""
        closeout = self._closeout(self._visible_work_order(record))
        return bool(closeout and closeout.verified_at)

    def get_follow_up_required(self, record) -> bool:
        """Whether the closeout raised follow-up work."""
        closeout = self._closeout(self._visible_work_order(record))
        return bool(closeout and closeout.follow_up_required)


class ClientSerializer(serializers.ModelSerializer):
    """Serializer for Client instances - the tenants of this software."""

    machine_count = serializers.IntegerField(source='machines.count', read_only=True)

    class Meta:
        """Metaclass defining serializer fields."""

        model = Client
        fields = (
            'pk',
            'name',
            'code',
            'active',
            'machine_count',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('pk', 'created_at', 'updated_at')
