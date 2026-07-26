"""Serializers for the tasks application."""

from django.conf import settings

from rest_framework import serializers

from .models import KanbanCard, KanbanCardDependency, KanbanCardPart, KanbanColumn


class KanbanCardDependencySerializer(serializers.ModelSerializer):
    """Serializer for scheduling dependencies between work orders."""

    class Meta:
        """Serializer metadata."""

        model = KanbanCardDependency
        fields = (
            'id',
            'from_card',
            'to_card',
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


class KanbanCardPartSerializer(serializers.ModelSerializer):
    """Serializer for KanbanCardPart instances."""

    part_name = serializers.CharField(source='part.name', read_only=True)
    part_ipn = serializers.CharField(source='part.IPN', read_only=True, default='')
    part_thumbnail = serializers.SerializerMethodField()

    class Meta:
        """Serializer configuration for KanbanCardPart."""

        model = KanbanCardPart
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


class KanbanCardSerializer(serializers.ModelSerializer):
    """Serializer for KanbanCard instances."""

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
        queryset=KanbanCard._meta.get_field('machine').related_model.objects.all(),
        required=True,
        allow_null=False,
        help_text='Machine this work order is performed on',
    )

    parts = KanbanCardPartSerializer(source='card_parts', many=True, read_only=True)

    # Denormalized machine/assignee labels so the calendar and timeline can group
    # and label rows without a request per card. ``KanbanCardList`` select_relates
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
        """Serializer configuration for KanbanCard."""

        model = KanbanCard
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
            # Composition (§5.10). ``parent`` and ``card_kind`` are set at
            # creation through the command service and read-only here.
            'parent',
            'card_kind',
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
            'parent',
            'card_kind',
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


class KanbanCardSummarySerializer(serializers.ModelSerializer):
    """Compact card identity for hierarchy and dependency rows."""

    machine_name = serializers.CharField(
        source='machine.name', read_only=True, default=None
    )
    assigned_to_name = serializers.SerializerMethodField()

    class Meta:
        """Serializer configuration."""

        model = KanbanCard
        fields = (
            'id',
            'reference',
            'title',
            'description',
            'status',
            'priority',
            'lifecycle_status',
            'work_order_type',
            'card_kind',
            'parent',
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


class KanbanCardOverviewSerializer(KanbanCardSerializer):
    """Complete read-only work-order context for the detail page."""

    parent_detail = serializers.SerializerMethodField()
    children = serializers.SerializerMethodField()
    dependencies = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    repair_packet = serializers.SerializerMethodField()
    maintenance_record = serializers.SerializerMethodField()
    structured_closeout = serializers.SerializerMethodField()
    canonical_commands_enabled = serializers.SerializerMethodField()

    class Meta(KanbanCardSerializer.Meta):
        """Extend the card fields with work-order relationships."""

        fields = (
            *KanbanCardSerializer.Meta.fields,
            'parent_detail',
            'children',
            'dependencies',
            'events',
            'repair_packet',
            'maintenance_record',
            'structured_closeout',
            'canonical_commands_enabled',
        )
        read_only_fields = fields

    @staticmethod
    def _summary(card):
        """Serialize one related card without recursing."""
        if card is None:
            return None
        return KanbanCardSummarySerializer(card).data

    def get_parent_detail(self, obj):
        """Return the parent work order for a child card."""
        return self._summary(obj.parent)

    @staticmethod
    def get_children(obj):
        """Return direct jobs/tasks under this work order."""
        return KanbanCardSummarySerializer(
            obj.children.all().order_by('scheduled_start', 'created_at'), many=True
        ).data

    def get_dependencies(self, obj):
        """Return predecessor and successor relationships."""
        incoming = [
            {
                'id': dependency.pk,
                'direction': 'predecessor',
                'dependency_type': dependency.dependency_type,
                'lag_minutes': dependency.lag_minutes,
                'card': self._summary(dependency.from_card),
            }
            for dependency in obj.dependencies_in.all()
        ]
        outgoing = [
            {
                'id': dependency.pk,
                'direction': 'successor',
                'dependency_type': dependency.dependency_type,
                'lag_minutes': dependency.lag_minutes,
                'card': self._summary(dependency.to_card),
            }
            for dependency in obj.dependencies_out.all()
        ]
        return [*incoming, *outgoing]

    @staticmethod
    def get_events(obj):
        """Return append-only lifecycle audit events."""
        from .workorder_serializers import WorkOrderEventSerializer

        return WorkOrderEventSerializer(
            obj.events.all().order_by('-created_at'), many=True
        ).data

    @staticmethod
    def get_repair_packet(obj):
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
    def get_maintenance_record(obj):
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
    def get_structured_closeout(obj):
        """Return the immutable structured closeout when completed."""
        closeout = getattr(obj, 'structured_closeout', None)
        if closeout is None:
            return None
        return {
            'id': closeout.pk,
            'cause': closeout.cause,
            'action': closeout.action,
            'result': closeout.result,
            'verification_summary': closeout.verification_summary,
            'downtime_minutes': closeout.downtime_minutes,
            'follow_up_required': closeout.follow_up_required,
            'follow_up': closeout.follow_up,
            'completed_at': closeout.completed_at,
            'verified_at': closeout.verified_at,
        }

    @staticmethod
    def get_canonical_commands_enabled(obj) -> bool:
        """Report whether the feature-gated command surface is enabled."""
        return bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False))
