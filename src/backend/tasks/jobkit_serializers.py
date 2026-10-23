"""Serializers for the Job Kit planning REST API."""

from rest_framework import serializers

from .jobkit_models import (
    JobKit,
    JobKitAllocation,
    JobKitLine,
    JobKitShortage,
    JobKitSubstitution,
)


def _receipt_time(value):
    """Return a zoned instant even on native deployments with USE_TZ disabled."""
    from tasks.services.procurement import procurement_instant

    return procurement_instant(value)


class JobKitLineSerializer(serializers.ModelSerializer):
    """Read-only representation of a planned Job Kit line."""

    class Meta:
        """Serializer metadata."""

        model = JobKitLine
        fields = (
            'id',
            'kit',
            'key',
            'sequence',
            'kind',
            'requested_part',
            'selected_part',
            'required_quantity',
            'required',
            'fulfillment_mode',
            'substitution_policy',
            'requires_scan',
            'source',
            'source_requirement',
            'note',
            'version',
        )
        read_only_fields = fields


class JobKitSerializer(serializers.ModelSerializer):
    """Read-only Job Kit with its ordered planned lines."""

    lines = serializers.SerializerMethodField()
    receipts = serializers.SerializerMethodField()

    def to_representation(self, instance):
        """Keep staging stock identity behind the Materials stock boundary."""
        from .materials_readout import can_read_work_stock

        data = super().to_representation(instance)
        request = self.context.get('request')
        work_order = self.context.get('work_order') or instance.work_order
        if not request or not can_read_work_stock(request.user, work_order):
            data['staging_location'] = None
        return data

    class Meta:
        """Serializer metadata."""

        model = JobKit
        fields = (
            'id',
            'work_order',
            'status',
            'version',
            'source_application_hash',
            'built_at',
            'staged_at',
            'released_at',
            'closed_at',
            'staging_location',
            'created_by',
            'created_at',
            'updated_at',
            'lines',
            'receipts',
        )
        read_only_fields = fields

    def get_receipts(self, obj):
        """Read durable receipt facts without triggering reservation or refresh."""
        from users.permissions import check_user_role

        from .materials_readout import can_read_work_stock
        from .receipt_models import MaintenanceReceipt
        from .services.procurement import can_read_procurement_event

        request = self.context.get('request')
        work_order = self.context.get('work_order') or obj.work_order
        if (
            not request
            or not can_read_work_stock(request.user, work_order)
            or not check_user_role(request.user, 'purchase_order', 'view')
        ):
            return []
        return [
            {
                'id': receipt.pk,
                'operation_key': receipt.operation_key,
                'request': receipt.request_snapshot,
                'event': receipt.event_id,
                'kit_line': receipt.kit_line_id,
                'purchase_order_line': receipt.purchase_order_line_id,
                'shortage': receipt.shortage_id,
                'actor': receipt.actor_id,
                'recorded_at': _receipt_time(receipt.recorded_at),
                'received_before': str(receipt.received_before),
                'received_after': str(receipt.received_after),
                'stocks': [row.snapshot for row in receipt.stocks.all()],
                'reservation_state': receipt.reservation_state,
                'reservation_code': receipt.reservation_code,
                'allocation_ids': receipt.allocation_ids,
                'continued_at': _receipt_time(receipt.continued_at),
            }
            for receipt in MaintenanceReceipt.objects
            .filter(work_order=work_order)
            .select_related('event')
            .prefetch_related('stocks')
            .order_by('pk')
            if can_read_procurement_event(request.user, receipt.event, work_order)
        ]

    def get_lines(self, obj):
        """Return the kit's lines ordered deterministically."""
        lines = (
            obj.lines.all()
            if hasattr(obj, '_prefetched_objects_cache')
            else (obj.lines.order_by('sequence', 'pk'))
        )
        return JobKitLineSerializer(
            sorted(lines, key=lambda line: (line.sequence, line.pk)), many=True
        ).data


class JobKitShortageSerializer(serializers.ModelSerializer):
    """Read-only Job Kit shortage record."""

    class Meta:
        """Serializer metadata."""

        model = JobKitShortage
        fields = (
            'id',
            'line',
            'quantity',
            'status',
            'purchase_order_line',
            'approval',
            'reason',
            'created_at',
            'resolved_at',
        )
        read_only_fields = fields


