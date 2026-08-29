"""Serializers for the tasks application."""

from django.conf import settings

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    KanbanCard,
    KanbanColumn,
    WorkOrder,
    WorkOrderDependency,
    WorkOrderPart,
)


class WorkOrderDependencySerializer(serializers.ModelSerializer):
    """Serializer for scheduling dependencies between work orders."""

    class Meta:
        """Serializer metadata."""

        model = WorkOrderDependency
        fields = (
            'id',
            'predecessor',
            'successor',
            'dependency_type',
            'lag_minutes',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class KanbanColumnSerializer(serializers.ModelSerializer):
    """Serializer for persisted board columns."""

    card_count = serializers.SerializerMethodField()

    class Meta:
        """Serializer metadata."""

        model = KanbanColumn
        fields = (
            'id',
            'key',
            'label',
            'color',
            'order',
            'is_default',
            'is_terminal',
            'card_count',
            'created_at',
            'updated_at',
        )
        # ``key`` is immutable after creation: it is stored in card.status, so
        # renaming it would orphan every card in the column. Relabel instead.
        # ``is_terminal`` is read-only here: the terminal column is seeded and
        # reassigned through admin, not ad-hoc board edits, so "exactly one
        # terminal" cannot be broken from the board.
        read_only_fields = (
            'id',
            'is_default',
            'is_terminal',
            'card_count',
            'created_at',
            'updated_at',
        )

    def get_card_count(self, obj) -> int:
        """Return the number of active cards currently in this column."""
        return obj.card_count()

    def update(self, instance, validated_data):
        """Forbid changing the key of an existing column."""
        # ``key`` is not in read_only_fields because it must be settable on
        # create; guard it explicitly on update rather than silently ignoring.
        new_key = validated_data.get('key')

        if new_key is not None and new_key != instance.key:
            raise serializers.ValidationError({
                'key': 'A column key cannot be changed once created, because it '
                'is stored on every card in the column. Edit the label instead.'
            })

        return super().update(instance, validated_data)


class WorkOrderPartSerializer(serializers.ModelSerializer):
    """Serializer for WorkOrderPart instances."""

    part_name = serializers.CharField(source='part.name', read_only=True)
    part_ipn = serializers.CharField(source='part.IPN', read_only=True, default='')
    part_thumbnail = serializers.SerializerMethodField()

    class Meta:
        """Serializer configuration for WorkOrderPart."""

        model = WorkOrderPart
        fields = (
            'id',
            'part',
            'part_name',
            'part_ipn',
            'part_thumbnail',
            'quantity',
            'allocated_quantity',
            'allocation_status',
            'allocation_note',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'allocated_quantity',
            'allocation_status',
            'allocation_note',
            'created_at',
            'updated_at',
        )

    def get_part_thumbnail(self, obj) -> str | None:
        """Return the thumbnail URL for the part."""
        if obj.part and obj.part.image:
            return obj.part.image.url
        return None


class WorkOrderBoardSerializer(serializers.ModelSerializer):
    """Serializer for WorkOrder instances."""

    tags = serializers.ListField(
        child=serializers.CharField(max_length=32), allow_empty=True, required=False
    )
    due_date = serializers.DateField(allow_null=True, required=False)

    # Every work order is anchored to a machine (decision S3c / plan 5.6): the
    # create form, and every API/AI write path through this serializer, must
    # supply one. This is enforced at the API layer; the model column is still
    # nullable, so the DB-level non-null constraint and its backfill are a
    # separate follow-up (see the S3c note in the execution plan). ``required``
    # is not enforced on a partial update, so an existing card can still be
    # PATCHed without re-sending the machine.
    machine = serializers.PrimaryKeyRelatedField(
        queryset=WorkOrder._meta.get_field('machine').related_model.objects.all(),
        required=True,
        allow_null=False,
        help_text='Machine this work order is performed on',
    )

    parts = WorkOrderPartSerializer(
        source='work_order_parts', many=True, read_only=True
    )

    # Denormalized machine/assignee labels so the calendar and timeline can group
    # and label rows without a request per card. ``WorkOrderBoardList`` select_relates
    # both relations; without that these would be N+1 on an unpaginated list.
    machine_name = serializers.CharField(
        source='machine.name', read_only=True, default=None
    )
    machine_location = serializers.CharField(
        source='machine.location', read_only=True, default=None
    )
    assigned_to_username = serializers.CharField(
        source='assigned_to.username', read_only=True, default=None
    )
    assigned_to_name = serializers.SerializerMethodField()

    class Meta:
        """Serializer configuration for WorkOrder."""

        model = WorkOrder
        fields = (
            'id',
            'title',
            'description',
            'status',
            'priority',
            'due_date',
            'assignee',
            'tags',
            'company',
            'company_contact_name',
            'company_contact_phone',
            'job_number',
            'service_quote',
            'is_active',
            'created_at',
            'updated_at',
            'parts',
            # Planning metadata. These already existed on the model but were not
            # exposed here, so the board could not read or write a schedule at all.
            'machine',
            'machine_name',
            'machine_location',
            'assigned_to',
            'assigned_to_username',
            'assigned_to_name',
            'scheduled_start',
            'scheduled_end',
            'estimated_minutes',
            'work_order_type',
            # Typed work-order fields. An earlier revision withheld these so the
            # flagged canonical API could not leak through this unflagged surface.
            # That boundary has been retired deliberately: there are no external
            # clients of this API, and the board, calendar and timeline all read
            # this one surface, so a second shape to reconcile buys nothing.
            #
            # Exposed for *reading* only -- see read_only_fields. lifecycle_version
            # in particular is published here because Phase 3 needs it as the
            # expected_version optimistic-concurrency token.
            'reference',
            'lifecycle_status',
            'lifecycle_version',
            'actual_started_at',
            'actual_completed_at',
        )
        # The read/write split is the part of the old boundary that still matters,
        # and it matches ``WorkOrderSerializer``: lifecycle state, assignment,
        # identity and execution timestamps change only through the canonical
        # commands, never through an ordinary board edit. Planning metadata
        # (schedule, machine, duration, type) stays writable until the scheduling
        # command service takes over those writes in Phase 3.
        read_only_fields = (
            'id',
            'is_active',
            'created_at',
            'updated_at',
            'reference',
            'assigned_to',
            'lifecycle_status',
            'lifecycle_version',
            'actual_started_at',
            'actual_completed_at',
        )

    def get_assigned_to_name(self, obj) -> str | None:
        """Return the assignee's display name, falling back to the username."""
        user = obj.assigned_to

        if user is None:
            return None

        full_name = f'{user.first_name} {user.last_name}'.strip()

        return full_name or user.username

    def validate(self, attrs):
        """Reject a scheduled window that ends before it starts.

        Reads through to the instance so a PATCH supplying only one endpoint is
        still validated against the stored value of the other.
        """
        attrs = super().validate(attrs)

        def resolve(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        start = resolve('scheduled_start')
        end = resolve('scheduled_end')

        if start and end and end < start:
            raise serializers.ValidationError({
                'scheduled_end': 'Scheduled end must not be before scheduled start.'
            })

        # A card reaches the terminal (done) column only through closeout (§5.8):
        # refuse a manual drag/edit that sets status to the terminal key unless
        # the work order is actually completed. Otherwise "done" would mean two
        # different things and the maintenance history could miss a job.
        new_status = attrs.get('status')
        if new_status is not None:
            terminal_key = KanbanColumn.terminal_key()
            current_lifecycle = getattr(self.instance, 'lifecycle_status', None)
            if (
                terminal_key
                and new_status == terminal_key
                and current_lifecycle != 'completed'
            ):
                raise serializers.ValidationError({
                    'status': (
                        'A work order can only enter the done column by being '
                        'closed out, not by a manual move.'
                    )
                })

        return attrs

    def validate_tags(self, value):
        """Ensure tags are stored as unique values."""
        # Remove duplicates while preserving order
        seen = set()
        filtered = []

        for tag in value:
            if tag not in seen:
                seen.add(tag)
                filtered.append(tag)

        return filtered


class KanbanCardSerializer(serializers.ModelSerializer):
    """One tracked piece of a work order, as the board needs it.

    A card carries enough of its work order to be rendered without a second
    request - reference, priority, machine, lifecycle - because the board draws
    the job's identity on every card belonging to it. Those fields are read-only
    here: they describe the job, and the job is changed through its own
    endpoints, never by editing one of its cards.

    ``effective_*`` report what the card resolves to after falling back to the
    work order, so a client does not have to re-implement that rule to show who
    is doing a piece of work and when.
    """

    work_order_reference = serializers.CharField(
        source='work_order.reference', read_only=True, default=None
    )
    work_order_title = serializers.CharField(
        source='work_order.title', read_only=True, default=None
    )
    priority = serializers.CharField(
        source='work_order.priority', read_only=True, default=None
    )
    lifecycle_status = serializers.CharField(
        source='work_order.lifecycle_status', read_only=True, default=None
    )
    lifecycle_version = serializers.IntegerField(
        source='work_order.lifecycle_version', read_only=True, default=None
    )
    machine = serializers.IntegerField(
        source='work_order.machine_id', read_only=True, default=None
    )
    machine_name = serializers.CharField(
        source='work_order.machine.name', read_only=True, default=None
    )
    tags = serializers.JSONField(source='work_order.tags', read_only=True)

    effective_assignee = serializers.SerializerMethodField()
    effective_start = serializers.DateTimeField(read_only=True)
    effective_end = serializers.DateTimeField(read_only=True)

    class Meta:
        """Serializer configuration for KanbanCard."""

        model = KanbanCard
        fields = (
            'id',
            'work_order',
            'card_kind',
            'status',
            'board_order',
            'title',
            'description',
            'assigned_to',
            'assignee',
            'scheduled_start',
            'scheduled_end',
            'estimated_minutes',
            'is_active',
            'created_at',
            'updated_at',
            # Read-only work-order context, so one request renders the board.
            'work_order_reference',
            'work_order_title',
            'priority',
            'lifecycle_status',
            'lifecycle_version',
            'machine',
            'machine_name',
            'tags',
            'effective_assignee',
            'effective_start',
            'effective_end',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def update(self, instance, validated_data):
        """Apply a board edit, refusing to move the card to another job.

        Which work order a piece of work belongs to is settled when the card is
        created. Allowing a PATCH to change it would let a board drag move work
        between jobs, quietly detaching it from the closeout that accounts for
        it - so this is rejected rather than ignored, which would look like it
        had worked.
        """
        work_order = validated_data.pop('work_order', None)
        if work_order is not None and work_order.pk != instance.work_order_id:
            raise serializers.ValidationError({
                'work_order': ['A card cannot be moved to a different work order.']
            })
        return super().update(instance, validated_data)

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_effective_assignee(self, obj) -> int | None:
        """Return who is doing this piece, after the fallback to the job."""
        user = obj.effective_assignee
        return user.pk if user else None


class WorkOrderSummarySerializer(serializers.ModelSerializer):
    """Compact card identity for hierarchy and dependency rows."""

    machine_name = serializers.CharField(
        source='machine.name', read_only=True, default=None
    )
    assigned_to_name = serializers.SerializerMethodField()

    class Meta:
        """Serializer configuration."""

        model = WorkOrder
        fields = (
            'id',
            'reference',
            'title',
            'description',
            'status',
            'priority',
            'lifecycle_status',
            'work_order_type',
            'machine',
            'machine_name',
            'assigned_to',
            'assigned_to_name',
            'scheduled_start',
            'scheduled_end',
            'estimated_minutes',
            'is_active',
        )
        read_only_fields = fields

    def get_assigned_to_name(self, obj) -> str | None:
        """Return the assignee display name."""
        user = obj.assigned_to
        if user is None:
            return None
        return user.get_full_name().strip() or user.get_username()


def _is_preliminary(diagnosis) -> bool:
    """Whether a diagnosis blob must still be presented as preliminary."""
    from repair.schema import is_preliminary

    return is_preliminary(diagnosis)


def _approved_scope_projection(packet) -> dict | None:
    """Return the approved scope currently in force for a packet."""
    scope = packet.approved_scopes.filter(superseded_at__isnull=True).first()
    if scope is None:
        return None
    return {
        'id': scope.pk,
        'version': scope.version,
        'verified_cause': scope.verified_cause,
        'scope_lines': scope.scope_lines,
        'failure_codes': scope.failure_codes,
        'crew_size': scope.crew_size,
        'planned_elapsed_minutes': scope.planned_elapsed_minutes,
        'approved_at': scope.approved_at,
        'approval_note': scope.approval_note,
    }


class WorkOrderOverviewSerializer(WorkOrderBoardSerializer):
    """Complete read-only work-order context for the detail page."""

    cards = serializers.SerializerMethodField()
    dependencies = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    repair_packet = serializers.SerializerMethodField()
    maintenance_record = serializers.SerializerMethodField()
    structured_closeout = serializers.SerializerMethodField()
    canonical_commands_enabled = serializers.SerializerMethodField()
    source_alert = serializers.SerializerMethodField()

    class Meta(WorkOrderBoardSerializer.Meta):
        """Extend the card fields with work-order relationships."""

        fields = (
            *WorkOrderBoardSerializer.Meta.fields,
            'cards',
            'dependencies',
            'events',
            'repair_packet',
            'maintenance_record',
            'structured_closeout',
            'canonical_commands_enabled',
            'source_alert',
        )
        read_only_fields = fields

    @extend_schema_field(KanbanCardSerializer(many=True))
    def get_cards(self, obj) -> list:
        """Return every piece of tracked work belonging to this job.

        The detail page shows the job and the cards it is being worked
        through; without this it would show a job with no visible work.
        """
        return KanbanCardSerializer(
            obj.cards.select_related('assigned_to').order_by(
                'board_order', 'created_at'
            ),
            many=True,
        ).data

    @staticmethod
    def _summary(work_order):
        """Serialize one related card without recursing."""
        if work_order is None:
            return None
        return WorkOrderSummarySerializer(work_order).data

    def get_dependencies(self, obj) -> list:
        """Return predecessor and successor relationships."""
        incoming = [
            {
                'id': dependency.pk,
                'direction': 'predecessor',
                'dependency_type': dependency.dependency_type,
                'lag_minutes': dependency.lag_minutes,
                'card': self._summary(dependency.predecessor),
            }
            for dependency in obj.dependencies_in.all()
        ]
        outgoing = [
            {
                'id': dependency.pk,
                'direction': 'successor',
                'dependency_type': dependency.dependency_type,
                'lag_minutes': dependency.lag_minutes,
                'card': self._summary(dependency.successor),
            }
            for dependency in obj.dependencies_out.all()
        ]
        return [*incoming, *outgoing]

    @staticmethod
    def get_events(obj) -> list:
        """Return append-only lifecycle audit events."""
        from .workorder_serializers import WorkOrderEventSerializer

        return WorkOrderEventSerializer(
            obj.events.all().order_by('-created_at'), many=True
        ).data

    @staticmethod
    def get_repair_packet(obj) -> dict | None:
        """Return repair diagnosis and safety context when present."""
        packet = getattr(obj, 'repair_packet', None)
        if packet is None:
            return None
        return {
            'id': packet.pk,
            'reference': packet.reference,
            'status': packet.status,
            'criticality': packet.criticality,
            'fault_summary': packet.fault_summary,
            'symptom': packet.symptom,
            'production_impact': packet.production_impact,
            'generation_status': packet.generation_status,
            'diagnosis': packet.diagnosis,
            # Until a human verifies it, the diagnosis blob is preliminary and
            # the page must label it that way rather than as a finding.
            'diagnosis_is_preliminary': _is_preliminary(packet.diagnosis),
            'diagnosis_status': (packet.diagnosis or {}).get('status'),
            'findings': [
                {
                    'id': finding.pk,
                    'finding_key': finding.finding_key,
                    'category': finding.category,
                    'observation': finding.observation,
                    'value': finding.value,
                    'unit': finding.unit,
                    'evidence_source': finding.evidence_source,
                    'snapshot_id': (
                        str(finding.snapshot_id) if finding.snapshot_id else None
                    ),
                    'observed_at': finding.observed_at,
                    'verification': finding.verification,
                }
                for finding in packet.findings.all()
            ],
            'approved_scope': _approved_scope_projection(packet),
            'gates': [
                {
                    'id': gate.pk,
                    'name': gate.name,
                    'gate_type': gate.gate_type,
                    'status': gate.status,
                    'is_blocking': gate.is_blocking,
                    'is_mandatory': gate.is_mandatory,
                    'requires_photo': gate.requires_photo,
                    'requires_second_person': gate.requires_second_person,
                }
                for gate in packet.gates.all().order_by('sequence', 'created_at')
            ],
        }

    @staticmethod
    def get_source_alert(obj) -> dict | None:
        """Return the health anomaly this work order answers, if any.

        The alert is what a technician opens the page to understand, so it is
        projected here rather than left a click away. External alert and alarm
        identifiers are retained as human references; links still use internal
        keys.
        """
        anomaly = obj.anomalies.select_related('source').order_by('pk').first()
        if anomaly is None:
            return None
        return {
            'id': anomaly.pk,
            'title': anomaly.title,
            'severity': anomaly.severity,
            'status': anomaly.status,
            'alarm_code': anomaly.alarm_code,
            'external_id': anomaly.external_id,
            'detector': anomaly.detector,
            'detector_version': anomaly.detector_version,
            'evidence_summary': anomaly.evidence_summary,
            'first_observed_at': anomaly.first_observed_at,
            'last_observed_at': anomaly.last_observed_at,
            'source_name': anomaly.source.name if anomaly.source else None,
            'source_type': anomaly.source.source_type if anomaly.source else None,
            'machine_id': anomaly.machine_id,
        }

    @staticmethod
    def get_maintenance_record(obj) -> dict | None:
        """Return maintenance history produced by this work order."""
        record = getattr(obj, 'maintenance_record', None)
        if record is None:
            return None
        return {
            'id': record.pk,
            'date': record.date,
            'summary': record.summary,
            'details': record.details,
            'performed_by': record.performed_by,
        }

    @staticmethod
    def get_structured_closeout(obj) -> dict | None:
        """Return the effective structured closeout when completed.

        Applied amendments supersede the immutable base row, matching the AI
        projection (``tasks.ai_read.work_order_closeout``); ``amended`` and
        ``amendment_count`` make a governed correction visible instead of
        silently changing values.
        """
        from .services.closeout_amend import effective_closeout_overview

        closeout = getattr(obj, 'structured_closeout', None)
        if closeout is None:
            return None
        fields = effective_closeout_overview(closeout)
        return {
            'id': closeout.pk,
            'cause': fields['cause'],
            'action': fields['action'],
            'result': fields['result'],
            'verification_summary': fields['verification_summary'],
            'downtime_minutes': fields['downtime_minutes'],
            'follow_up_required': fields['follow_up_required'],
            'follow_up': fields['follow_up'],
            'completed_at': closeout.completed_at,
            'verified_at': closeout.verified_at,
            'amended': fields['amended'],
            'amendment_count': fields['amendment_count'],
        }

    @staticmethod
    def get_canonical_commands_enabled(obj) -> bool:
        """Report whether the feature-gated command surface is enabled."""
        return bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False))
