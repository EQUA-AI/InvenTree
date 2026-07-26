"""Database models for the Repair Packet (spine) application.

The Repair Packet is the aggregate root of the fault-to-fix loop. It references
(rather than duplicates) the outputs of the other subsystems: the asset from the
``assets`` app, the work order from the ``tasks`` app, parts/stock from InvenTree
core, and approvals from the ``approvals`` app. See LocalDocs/SpineImplementation.md.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import InvenTree.models

from .schema import DIAGNOSIS_SCHEMA_VERSION


class PacketStatus(models.TextChoices):
    """Repair packet lifecycle finite state machine states."""

    DRAFT = 'draft', _('Draft')
    DIAGNOSED = 'diagnosed', _('Diagnosed')
    APPROVED = 'approved', _('Approved')
    EXECUTING = 'executing', _('Executing')
    CLOSED = 'closed', _('Closed')
    CANCELED = 'canceled', _('Canceled')


TERMINAL_PACKET_STATUSES = frozenset({PacketStatus.CLOSED, PacketStatus.CANCELED})

# Maps (from_status) -> set of allowed (to_status) values.
VALID_TRANSITIONS: dict[str, set[str]] = {
    PacketStatus.DRAFT: {PacketStatus.DIAGNOSED, PacketStatus.CANCELED},
    PacketStatus.DIAGNOSED: {
        PacketStatus.APPROVED,
        PacketStatus.DRAFT,
        PacketStatus.CANCELED,
    },
    PacketStatus.APPROVED: {PacketStatus.EXECUTING, PacketStatus.CANCELED},
    PacketStatus.EXECUTING: {PacketStatus.CLOSED, PacketStatus.CANCELED},
}


def is_valid_packet_transition(from_status: str, to_status: str) -> bool:
    """Check whether a packet status transition is allowed by the FSM."""
    return to_status in VALID_TRANSITIONS.get(from_status, set())


class GateStatus(models.TextChoices):
    """Safety gate confirmation states."""

    PENDING = 'pending', _('Pending')
    CONFIRMED = 'confirmed', _('Confirmed')
    WAIVED = 'waived', _('Waived')


class GenerationStatus(models.TextChoices):
    """Status of the AI generation step for a packet."""

    IDLE = 'idle', _('Idle')
    PENDING = 'pending', _('Pending')
    RUNNING = 'running', _('Running')
    SUCCEEDED = 'succeeded', _('Succeeded')
    FAILED = 'failed', _('Failed')


CRITICALITY_CHOICES = [
    ('low', _('Low')),
    ('medium', _('Medium')),
    ('high', _('High')),
    ('critical', _('Critical')),
]


class RepairPacket(InvenTree.models.InvenTreeAttachmentMixin, models.Model):
    """Approval-ready, executable work package for a single fault-to-fix loop."""

    status = models.CharField(
        max_length=20,
        choices=PacketStatus.choices,
        default=PacketStatus.DRAFT,
        db_index=True,
        verbose_name=_('Status'),
    )

    reference = models.CharField(
        max_length=32,
        blank=True,
        unique=True,
        db_index=True,
        verbose_name=_('Reference'),
        help_text=_('Auto-generated packet reference (e.g. RP-000123)'),
    )

    # --- Fault summary ---
    machine = models.ForeignKey(
        'assets.AssetMachine',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='repair_packets',
        verbose_name=_('Asset'),
    )

    fault_summary = models.TextField(blank=True, verbose_name=_('Fault Summary'))
    symptom = models.CharField(max_length=255, blank=True, verbose_name=_('Symptom'))

    criticality = models.CharField(
        max_length=12,
        choices=CRITICALITY_CHOICES,
        default='medium',
        db_index=True,
        verbose_name=_('Criticality'),
    )

    production_impact = models.TextField(
        blank=True, verbose_name=_('Production Impact')
    )

    # --- Diagnosis (produced by the AI generation layer; structured + provenance) ---
    diagnosis = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Diagnosis'),
        help_text=_('Structured diagnosis result from the AI generation layer'),
    )
    diagnosis_schema_version = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_('Diagnosis Schema Version'),
        help_text=_('0 = not yet generated'),
    )
    generation_status = models.CharField(
        max_length=12,
        choices=GenerationStatus.choices,
        default=GenerationStatus.IDLE,
        db_index=True,
        verbose_name=_('Generation Status'),
    )

    # --- Wiring to other subsystems ---
    work_order = models.OneToOneField(
        'tasks.KanbanCard',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='repair_packet',
        verbose_name=_('Work Order'),
    )

    # --- Closeout ---
    closeout = models.JSONField(default=dict, blank=True, verbose_name=_('Closeout'))
    maintenance_record = models.ForeignKey(
        'assets.AssetMaintenanceRecord',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='repair_packets',
        verbose_name=_('Maintenance Record'),
    )

    # --- Provenance / idempotency (ties to ai workflow + approvals) ---
    agent_run_id = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name=_('Agent Run ID')
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='repair_packets',
        verbose_name=_('Created By'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['-created_at']
        verbose_name = _('Repair Packet')
        verbose_name_plural = _('Repair Packets')

    def __str__(self) -> str:
        """Return the packet reference, or a pk-based placeholder."""
        return self.reference or f'RepairPacket #{self.pk}'

    def save(self, *args, **kwargs):
        """Persist and lazily assign a human-readable reference from the pk.

        The reference is derived from the (unique) primary key, so it is
        inherently collision-free; the follow-up ``update`` only runs once, on
        first insert, and never races because each row owns its own pk.
        """
        super().save(*args, **kwargs)
        if not self.reference:
            ref = f'RP-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(reference=ref)
            self.reference = ref

    @property
    def is_terminal(self) -> bool:
        """Whether the packet is in a terminal (closed/canceled) state."""
        return self.status in TERMINAL_PACKET_STATUSES

    def unsatisfied_blocking_gates(self) -> list[tuple[object, str]]:
        """Return blocking gates that are not fully satisfied.

        A gate is satisfied only when it is confirmed with all required proof /
        verification, or waived with the required waiver metadata. This method is
        intentionally conservative: ambiguous gate state blocks progression.
        """
        bad: list[tuple[object, str]] = []
        for gate in self.gates.filter(is_blocking=True).order_by(
            'sequence', 'created_at'
        ):
            reason = gate.unsatisfied_reason()
            if reason:
                bad.append((gate, reason))
        return bad

    def can_advance(self) -> tuple[bool, str]:
        """Guard lifecycle transitions: block on unsatisfied safety gates."""
        bad = self.unsatisfied_blocking_gates()
        if bad:
            gate, reason = bad[0]
            extra = len(bad) - 1
            suffix = f' (+{extra} more)' if extra else ''
            return False, f'Safety gate "{gate.name}" not satisfied: {reason}{suffix}'
        return True, ''

    def can_return_to_service(self) -> tuple[bool, str]:
        """Guard closeout / return-to-service while lockout points are active."""
        active = LockoutPoint.objects.filter(
            gate__packet=self, gate__gate_type='loto'
        ).exclude(status=LockoutPoint.PointStatus.RESTORED)
        if active.exists():
            point = active.first()
            return False, f'Lockout point "{point.isolation_device}" is not restored'
        return True, ''


class RepairPacketGate(models.Model):
    """A safety gate (LOTO/permit/PPE/...) that must be confirmed before execution.

    Minimal implementation for the spine; the full Safety Gates feature (#2)
    extends this with a template library and approval routing.
    """

    GATE_TYPE_CHOICES = [
        ('loto', _('Lockout/Tagout')),
        ('permit', _('Permit')),
        ('ppe', _('PPE')),
        ('isolation', _('Isolation')),
        ('hot_work', _('Hot Work')),
        ('other', _('Other')),
    ]

    packet = models.ForeignKey(
        RepairPacket, on_delete=models.CASCADE, related_name='gates'
    )
    template = models.ForeignKey(
        'repair.SafetyGateTemplate',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='instances',
    )
    sequence = models.PositiveSmallIntegerField(default=0)
    is_blocking = models.BooleanField(default=True)
    is_mandatory = models.BooleanField(default=True)
    name = models.CharField(max_length=255)
    gate_type = models.CharField(
        max_length=16, choices=GATE_TYPE_CHOICES, default='other'
    )
    status = models.CharField(
        max_length=12,
        choices=GateStatus.choices,
        default=GateStatus.PENDING,
        db_index=True,
    )
    requires_photo = models.BooleanField(default=False)
    required_permission = models.CharField(max_length=64, blank=True)
    requires_second_person = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    waived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    waived_at = models.DateTimeField(null=True, blank=True)
    waiver_reason = models.TextField(blank=True)
    waiver_authority = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Model metadata."""

        ordering = ['created_at']

    def __str__(self) -> str:
        """Return the gate name and its current status."""
        return f'{self.name} ({self.status})'

    @property
    def is_satisfied(self) -> bool:
        """Whether this gate is satisfied for transition-blocking purposes."""
        return self.unsatisfied_reason() == ''

    def has_required_photo(self) -> bool:
        """Whether the gate has a photo proof when one is required."""
        if not self.requires_photo:
            return True
        return self.proofs.filter(
            proof_type=SafetyEvidenceProof.ProofType.PHOTO
        ).exists()

    def unsatisfied_reason(self) -> str:
        """Explain why this gate is not satisfied, or return an empty string."""
        if not self.is_blocking:
            return ''

        if self.status == GateStatus.PENDING:
            return 'pending'

        if self.status == GateStatus.WAIVED:
            if not self.waiver_reason:
                return 'waiver reason missing'
            if not self.waiver_authority:
                return 'waiver authority missing'
            return ''

        if self.status == GateStatus.CONFIRMED:
            if not self.has_required_photo():
                return 'required photo proof missing'
            if self.requires_second_person and not self.verified_by_id:
                return 'second-person verification missing'
            if self.gate_type == 'loto':
                outstanding = self.lockout_points.exclude(
                    status=LockoutPoint.PointStatus.VERIFIED
                ).count()
                if outstanding:
                    return f'{outstanding} lockout point(s) not verified'
            return ''

        return 'unknown state'

    def confirm(self, user=None, note: str = '') -> None:
        """Confirm this gate, recording who confirmed it and when."""
        self.status = GateStatus.CONFIRMED
        self.confirmed_by = user if (user and user.is_authenticated) else None
        self.confirmed_at = timezone.now()
        if note:
            self.note = note
        self.save(update_fields=['status', 'confirmed_by', 'confirmed_at', 'note'])

    def verify(self, user=None, note: str = '') -> None:
        """Record independent verification for gates that require it."""
        self.verified_by = user if (user and user.is_authenticated) else None
        self.verified_at = timezone.now()
        if note:
            self.note = note
        self.save(update_fields=['verified_by', 'verified_at', 'note'])

    def waive(self, user=None, reason: str = '', authority: str = '') -> None:
        """Waive this gate with explicit reason and authority metadata."""
        self.status = GateStatus.WAIVED
        self.waived_by = user if (user and user.is_authenticated) else None
        self.waived_at = timezone.now()
        self.waiver_reason = reason
        self.waiver_authority = authority
        self.save(
            update_fields=[
                'status',
                'waived_by',
                'waived_at',
                'waiver_reason',
                'waiver_authority',
            ]
        )


class RepairPacketEvidence(models.Model):
    """A piece of evidence (photo/reading/document) attached to a packet."""

    KIND_CHOICES = [
        ('photo', _('Photo')),
        ('reading', _('Reading')),
        ('doc', _('Document')),
    ]

    packet = models.ForeignKey(
        RepairPacket, on_delete=models.CASCADE, related_name='evidence'
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default='reading')
    label = models.CharField(max_length=255, blank=True)
    value = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Model metadata."""

        ordering = ['created_at']

    def __str__(self) -> str:
        """Return the checklist item kind and label."""
        return f'{self.kind}: {self.label}'


class SafetyGateTemplate(models.Model):
    """Reusable safety gate rule that can be resolved onto repair packets."""

    name = models.CharField(max_length=255)
    gate_type = models.CharField(
        max_length=16, choices=RepairPacketGate.GATE_TYPE_CHOICES, default='other'
    )
    instructions = models.TextField(blank=True)
    applies_to = models.JSONField(default=dict, blank=True)
    required_permission = models.CharField(max_length=64, blank=True)
    requires_photo = models.BooleanField(default=False)
    requires_second_person = models.BooleanField(default=False)
    is_blocking = models.BooleanField(default=True)
    is_mandatory = models.BooleanField(default=True)
    risk_tier = models.SmallIntegerField(default=2)
    default_sequence = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['default_sequence', 'name']

    def __str__(self) -> str:
        """Return the template name."""
        return self.name


class LockoutPoint(models.Model):
    """Physical energy-control point associated with a LOTO gate."""

    class EnergySource(models.TextChoices):
        """Hazardous energy source categories for a lockout point."""

        ELECTRICAL = 'electrical', _('Electrical')
        HYDRAULIC = 'hydraulic', _('Hydraulic')
        PNEUMATIC = 'pneumatic', _('Pneumatic')
        MECHANICAL = 'mechanical', _('Mechanical')
        THERMAL = 'thermal', _('Thermal')
        CHEMICAL = 'chemical', _('Chemical')
        GRAVITY = 'gravity', _('Gravity')
        OTHER = 'other', _('Other')

    class PointStatus(models.TextChoices):
        """LOTO progression states of a lockout point."""

        IDENTIFIED = 'identified', _('Identified')
        ISOLATED = 'isolated', _('Isolated')
        LOCKED = 'locked', _('Locked')
        VERIFIED = 'verified', _('Verified')
        RESTORED = 'restored', _('Restored')

    gate = models.ForeignKey(
        RepairPacketGate, on_delete=models.CASCADE, related_name='lockout_points'
    )
    energy_source = models.CharField(
        max_length=16, choices=EnergySource.choices, default=EnergySource.OTHER
    )
    isolation_device = models.CharField(max_length=255)
    lock_id = models.CharField(max_length=64, blank=True)
    tag_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16, choices=PointStatus.choices, default=PointStatus.IDENTIFIED
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['created_at']

    def __str__(self) -> str:
        """Return the energy source, isolation device and status."""
        return f'{self.energy_source}: {self.isolation_device} ({self.status})'


class SafetyEvidenceProof(models.Model):
    """Structured proof that a safety gate action happened in the field."""

    class ProofType(models.TextChoices):
        """Kinds of field evidence that can back a safety gate."""

        PHOTO = 'photo', _('Photo')
        SCAN = 'scan', _('Scan')
        READING = 'reading', _('Reading')
        GEOFENCE = 'geofence', _('Geofence')
        SIGNATURE = 'signature', _('Signature')

    gate = models.ForeignKey(
        RepairPacketGate, on_delete=models.CASCADE, related_name='proofs'
    )
    lockout_point = models.ForeignKey(
        LockoutPoint,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='proofs',
    )
    proof_type = models.CharField(max_length=24, choices=ProofType.choices)
    value = models.JSONField(default=dict, blank=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Model metadata."""

        ordering = ['captured_at']

    def __str__(self) -> str:
        """Return the proof type and its parent gate id."""
        return f'{self.proof_type} proof for gate {self.gate_id}'


class RepairPacketApprovalLink(models.Model):
    """Associates approvals with a packet without a hard FK from the approvals app."""

    PURPOSE_CHOICES = [
        ('spend', _('Spend')),
        ('rfq', _('RFQ')),
        ('po', _('Purchase Order')),
        ('safety', _('Safety')),
    ]

    packet = models.ForeignKey(
        RepairPacket, on_delete=models.CASCADE, related_name='approval_links'
    )
    approval = models.ForeignKey(
        'approvals.Approval',
        on_delete=models.CASCADE,
        related_name='repair_packet_links',
    )
    purpose = models.CharField(max_length=32, choices=PURPOSE_CHOICES, default='spend')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Model metadata."""

        ordering = ['created_at']

    def __str__(self) -> str:
        """Return the linked packet and the approval purpose."""
        return f'{self.packet} <- {self.purpose}'


class RepairPacketEvent(models.Model):
    """Immutable audit record of a lifecycle change on a packet.

    Mirrors the ``approvals.ApprovalEvent`` pattern so the packet has a first-class,
    queryable history (surfaced later as an audit timeline in the UI).
    """

    class EventType(models.TextChoices):
        """Lifecycle event categories recorded for a packet."""

        CREATED = 'created', _('Created')
        GENERATED = 'generated', _('Generated')
        GENERATION_FAILED = 'generation_failed', _('Generation Failed')
        ADVANCED = 'advanced', _('Advanced')
        CANCELED = 'canceled', _('Canceled')
        GATES_RESOLVED = 'gates_resolved', _('Gates Resolved')
        GATE_CONFIRMED = 'gate_confirmed', _('Gate Confirmed')
        GATE_VERIFIED = 'gate_verified', _('Gate Verified')
        GATE_WAIVED = 'gate_waived', _('Gate Waived')
        LOCKOUT_UPDATED = 'lockout_updated', _('Lockout Updated')
        RETURN_TO_SERVICE = 'return_to_service', _('Return To Service')
        WORK_ORDER_CREATED = 'work_order_created', _('Work Order Created')
        WORK_ORDER_SKIPPED = 'work_order_skipped', _('Work Order Skipped')

    packet = models.ForeignKey(
        RepairPacket, on_delete=models.CASCADE, related_name='events'
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Model metadata."""

        ordering = ['-created_at']

    def __str__(self) -> str:
        """Return the packet id, event type and status transition."""
        return (
            f'{self.packet_id}: {self.event_type} {self.from_status}->{self.to_status}'
        )


class RepairPacketGenerationRun(models.Model):
    """Provenance + idempotency ledger for AI generation attempts.

    Reusing ``agent_run_id`` as a unique key makes generation replay-safe: a retry
    with the same run id is recognised instead of duplicating diagnosis/parts, and
    the same id flows into ``approvals`` for its idempotency key.
    """

    class RunStatus(models.TextChoices):
        """Terminal and in-flight states of a generation run."""

        RUNNING = 'running', _('Running')
        SUCCEEDED = 'succeeded', _('Succeeded')
        FAILED = 'failed', _('Failed')

    packet = models.ForeignKey(
        RepairPacket, on_delete=models.CASCADE, related_name='generation_runs'
    )
    agent_run_id = models.CharField(max_length=64, unique=True, db_index=True)
    provider = models.CharField(max_length=32, blank=True)
    status = models.CharField(
        max_length=12, choices=RunStatus.choices, default=RunStatus.RUNNING
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    result_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        """Model metadata."""

        ordering = ['-started_at']

    def __str__(self) -> str:
        """Return the agent run id and run status."""
        return f'{self.agent_run_id} ({self.status})'


# Re-exported for callers that persist the current schema version.
__all__ = [
    'CRITICALITY_CHOICES',
    'DIAGNOSIS_SCHEMA_VERSION',
    'TERMINAL_PACKET_STATUSES',
    'VALID_TRANSITIONS',
    'GateStatus',
    'GenerationStatus',
    'LockoutPoint',
    'PacketStatus',
    'RepairPacket',
    'RepairPacketApprovalLink',
    'RepairPacketEvent',
    'RepairPacketEvidence',
    'RepairPacketGate',
    'RepairPacketGenerationRun',
    'SafetyEvidenceProof',
    'SafetyGateTemplate',
    'is_valid_packet_transition',
]

# Risk Radar / Command Center models live in their own module; importing it
# here registers them with Django's app registry (model discovery).
from . import risk_models  # noqa: F401