class JobKitAllocationSerializer(serializers.ModelSerializer):
    """Read-only representation of a real Job Kit stock reservation."""

    def to_representation(self, instance):
        """Apply the same stock-identity boundary as the Materials projection."""
        data = super().to_representation(instance)
        request = self.context.get('request')
        from .materials_readout import can_read_work_stock

        work_order = self.context.get('work_order') or instance.line.kit.work_order
        visibility = self.context.setdefault('_stock_visibility', {})
        if work_order.pk not in visibility:
            visibility[work_order.pk] = bool(
                request and can_read_work_stock(request.user, work_order)
            )
        if not visibility[work_order.pk]:
            for field in (
                'stock_item',
                'source_location_snapshot',
                'stock_tracking_id',
            ):
                data[field] = None
        return data

    class Meta:
        """Serializer metadata."""

        model = JobKitAllocation
        fields = (
            'id',
            'line',
            'stock_item',
            'quantity',
            'status',
            'source_location_snapshot',
            'reserved_by',
            'reserved_at',
            'staged_by',
            'staged_at',
            'issued_at',
            'issued_by',
            'disposed_at',
            'stock_tracking_id',
            'idempotency_key',
        )
        read_only_fields = fields


class BuildJobKitCommandSerializer(serializers.Serializer):
    """Intent to deterministically build/reconcile the Job Kit."""

    expected_version = serializers.IntegerField(min_value=0)
    idempotency_key = serializers.CharField(max_length=128, allow_blank=False)


class ReserveJobKitCommandSerializer(serializers.Serializer):
    """Intent to atomically reserve stock for the Job Kit's required lines."""

    expected_version = serializers.IntegerField(min_value=0)
    idempotency_key = serializers.CharField(max_length=128, allow_blank=False)


class JobKitSubstitutionSerializer(serializers.ModelSerializer):
    """Read-only governed substitution proposal/decision resource."""

    class Meta:
        """Serializer metadata."""

        model = JobKitSubstitution
        fields = (
            'id',
            'line',
            'requested_part',
            'proposed_part',
            'basis',
            'status',
            'proposed_by',
            'decided_by',
            'approval',
            'decided_at',
            'reason',
            'created_at',
        )
        read_only_fields = fields


class ProposeSubstitutionSerializer(serializers.Serializer):
    """Intent to propose an alternate part for a line."""

    proposed_part_id = serializers.IntegerField(min_value=1)
    basis = serializers.JSONField(required=False, default=dict)
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class DecideSubstitutionSerializer(serializers.Serializer):
    """Intent to approve or reject a proposed substitution.

    ``confirmed_verification_id`` binds a current Right-Part Finder decision;
    it is required only for configured critical categories when RPF Job Kit
    enforcement is enabled, and is rechecked by the backend service.
    """

    approve = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    confirmed_verification_id = serializers.IntegerField(
        required=False, allow_null=True, default=None
    )


class LinkPurchaseOrderSerializer(serializers.Serializer):
    """Intent to link a real purchase-order line to a shortage."""

    purchase_order_line_id = serializers.IntegerField(min_value=1)


class AddManualLineSerializer(serializers.Serializer):
    """Intent to append an authorized manual planning line."""

    kind = serializers.CharField(max_length=16)
    part_id = serializers.IntegerField(min_value=1)
    required_quantity = serializers.DecimalField(max_digits=15, decimal_places=5)
    fulfillment_mode = serializers.CharField(max_length=24)
    substitution_policy = serializers.CharField(
        max_length=20, required=False, default='none'
    )
    requires_scan = serializers.BooleanField(required=False, default=False)
    note = serializers.CharField(required=False, allow_blank=True, default='')
    expected_version = serializers.IntegerField(
        min_value=0, required=False, default=None
    )


class UpdateManualLineSerializer(serializers.Serializer):
    """Intent to amend an editable manual line."""

    required_quantity = serializers.DecimalField(
        max_digits=15, decimal_places=5, required=False
    )
    required = serializers.BooleanField(required=False)
    fulfillment_mode = serializers.CharField(max_length=24, required=False)
    substitution_policy = serializers.CharField(max_length=20, required=False)
    requires_scan = serializers.BooleanField(required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    kind = serializers.CharField(max_length=16, required=False)
    expected_version = serializers.IntegerField(
        min_value=0, required=False, default=None
    )


class RemoveManualLineSerializer(serializers.Serializer):
    """Intent to remove an editable manual line."""

    expected_version = serializers.IntegerField(
        min_value=0, required=False, default=None
    )
