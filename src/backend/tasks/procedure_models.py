"""Governed maintenance procedures and work-order procedure execution models."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

import InvenTree.models
from tasks.models import WorkOrderType


class ProcedureRevisionStatus(models.TextChoices):
    """Lifecycle states for a governed procedure revision."""

    DRAFT = 'draft', _('Draft')
    IN_REVIEW = 'in_review', _('In Review')
    PUBLISHED = 'published', _('Published')
    SUPERSEDED = 'superseded', _('Superseded')
    ARCHIVED = 'archived', _('Archived')


class ProcedureStepType(models.TextChoices):
    """Supported procedure step types."""

    INSTRUCTION = 'instruction', _('Instruction')
    MEASUREMENT = 'measurement', _('Measurement')
    INSPECTION = 'inspection', _('Inspection')
    SAFETY = 'safety', _('Safety Reference')
    HOLD_POINT = 'hold_point', _('Hold Point')
    VERIFICATION = 'verification', _('Verification')


class ProcedureResourceKind(models.TextChoices):
    """Kinds of resources required by a procedure."""

    PART = 'part', _('Part')
    CONSUMABLE = 'consumable', _('Consumable')
    TOOL = 'tool', _('Tool')
    SAFETY = 'safety', _('Safety Equipment')


class FulfillmentMode(models.TextChoices):
    """Ways a procedure resource can be fulfilled."""

    RESERVE_CONSUME = 'reserve_consume', _('Reserve and Consume')
    CHECKOUT_RETURN = 'checkout_return', _('Checkout and Return')
    VERIFY_ONLY = 'verify_only', _('Verify Only')


class StepExecutionStatus(models.TextChoices):
    """Execution states for a work-order procedure step."""

    PENDING = 'pending', _('Pending')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    FAILED = 'failed', _('Failed')
    NOT_APPLICABLE = 'not_applicable', _('Not Applicable')
    BLOCKED = 'blocked', _('Blocked')


class Procedure(models.Model):
    """Stable identity for a family of governed procedure revisions."""

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    customer = models.ForeignKey(
        'company.Company', null=True, blank=True, on_delete=models.PROTECT
    )
    active = models.BooleanField(default=True, db_index=True)
    current_revision = models.ForeignKey(
        'tasks.ProcedureRevision',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['customer', 'code'], name='tasks_procedure_scope_code_uniq'
            )
        ]


class ProcedureRevision(models.Model):
    """Immutable governed content revision for a procedure."""

    procedure = models.ForeignKey(
        Procedure, on_delete=models.CASCADE, related_name='revisions'
    )
    revision = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=ProcedureRevisionStatus.choices,
        default=ProcedureRevisionStatus.DRAFT,
        db_index=True,
    )
    work_order_type = models.CharField(max_length=20, choices=WorkOrderType.choices)
    change_summary = models.TextField(blank=True)
    default_estimated_minutes = models.PositiveIntegerField(null=True, blank=True)
    review_due_at = models.DateTimeField(null=True, blank=True)
    schema_version = models.PositiveSmallIntegerField(default=1)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    content_version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['procedure', 'revision'], name='tasks_procedure_revision_uniq'
            ),
            models.CheckConstraint(
                condition=Q(revision__gt=0), name='tasks_procedure_revision_positive'
            ),
            models.CheckConstraint(
                condition=Q(content_version__gt=0),
                name='tasks_procedure_content_version_positive',
            ),
            models.UniqueConstraint(
                fields=['procedure'],
                condition=Q(status=ProcedureRevisionStatus.PUBLISHED),
                name='tasks_procedure_one_published',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status=ProcedureRevisionStatus.PUBLISHED)
                    | (Q(published_by__isnull=False) & Q(published_at__isnull=False))
                ),
                name='tasks_procedure_published_metadata',
            ),
        ]


class ProcedureApplicability(models.Model):
    """Explicit matching rule for applying a procedure revision."""

    revision = models.ForeignKey(
        ProcedureRevision, on_delete=models.CASCADE, related_name='applicability_rules'
    )
    machine = models.ForeignKey(
        'assets.AssetMachine', null=True, blank=True, on_delete=models.PROTECT
    )
    manufacturer = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=255, blank=True)
    location_pattern = models.CharField(max_length=255, blank=True)
    required_tags = models.JSONField(default=list, blank=True)
    predicate = models.JSONField(default=dict, blank=True)


class ProcedureStep(models.Model):
    """Ordered instruction or control in a procedure revision."""

    revision = models.ForeignKey(
        ProcedureRevision, on_delete=models.CASCADE, related_name='steps'
    )
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    sequence = models.PositiveIntegerField()
    step_type = models.CharField(max_length=20, choices=ProcedureStepType.choices)
    title = models.CharField(max_length=255)
    instruction = models.TextField()
    required = models.BooleanField(default=True)
    estimated_minutes = models.PositiveIntegerField(null=True, blank=True)
    required_permission = models.CharField(max_length=100, blank=True)
    value_type = models.CharField(
        max_length=20,
        choices=[
            ('none', _('None')),
            ('number', _('Number')),
            ('boolean', _('Pass / Fail')),
            ('choice', _('Choice')),
            ('text', _('Text')),
        ],
        default='none',
    )
    unit = models.CharField(max_length=64, blank=True)
    min_value = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    max_value = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    allowed_values = models.JSONField(default=list, blank=True)
    evidence_policy = models.JSONField(default=dict, blank=True)
    safety_gate_template = models.ForeignKey(
        'repair.SafetyGateTemplate',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='procedure_steps',
    )

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['revision', 'sequence'],
                name='tasks_step_revision_sequence_uniq',
            ),
            models.UniqueConstraint(
                fields=['revision', 'key'], name='tasks_step_revision_key_uniq'
            ),
            models.CheckConstraint(
                condition=Q(sequence__gt=0), name='tasks_step_sequence_positive'
            ),
        ]


class ProcedureResourceRequirement(models.Model):
    """Ordered part, consumable, tool, or safety resource requirement."""

    revision = models.ForeignKey(
        ProcedureRevision,
        on_delete=models.CASCADE,
        related_name='resource_requirements',
    )
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=ProcedureResourceKind.choices)
    part = models.ForeignKey('part.Part', on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    fulfillment_mode = models.CharField(max_length=24, choices=FulfillmentMode.choices)
    required = models.BooleanField(default=True)
    substitution_policy = models.CharField(
        max_length=20,
        choices=[
            ('none', _('No Substitution')),
            ('approved_only', _('Approved Alternates Only')),
            ('supervisor', _('Supervisor Approval')),
        ],
        default='none',
    )
    requires_scan = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['revision', 'key'], name='tasks_resource_revision_key_uniq'
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name='tasks_resource_quantity_positive'
            ),
            models.CheckConstraint(
                condition=Q(sequence__gt=0), name='tasks_resource_sequence_positive'
            ),
        ]


class ProcedureRevisionSource(models.Model):
    """Immutable source snapshot used to author a procedure revision."""

    revision = models.ForeignKey(
        ProcedureRevision, on_delete=models.CASCADE, related_name='sources'
    )
    packet = models.ForeignKey(
        'repair.RepairPacket', null=True, blank=True, on_delete=models.PROTECT
    )
    maintenance_record = models.ForeignKey(
        'assets.AssetMaintenanceRecord', null=True, blank=True, on_delete=models.PROTECT
    )
    source_snapshot = models.JSONField()
    source_hash = models.CharField(max_length=64)
    captured_at = models.DateTimeField()
    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class ProcedureFieldDecision(models.Model):
    """Human disposition of an authored or assisted procedure field."""

    revision = models.ForeignKey(
        ProcedureRevision, on_delete=models.CASCADE, related_name='field_decisions'
    )
    field_path = models.CharField(max_length=255)
    origin = models.CharField(max_length=20)
    proposal = models.JSONField(default=dict)
    decision = models.CharField(max_length=16)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )
    decided_at = models.DateTimeField(null=True, blank=True)


class WorkOrderProcedureApplication(models.Model):
    """Immutable procedure snapshot applied to a work order."""

    work_order = models.ForeignKey(
        'tasks.KanbanCard',
        on_delete=models.CASCADE,
        related_name='procedure_applications',
    )
    revision = models.ForeignKey(ProcedureRevision, on_delete=models.PROTECT)
    sequence = models.PositiveIntegerField(default=1)
    primary = models.BooleanField(default=True)
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    policy_version = models.PositiveSmallIntegerField(default=1)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    applied_at = models.DateTimeField(auto_now_add=True)
    idempotency_key = models.CharField(max_length=128)
    drift_status = models.CharField(max_length=20, default='current')

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['work_order', 'idempotency_key'],
                name='tasks_application_work_order_idem_uniq',
            ),
            models.UniqueConstraint(
                fields=['work_order'],
                condition=Q(primary=True),
                name='tasks_application_one_primary',
            ),
        ]


class WorkOrderStepExecution(InvenTree.models.InvenTreeAttachmentMixin, models.Model):
    """Recorded execution state for one snapshotted procedure step."""

    application = models.ForeignKey(
        WorkOrderProcedureApplication,
        on_delete=models.CASCADE,
        related_name='step_executions',
    )
    step_key = models.UUIDField()
    sequence = models.PositiveIntegerField()
    step_snapshot = models.JSONField()
    status = models.CharField(
        max_length=20,
        choices=StepExecutionStatus.choices,
        default=StepExecutionStatus.PENDING,
    )
    value = models.JSONField(default=dict, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    note = models.TextField(blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    disposition_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['application', 'step_key'],
                name='tasks_execution_application_step_uniq',
            ),
            models.CheckConstraint(
                condition=Q(version__gt=0), name='tasks_execution_version_positive'
            ),
        ]
